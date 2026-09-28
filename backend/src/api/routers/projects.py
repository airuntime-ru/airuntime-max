import logging
import shutil
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.project import (
    ProjectCreateRequest,
    ProjectGenerationUsageResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from src.api.dto.project_logs import ProjectLogsResponse
from src.api.dto.project_runtime import ProjectRuntimeLimitsResponse
from src.db.models.chat import Chat
from src.db.models.chat_file import ChatFile
from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.project import Project
from src.db.models.secret import Secret
from src.db.models.user import User
from src.db.session import get_db
from src.services.billing import summarize_project_generation_usage, total_project_limit
from src.services.cloudflare_dns import delete_dns_for_website_deploy
from src.services.custom_domain import (
    CustomDomainError,
    domain_response,
    set_custom_domain,
    verify_custom_domain,
)
from src.services.deployment_check import check_and_repair_deployment
from src.services.docker_control_queue import submit_control_job
from src.services.orchestration import engine as orchestration_engine
from src.services.orchestration.repository import OrchestrationRunRepository
from src.services.orchestration.status import is_run_terminal
from src.services.project_intent import infer_project_type, reconcile_type_with_workspace
from src.services.project_logs import read_project_logs
from src.services.project_runtime import (
    RunningProjectLimitError,
    count_running_projects,
    get_running_limit,
    start_project_runtime,
    stop_project_runtime,
)
from src.services.project_subdomain import (
    assert_subdomain_available,
    normalize_deploy_subdomain,
    resolve_deploy_subdomain,
)
from src.services.secrets import TELEGRAM_BOT_TOKEN_KEY
from src.services.storage import storage_service
from src.services.system_settings import get_system_setting_number
from src.services.workspace import project_dir_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_response(project: Project) -> ProjectResponse:
    return ProjectResponse.from_project(project)


def _owned_project_or_404(db: Session, project_id: UUID, user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _effective_project_limit(db: Session, user: User) -> int | None:
    """How many projects this user may own in total, or None for no limit.

    The plan is the product-level source of truth (Epic A4). `max_projects_per_user` survives
    only as a global emergency ceiling an operator can drop without a deploy - it must not
    define the offer, and it no longer applies to users whose plan already sets a limit.
    """
    plan_limit = total_project_limit(db, user)
    global_ceiling = get_system_setting_number("max_projects_per_user")
    candidates = [value for value in (plan_limit, global_ceiling) if value is not None]
    return min(candidates) if candidates else None


@router.get("", response_model=ProjectListResponse)
def list_projects(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    base_query = db.query(Project).filter(Project.user_id == current_user.id)
    total = base_query.count()
    deployed_total = base_query.filter(Project.deployment_url.isnot(None)).count()
    rows = base_query.order_by(Project.created_at.desc()).offset(offset).limit(limit).all()
    return ProjectListResponse(
        items=[_to_response(project) for project in rows],
        total=total,
        deployed_total=deployed_total,
    )


@router.post("", response_model=ProjectResponse)
def create_project(
    payload: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    max_projects = _effective_project_limit(db, current_user)
    if max_projects is not None:
        existing_count = db.query(Project).filter(Project.user_id == current_user.id).count()
        if existing_count >= max_projects:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"На вашем тарифе доступно проектов: {max_projects}. "
                "Удалите неиспользуемый проект или смените тариф в профиле.",
            )

    project_type = payload.type or infer_project_type(f"{payload.name}\n{payload.description}")
    project = Project(
        user_id=current_user.id,
        type=project_type,
        name=payload.name,
        description=payload.description,
    )
    db.add(project)
    db.flush()
    db.add(Chat(project_id=project.id, title="Первый запуск"))
    db.commit()
    db.refresh(project)
    return _to_response(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.status is not None:
        project.status = payload.status
    if "deploy_subdomain" in payload.model_fields_set:
        # Not gated on project.type - harmless to set for a bot-only project (simply unused at
        # deploy time), and gating it here was a recurring source of confusing 400s whenever
        # classification lagged behind what the project actually contains.
        normalized = normalize_deploy_subdomain(payload.deploy_subdomain)
        if normalized:
            assert_subdomain_available(db, normalized, exclude_project_id=str(project.id))
        project.deploy_subdomain = normalized
    db.add(project)
    db.commit()
    db.refresh(project)
    return _to_response(project)


@router.get("/runtime-limits", response_model=ProjectRuntimeLimitsResponse)
def get_runtime_limits(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProjectRuntimeLimitsResponse:
    max_total = _effective_project_limit(db, current_user)
    return ProjectRuntimeLimitsResponse(
        running=count_running_projects(db, current_user.id),
        max_running=get_running_limit(db, current_user.id),
        total=db.query(Project).filter(Project.user_id == current_user.id).count(),
        max_total=max_total,
    )


@router.post("/{project_id}/stop", response_model=ProjectResponse)
def stop_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        project = stop_project_runtime(db, project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(project)


@router.post("/{project_id}/start", response_model=ProjectResponse)
def start_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        project = start_project_runtime(db, project)
    except RunningProjectLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(project)


@router.get("/{project_id}/logs", response_model=ProjectLogsResponse)
def get_project_logs(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectLogsResponse:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return read_project_logs(db, project)


@router.post("/{project_id}/check-deployment")
def check_project_deployment(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Inspect the latest completed or failed deployment, repair code if an error is found,
    and queue a redeploy. Works for crashed/failed launches as well as live containers."""
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return check_and_repair_deployment(db, project)


@router.delete("/{project_id}")
def delete_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Stop orchestration before touching runtime/files. Otherwise a task that finishes just
    # after the first cleanup can rebuild the image and recreate the workspace behind us.
    active_runs = [
        run
        for run in db.query(OrchestrationRun)
        .filter(OrchestrationRun.project_id == project.id)
        .all()
        if not is_run_terminal(run.status)
    ]
    run_repo = OrchestrationRunRepository(db)
    for run in active_runs:
        run_repo.request_cancel(run)
        token = orchestration_engine.get_cancellation_token(run.id)
        if token is not None:
            token.cancel("project deletion requested")
    if active_runs:
        db.commit()

    # A 200 response is a deletion guarantee, not "the database row disappeared while runtime
    # resources may have been orphaned". Keep the row on any cleanup failure so the operation is
    # safely retryable and operators still have the ownership metadata needed for recovery.
    cleanup_result = submit_control_job(action="cleanup", project_id=str(project.id))
    if not cleanup_result or not cleanup_result.get("ok"):
        logger.error(
            "Refusing to delete project %s because runtime cleanup was not confirmed: %r",
            project.id,
            cleanup_result,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось подтвердить очистку контейнеров и Docker-ресурсов. Повторите удаление.",
        )

    if active_runs:
        active_ids = [run.id for run in active_runs]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            db.expire_all()
            still_running = [
                run
                for run in db.query(OrchestrationRun)
                .filter(OrchestrationRun.id.in_(active_ids))
                .all()
                if not is_run_terminal(run.status)
            ]
            if not still_running:
                break
            time.sleep(0.1)
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Оркестратор ещё завершает активную задачу. Повторите удаление через несколько секунд.",
            )

        # The task may have produced a final image between cancellation and its terminal DB
        # transition. A second idempotent cleanup closes that race before files/rows disappear.
        cleanup_result = submit_control_job(action="cleanup", project_id=str(project.id))
        if not cleanup_result or not cleanup_result.get("ok"):
            logger.error(
                "Refusing to delete project %s because post-cancel cleanup was not confirmed: %r",
                project.id,
                cleanup_result,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось подтвердить финальную очистку Docker-ресурсов. Повторите удаление.",
            )

    object_keys = [
        row.object_key for row in db.query(ChatFile).filter(ChatFile.project_id == project.id).all()
    ]
    try:
        for object_key in object_keys:
            storage_service.delete_object(object_key)
    except Exception as storage_exc:  # noqa: BLE001 - preserve the DB row for a safe retry
        logger.exception("Object storage cleanup failed for project %s", project.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось удалить загруженные файлы проекта. Повторите удаление.",
        ) from storage_exc

    workspace_path = project_dir_path(project.id)
    try:
        shutil.rmtree(workspace_path)
    except FileNotFoundError:
        pass
    except OSError as workspace_exc:
        logger.exception("Workspace cleanup failed for project %s", project.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось удалить файлы проекта. Повторите удаление.",
        ) from workspace_exc
    if workspace_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Файлы проекта остались на диске после удаления. Повторите операцию.",
        )
    # DNS cleanup must run while deploy_subdomain / resolved host are still available.
    # Best-effort: a Cloudflare outage must not leave the project undeletable.
    if project.type in ("website", "mixed"):
        subdomain = resolve_deploy_subdomain(project)
        try:
            delete_dns_for_website_deploy(subdomain)
        except Exception as dns_exc:  # noqa: BLE001 - keep row so DNS cleanup remains retryable
            logger.exception(
                "Cloudflare DNS cleanup failed for project %s (subdomain=%s)",
                project.id,
                subdomain,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Не удалось удалить DNS-запись проекта. Повторите удаление.",
            ) from dns_exc
    db.delete(project)
    db.commit()
    return {"status": "deleted"}


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    has_bot_secret = (
        db.query(Secret.id)
        .filter(Secret.project_id == project.id, Secret.key == TELEGRAM_BOT_TOKEN_KEY)
        .first()
        is not None
    )
    if reconcile_type_with_workspace(project, has_bot_secret=has_bot_secret):
        db.add(project)
        db.commit()
        db.refresh(project)
    return _to_response(project)


@router.get("/{project_id}/generation-usage", response_model=ProjectGenerationUsageResponse)
def get_project_generation_usage(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectGenerationUsageResponse:
    _owned_project_or_404(db, project_id, current_user)
    return ProjectGenerationUsageResponse.model_validate(
        summarize_project_generation_usage(db, project_id)
    )


class CustomDomainPayload(BaseModel):
    domain: str | None = None


@router.get("/{project_id}/domain")
def get_custom_domain(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = _owned_project_or_404(db, project_id, current_user)
    return domain_response(project)


@router.put("/{project_id}/domain")
def put_custom_domain(
    project_id: UUID,
    payload: CustomDomainPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Attach or clear a custom domain. Available on every plan, including free."""
    project = _owned_project_or_404(db, project_id, current_user)
    try:
        project = set_custom_domain(db, project, payload.domain)
    except CustomDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return domain_response(project)


@router.post("/{project_id}/domain/verify")
def verify_domain(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = _owned_project_or_404(db, project_id, current_user)
    try:
        project = verify_custom_domain(db, project)
    except CustomDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return domain_response(project)
