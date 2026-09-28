from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.db.models.deployment import Deployment
from src.db.models.project import Project
from src.services.deployment_queue import enqueue_deployment
from src.services.project_runtime import assert_can_start_project, cancel_active_deployments
from src.services.prompt_guard import redact_secrets

# Must fit Deployment.logs_ref String(512). Full errors live on Deployment.error_text.
_LOGS_REF_MAX = 500
_ERROR_TEXT_MAX = 50_000
_LOG_TEXT_MAX = 200_000


def create_deployment_for_project(
    db: Session, project: Project, *, skip_auto_check: bool = False
) -> Deployment:
    assert_can_start_project(db, project.user_id, exclude_project_id=project.id)
    # One active build per project - otherwise users end up with multiple stuck "running" rows.
    cancel_active_deployments(db, project.id)
    # Always mark deploying (including redeploy from live) so the UI never shows "live"
    # while a new build is in flight.
    project.status = "deploying"
    db.add(project)
    db.commit()
    db.refresh(project)

    now = datetime.now(UTC)
    deployment = Deployment(
        project_id=project.id,
        status="queued",
        image_ref=None,
        container_id=None,
        logs_ref=None,
        error_text=None,
        log_text="В очереди на запуск…\n",
        # Used as queue timestamp so the worker can reap stuck queued jobs.
        started_at=now,
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    job = {
        "deployment_id": str(deployment.id),
        "project_id": str(project.id),
        "image_ref": None,
        "skip_auto_check": skip_auto_check,
    }
    queued = enqueue_deployment(
        deployment_id=job["deployment_id"],
        project_id=job["project_id"],
        image_ref=None,
        skip_auto_check=skip_auto_check,
    )
    if not queued:
        # Never run Docker from the API process. Inline process_job() used to freeze the
        # asyncio loop (and every chat/login request) for the length of a docker build.
        deployment.status = "failed"
        store_deployment_error(
            deployment,
            "Не удалось поставить задачу в очередь запуска. Повторите из вкладки «Деплои».",
        )
        deployment.finished_at = datetime.now(UTC)
        # No worker can recover a job that never reached Redis. Keep the project restartable
        # instead of stranding it forever in the optimistic "deploying" state.
        project.status = "ready"
        db.add(project)
        db.add(deployment)
        db.commit()
        db.refresh(deployment)
    return deployment


def truncate_logs_ref(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= _LOGS_REF_MAX:
        return cleaned
    return cleaned[: _LOGS_REF_MAX - 3] + "..."


def store_deployment_error(deployment: Deployment, text: str) -> None:
    """Persist the full failure for repair/UI; keep a short hint on logs_ref for older clients."""
    cleaned = redact_secrets((text or "").strip())
    if len(cleaned) > _ERROR_TEXT_MAX:
        cleaned = cleaned[-_ERROR_TEXT_MAX:]
    deployment.error_text = cleaned or None
    # Keep the failure in the expandable live log too.
    if cleaned:
        append_deployment_log(deployment, f"\n--- Ошибка ---\n{cleaned}\n", commit=False)
    # Do not overwrite a live docker:// pointer with truncated prose.
    if not (deployment.logs_ref or "").startswith("docker://"):
        deployment.logs_ref = truncate_logs_ref(cleaned) if cleaned else None


def append_deployment_log(deployment: Deployment, chunk: str, *, commit: bool = False) -> None:
    """Append to the live build/deploy log shown in the expandable deployments UI.

    Redacted before storage, not just at read time: a crashed container's stdout or a verbose
    Docker build step can echo real secret values now that build_project_image() actually wires
    every requested secret (not just TELEGRAM_BOT_TOKEN) into the container's environment - this
    is the one place all such log text converges before reaching the deployments UI/DB.
    """
    if not chunk:
        return
    existing = deployment.log_text or ""
    combined = existing + redact_secrets(chunk)
    if len(combined) > _LOG_TEXT_MAX:
        combined = combined[-_LOG_TEXT_MAX:]
    deployment.log_text = combined
