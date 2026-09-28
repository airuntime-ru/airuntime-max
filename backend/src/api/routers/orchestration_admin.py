"""Admin/debug view over the orchestration engine's aggregate health (spec section 20).

Separate from the project-scoped orchestration router because these numbers are global, not
per-project: "is the engine working" is a fleet-level question. Admin-only - the aggregates say
nothing secret (services/orchestration/metrics.py returns only counts/rates/durations, never
model output, secret values, or workspace contents), but they do describe other users' activity,
so they are not for ordinary project owners.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.db.models.user import User
from src.db.session import get_db
from src.services.orchestration.metrics import collect_metrics

router = APIRouter(prefix="/admin/orchestration", tags=["orchestration-admin"])


def _require_admin(current_user: User) -> None:
    if current_user.role != "admin":
        # 404, not 403: an ordinary user should not be able to discover that this surface exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("/metrics")
def orchestration_metrics(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    project_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(current_user)
    return collect_metrics(db, window_hours=window_hours, project_id=project_id).to_dict()
