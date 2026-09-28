"""End-to-end cancellation (spec section 17) - the single biggest gap the pre-existing system
had (confirmed by the initial audit: no cancel endpoint anywhere, no propagation to the Codex
container, which can otherwise run un-killable for up to codex_turn_timeout_seconds=1800s
burning real API cost after a user closes the tab).

`CancellationToken` is a plain asyncio.Event wrapper threaded through executors.py /
codex_runtime.py / loop.py. Setting it does two independent things depending on what's running:
  - Codex (queued path): codex_runtime.py's `_stream_events` polling loop checks it once per
    cycle and, on a hit, actively calls `cancel_codex_run` below (a real `docker stop` via the
    worker - see docker_control_actions.py) before returning, instead of merely abandoning the
    consumer the way a plain HTTP disconnect always silently did before.
  - HTTP tool-calling path (loop.py): checked between iterations of the main loop; an
    already-dispatched tool call (e.g. a build_project RPC in flight) is allowed to finish
    server-side (its result is simply discarded) rather than force-killed - see this module's
    own docstring in loop.py's diff for why a deeper fix there is out of scope here.

Backend never gets a Docker socket either way - `cancel_codex_run` is a Redis-RPC control
action like every other Docker-touching operation in this codebase (docker_control_queue.py).
"""

from __future__ import annotations

import asyncio
import logging

from src.services.docker_control_queue import submit_control_job

logger = logging.getLogger(__name__)


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    def cancel(self, reason: str = "Cancelled by user") -> None:
        self._reason = reason
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait(self) -> None:
        await self._event.wait()


async def cancel_codex_run(
    *, project_id: str, correlation_id: str, timeout_seconds: int = 10
) -> bool:
    """Best-effort real container stop - returns False (not an exception) if the worker is
    unreachable or the container was already gone, since by the time this fires the run may
    already have finished naturally. Never raises; a failure here must not prevent the rest of
    a cancellation flow (releasing the lease, marking the run cancelled) from proceeding."""
    result = await asyncio.to_thread(
        submit_control_job,
        action="cancel_codex_run",
        project_id=project_id,
        extra={"correlation_id": correlation_id},
        timeout_seconds=timeout_seconds,
    )
    if result is None:
        logger.warning(
            "cancel_codex_run: RPC timed out or worker unreachable for %s", correlation_id
        )
        return False
    return bool(result.get("ok"))
