"""Run an async coroutine from either sync or async call sites.

The coding agent loop is async (it streams), but the deployment worker and
the Docker build/repair path are plain synchronous functions that may or may
not already be running inside an event loop (inline dev mode runs them
straight from an async FastAPI request; the standalone worker process does
not). This picks the right strategy either way.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[object, object, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()
