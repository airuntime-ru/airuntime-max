from uuid import UUID

from sqlalchemy.orm import Session

from src.api.dto.project_logs import ProjectLogsResponse
from src.db.models.deployment import Deployment
from src.db.models.project import Project
from src.services.docker_control_queue import submit_control_job

MAX_LOG_CHARS = 80_000


def _tail_text(value: str, max_chars: int = MAX_LOG_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _read_runtime_logs(project_id: UUID, logs_ref: str | None) -> tuple[str, str | None]:
    if not logs_ref:
        return "", None
    if not logs_ref.startswith("docker://"):
        # Legacy rows stored truncated failure text here - surface it as deploy error, not runtime.
        return "", None

    container_id = logs_ref.removeprefix("docker://")
    result = submit_control_job(
        action="logs", project_id=str(project_id), extra={"container_id": container_id, "tail": 400}
    )
    if result is None:
        return "", "Runtime logs are unavailable: deployment service did not respond."
    if not result.get("ok"):
        return "", f"Runtime logs are unavailable: {result.get('error', 'unknown error')}"
    return _tail_text(result.get("logs", "")), None


def _latest_deployment(db: Session, project_id: UUID) -> Deployment | None:
    return (
        db.query(Deployment)
        .filter(Deployment.project_id == project_id)
        .order_by(
            Deployment.started_at.desc().nullslast(),
            Deployment.finished_at.desc().nullslast(),
        )
        .first()
    )


def read_project_logs(db: Session, project: Project) -> ProjectLogsResponse:
    deployment = _latest_deployment(db, project.id)
    runtime_logs = ""
    runtime_error = None
    deployment_logs = ""

    if deployment:
        lines = [
            f"Deployment: {deployment.id}",
            f"Status: {deployment.status}",
            f"Image: {deployment.image_ref or '-'}",
            f"Container: {deployment.container_id or '-'}",
            f"Started: {deployment.started_at or '-'}",
            f"Finished: {deployment.finished_at or '-'}",
        ]
        if deployment.log_text:
            lines.append("")
            lines.append("--- Лог сборки / деплоя ---")
            lines.append(deployment.log_text)
        if deployment.error_text:
            lines.append("")
            lines.append("--- Ошибка деплоя / сборки ---")
            lines.append(deployment.error_text)
        elif (
            not deployment.log_text
            and deployment.logs_ref
            and not deployment.logs_ref.startswith("docker://")
        ):
            lines.append("")
            lines.append("--- Ошибка деплоя ---")
            lines.append(deployment.logs_ref)
        deployment_logs = "\n".join(lines)
        runtime_logs, runtime_error = _read_runtime_logs(project.id, deployment.logs_ref)

    return ProjectLogsResponse(
        project_logs=_tail_text(project.logs or ""),
        deployment_logs=deployment_logs,
        runtime_logs=runtime_logs,
        runtime_error=runtime_error,
        deployment_status=deployment.status if deployment else None,
        container_id=deployment.container_id if deployment else None,
        logs_ref=deployment.logs_ref if deployment else None,
    )
