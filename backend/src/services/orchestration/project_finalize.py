"""Post-run project finalization for background orchestration (no chat SSE)."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.project import Project
from src.services.agentic_artifacts import ensure_dockerfile, workspace_has_agent_code
from src.services.artifacts import _telegram_token
from src.services.deployments import create_deployment_for_project
from src.services.project_intent import reconcile_type_with_workspace
from src.services.project_runtime import RunningProjectLimitError
from src.services.secrets import ensure_secret_placeholder
from src.services.workspace import project_dir

logger = logging.getLogger(__name__)


def _is_deployable(project: Project, artifact_path: Path) -> bool:
    return (artifact_path / "Dockerfile").exists() or workspace_has_agent_code(
        artifact_path, project
    )


def finalize_project_after_completed_run(
    db: Session, *, run: OrchestrationRun, project: Project
) -> None:
    """Mirror chat.py's run_completed branch when the engine finishes outside chat SSE."""
    if run.status != "completed":
        return

    artifact_path = project_dir(project.id)
    if not _is_deployable(project, artifact_path):
        logger.info(
            "orchestration finalize: project %s has no deployable artifacts yet", project.id
        )
        if project.status == "created":
            project.status = "ready"
            db.add(project)
        return

    try:
        ensure_dockerfile(project, artifact_path)
    except Exception:  # noqa: BLE001 - deploy queue should still be attempted
        logger.warning("ensure_dockerfile failed for project %s", project.id, exc_info=True)

    if reconcile_type_with_workspace(
        project, artifact_path, has_bot_secret=bool(_telegram_token(db, project))
    ):
        db.add(project)

    has_website = project.type in ("website", "mixed")
    has_bot = project.type in ("telegram_bot", "mixed")

    if has_bot and not _telegram_token(db, project):
        ensure_secret_placeholder(
            db, project, "TELEGRAM_BOT_TOKEN", "Нужен для запуска Telegram-бота"
        )
        project.status = "needs_configuration"
        db.add(project)
        return

    project.status = "ready"
    db.add(project)
    db.flush()

    if not (has_website or has_bot):
        return

    try:
        create_deployment_for_project(db, project)
    except RunningProjectLimitError as exc:
        project.status = "ready"
        note = (
            f"{exc}\n"
            "Файлы проекта сохранены. Остановите один из запущенных проектов "
            "и запустите этот вручную на странице «Деплои»."
        )
        project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
        db.add(project)
    except Exception:  # noqa: BLE001 - run is already completed; don't fail the engine
        logger.exception(
            "orchestration finalize: failed to queue deploy for project %s", project.id
        )
