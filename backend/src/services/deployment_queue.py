import json
from datetime import UTC, datetime

from redis import Redis
from redis.exceptions import RedisError

from src.core.config import settings

QUEUE_KEY = "deployment:jobs"


def _redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=15,
    )


def enqueue_deployment(
    *,
    deployment_id: str,
    project_id: str,
    image_ref: str | None = None,
    skip_auto_check: bool = False,
) -> bool:
    job = {
        "deployment_id": deployment_id,
        "project_id": project_id,
        "image_ref": image_ref,
        "skip_auto_check": skip_auto_check,
        "queued_at": datetime.now(UTC).isoformat(),
    }
    try:
        _redis().rpush(QUEUE_KEY, json.dumps(job))
    except RedisError:
        return False
    return True


def pop_deployment_job(timeout_seconds: int = 5) -> dict | None:
    try:
        result = _redis().blpop(QUEUE_KEY, timeout=timeout_seconds)
    except RedisError:
        return None
    if not result:
        return None
    _, payload = result
    return json.loads(payload)
