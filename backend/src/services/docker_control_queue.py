"""Synchronous-feeling RPC over Redis so the (internet-facing, agent-code-adjacent) backend
process never needs direct Docker socket access - only the worker container has that. The
backend pushes a job and blocks on a per-job result key; the worker pops the job, does the
real docker.from_env() call, and pushes the result back.

When code already runs inside the deployment worker, submit_control_job executes the action
inline instead of queueing - a single worker cannot wait on itself.
"""

from __future__ import annotations

import contextvars
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from redis import Redis
from redis.exceptions import RedisError

from src.core.config import settings

QUEUE_KEY = "docker_control:jobs"
RESULT_PREFIX = "docker_control:result:"

# True while the deployment worker is handling a job (deploy or control). Nested
# submit_control_job calls must not Redis-round-trip to this same process.
_worker_inline_docker: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "worker_inline_docker", default=False
)


def _redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=15,
    )


@contextmanager
def worker_inline_docker() -> Iterator[None]:
    """Mark the current context as the Docker-capable worker (inline control actions)."""
    token = _worker_inline_docker.set(True)
    try:
        yield
    finally:
        _worker_inline_docker.reset(token)


def in_worker_inline_docker() -> bool:
    return bool(_worker_inline_docker.get())


def submit_control_job(
    *, action: str, project_id: str, timeout_seconds: int = 20, extra: dict | None = None
) -> dict | None:
    """Push a control job and block for the worker's result. Returns None if Redis/the worker
    is unreachable or the job timed out - callers should treat that as "couldn't confirm",
    not as a hard failure, since Docker itself may just be briefly unavailable.

    Inside the worker process, runs the action inline so repair/build_project cannot deadlock.
    """
    if _worker_inline_docker.get():
        from src.services.docker_control_actions import run_control_action

        return run_control_action(action=action, project_id=project_id, extra=extra)

    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "action": action, "project_id": project_id, **(extra or {})}
    result_key = f"{RESULT_PREFIX}{job_id}"
    try:
        r = _redis()
        r.rpush(QUEUE_KEY, json.dumps(job))
        popped = r.blpop(result_key, timeout=timeout_seconds)
    except RedisError:
        return None
    if not popped:
        return None
    _, payload = popped
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def pop_control_job(timeout_seconds: int = 2) -> dict | None:
    try:
        result = _redis().blpop(QUEUE_KEY, timeout=timeout_seconds)
    except RedisError:
        return None
    if not result:
        return None
    _, payload = result
    return json.loads(payload)


def push_control_result(job_id: str, result: dict) -> None:
    result_key = f"{RESULT_PREFIX}{job_id}"
    try:
        r = _redis()
        r.rpush(result_key, json.dumps(result))
        r.expire(result_key, 60)
    except RedisError:
        pass
