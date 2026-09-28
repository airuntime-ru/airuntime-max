from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.deployment import Deployment
from src.db.models.project import Project
from src.db.models.user import User
from src.services.billing import concurrent_project_limit
from src.services.docker_control_queue import submit_control_job


class RunningProjectLimitError(RuntimeError):
    def __init__(self, running: int, limit: int) -> None:
        self.running = running
        self.limit = limit
        super().__init__(
            f"Достигнут лимит одновременно запущенных проектов ({limit}). "
            "Остановите один из активных проектов, чтобы запустить новый."
        )


RUNNING_STATUSES = frozenset({"live", "deploying"})


def count_running_projects(
    db: Session, user_id: UUID, *, exclude_project_id: UUID | None = None
) -> int:
    query = db.query(Project).filter(
        Project.user_id == user_id,
        Project.status.in_(tuple(RUNNING_STATUSES)),
    )
    if exclude_project_id is not None:
        query = query.filter(Project.id != exclude_project_id)
    return query.count()


def get_running_limit(db: Session, user_id: UUID) -> int:
    user = db.get(User, user_id)
    if not user:
        return settings.max_running_projects_per_user
    return concurrent_project_limit(db, user, fallback=settings.max_running_projects_per_user)


def assert_can_start_project(
    db: Session, user_id: UUID, *, exclude_project_id: UUID | None = None
) -> None:
    limit = get_running_limit(db, user_id)
    running = count_running_projects(db, user_id, exclude_project_id=exclude_project_id)
    if running >= limit:
        raise RunningProjectLimitError(running=running, limit=limit)


def _cancel_active_deployments(db: Session, project_id: UUID) -> None:
    active = (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project_id,
            Deployment.status.in_(("queued", "running")),
        )
        .all()
    )
    now = datetime.now(UTC)
    for deployment in active:
        deployment.status = "cancelled"
        deployment.finished_at = now
        db.add(deployment)


def cancel_active_deployments(db: Session, project_id: UUID) -> None:
    """Cancel queued/running deployments for a project (e.g. before starting a new one)."""
    _cancel_active_deployments(db, project_id)


def stop_project_runtime(db: Session, project: Project) -> Project:
    if project.status not in RUNNING_STATUSES:
        raise ValueError("Проект не запущен")

    result = submit_control_job(action="stop", project_id=str(project.id))
    if result is None:
        raise ValueError(
            "Не удалось остановить проект: сервис деплоя не отвечает, попробуйте ещё раз"
        )
    if not result.get("ok"):
        raise ValueError(
            f"Не удалось остановить проект: {result.get('error', 'неизвестная ошибка')}"
        )
    _cancel_active_deployments(db, project.id)
    project.status = "stopped"
    note = "Проект остановлен пользователем."
    project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def block_project(db: Session, project: Project, *, reason: str) -> Project:
    submit_control_job(action="stop", project_id=str(project.id))
    _cancel_active_deployments(db, project.id)
    project.status = "blocked"
    project.blocked_reason = reason
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def unblock_project(db: Session, project: Project) -> Project:
    project.status = "ready"
    project.blocked_reason = None
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def start_project_runtime(db: Session, project: Project) -> Project:
    if project.status in RUNNING_STATUSES:
        raise ValueError("Проект уже запущен или запускается")

    # Import here to avoid circular imports.
    from src.services.deployments import create_deployment_for_project

    previous_status = project.status
    assert_can_start_project(db, project.user_id, exclude_project_id=project.id)
    project.status = "deploying"
    db.add(project)
    db.commit()
    db.refresh(project)
    try:
        create_deployment_for_project(db, project)
    except Exception:
        # Roll back the optimistic "deploying" if enqueue/create failed (e.g. limit race).
        db.refresh(project)
        if project.status == "deploying":
            project.status = previous_status
            db.add(project)
            db.commit()
            db.refresh(project)
        raise
    db.refresh(project)
    return project
