"""Keep the projects table consistent with the Docker runtime."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.db.models.project import Project
from src.services.deployment.docker_adapter import DockerDeploymentAdapter
from src.services.deployments import create_deployment_for_project

logger = logging.getLogger(__name__)


def reconcile_live_projects(
    db: Session, *, adapter: DockerDeploymentAdapter | None = None
) -> list[str]:
    """Queue a normal self-healing deploy when a live project's app container vanished."""
    adapter = adapter or DockerDeploymentAdapter()
    repaired: list[str] = []
    for project in db.query(Project).filter(Project.status == "live").all():
        project_id = str(project.id)
        try:
            status = adapter.app_container_status(project_id)
        except Exception:  # noqa: BLE001 - one broken Docker object must not stop the sweep
            logger.exception("Runtime reconciliation could not inspect project %s", project_id)
            continue
        # Docker can briefly report these during daemon/host startup. They are not proof that
        # the app vanished, and racing them with a rebuild would create avoidable downtime.
        if status in {"running", "created", "restarting"}:
            continue

        note = (
            "Автовосстановление: live-контейнер отсутствует"
            if status is None
            else f"Автовосстановление: live-контейнер имеет статус {status}"
        )
        project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
        db.add(project)
        db.commit()
        try:
            deployment = create_deployment_for_project(db, project, skip_auto_check=True)
        except Exception:  # noqa: BLE001 - retain an actionable state; retry next sweep
            db.rollback()
            refreshed = db.get(Project, project.id)
            if refreshed and refreshed.status in {"live", "deploying"}:
                refreshed.status = "ready"
                db.add(refreshed)
                db.commit()
            logger.exception("Runtime reconciliation failed to queue project %s", project_id)
            continue
        if deployment.status == "queued":
            repaired.append(project_id)
            logger.warning(
                "Runtime reconciliation queued deployment %s for project %s (container=%s)",
                deployment.id,
                project_id,
                status,
            )
    return repaired
