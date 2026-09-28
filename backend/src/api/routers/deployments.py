from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.deployment import DeploymentListResponse, DeploymentResponse
from src.db.models.deployment import Deployment
from src.db.models.project import Project
from src.db.models.user import User
from src.db.session import get_db
from src.services.deployments import create_deployment_for_project
from src.services.project_runtime import RunningProjectLimitError

router = APIRouter(prefix="/projects/{project_id}/deployments", tags=["deployments"])


@router.get("", response_model=DeploymentListResponse)
def list_deployments(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeploymentListResponse:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    base_query = db.query(Deployment).filter(Deployment.project_id == project_id)
    total = base_query.count()
    rows = base_query.order_by(Deployment.started_at.desc()).offset(offset).limit(limit).all()
    return DeploymentListResponse(
        items=[DeploymentResponse.model_validate(row) for row in rows], total=total
    )


@router.post("", response_model=DeploymentResponse)
def create_deployment(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Deployment:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return create_deployment_for_project(db, project)
    except RunningProjectLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
