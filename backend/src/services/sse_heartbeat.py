"""Keeps a long-silent SSE stream's TCP connection from looking dead to whatever sits between
the browser and this process.

Why this exists: a coding-agent turn can go tens of seconds to several minutes between visible
events - Codex "thinking" with no tool call yet, a slow `docker build`/`npm install` step, or the
orchestration engine's own planning call, which by design produces zero visible output while it
runs. None of that is a bug in the agent - but a chunked HTTP response that goes quiet for long
enough gets treated as dead by *something* on a real network path (a NAT box, a corporate/VPN proxy, even some OS-level
socket handling) and the browser sees a raw connection failure (Safari surfaces this as literally
"Load failed", Chrome as "Failed to fetch") with no HTTP status and no body to show the user -
worse than any error this app could format itself. Interleaving a harmless ping during silence
keeps bytes moving so that never happens.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import TypeVar

T = TypeVar("T")

# SSE comment line - ignored by EventSource/fetch-stream parsers (the frontend only looks at
# lines starting with "data: "), but real bytes on the wire.
SSE_PING = ": ping\n\n"

_DONE = object()


class Ticker:
    """For polling loops that already wake up periodically but only emit a real SSE frame on
    state change (deploy-status polling in chat.py) - call due() on every wake-up and emit
    SSE_PING when it returns True, so a long stretch with no state change still keeps the
    connection alive. (with_heartbeat above covers the same problem for an async generator being
    consumed as it produces events; this covers a plain `while` polling loop instead.)
    """

    def __init__(self, interval_seconds: float = 15.0) -> None:
        self._interval = interval_seconds
        self._last = time.monotonic()

    def due(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            return True
        return False


async def _safe_anext(iterator: AsyncIterator[T]) -> object:
    try:
        return await iterator.__anext__()
    except StopAsyncIteration:
        return _DONE


async def with_heartbeat(
    source: AsyncIterable[T], *, interval_seconds: float = 15.0, ping: str = SSE_PING
) -> AsyncIterator[T | str]:
    """Re-yield everything from `source`, plus `ping` whenever it hasn't produced anything for
    `interval_seconds`. A real exception from `source` propagates normally - only silence (not
    failure) gets a heartbeat; callers keep their existing try/except around the loop unchanged.
    """
    iterator = source.__aiter__()
    pending: asyncio.Task[object] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(_safe_anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=interval_seconds)
            if pending in done:
                result = pending.result()
                pending = None
                if result is _DONE:
                    return
                yield result  # type: ignore[misc]
            else:
                yield ping  # type: ignore[misc]
    finally:
        if pending is not None:
            pending.cancel()
