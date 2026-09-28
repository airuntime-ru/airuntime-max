from fastapi import Request

from src.core.config import settings
from src.core.rate_limit import hit_rate_limit

_PAYMENT_CALLBACK_SUFFIXES = (
    "/billing/robokassa/result",
    "/billing/robokassa/success",
    "/billing/robokassa/fail",
)


def enforce_rate_limit(request: Request) -> None:
    if settings.debug:
        return
    path = request.url.path
    if any(path.endswith(suffix) for suffix in _PAYMENT_CALLBACK_SUFFIXES):
        return
    client_host = request.client.host if request.client else "unknown"
    hit_rate_limit(client_host)
