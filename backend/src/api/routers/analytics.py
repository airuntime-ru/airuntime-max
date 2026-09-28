"""Product analytics batch ingest (public, lightly rate-limited)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.api.dto.analytics import AnalyticsBatchIn, AnalyticsBatchOut
from src.core.config import settings
from src.core.rate_limit import InMemoryRateLimiter
from src.db.session import get_db
from src.services.analytics.ingest import ingest_batch

router = APIRouter(prefix="/analytics", tags=["analytics"])

_analytics_limiter = InMemoryRateLimiter(max_requests=60, window_seconds=60)


def _verify_ingest_key(
    x_analytics_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    expected = (settings.analytics_ingest_key or "").strip()
    if not expected:
        return
    provided = (x_analytics_key or "").strip()
    if not provided and authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1].strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid analytics key"
        )


def _analytics_rate_limit(request: Request) -> None:
    if settings.debug:
        return
    client_host = request.client.host if request.client else "unknown"
    _analytics_limiter.hit(f"analytics:{client_host}")


@router.post(
    "/batch",
    response_model=AnalyticsBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_analytics_rate_limit)],
)
def ingest_analytics_batch(
    payload: AnalyticsBatchIn,
    _: None = Depends(_verify_ingest_key),
    db: Session = Depends(get_db),
) -> AnalyticsBatchOut:
    accepted = ingest_batch(db, payload)
    return AnalyticsBatchOut(accepted=accepted)
