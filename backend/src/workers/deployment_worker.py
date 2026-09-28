import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.logging_setup import configure_logging
from src.db.models.deployment import Deployment
from src.db.models.project import Project
from src.db.models.project_service import ProjectService
from src.db.models.user import User
from src.db.session import SessionLocal
from src.services.artifacts import build_project_image
from src.services.billing import run_billing_maintenance
from src.services.cloudflare_dns import (
    CloudflareDnsError,
    sync_dns_for_website_deploy,
    user_facing_dns_note,
)
from src.services.deployment.docker_adapter import DeployRequest, DockerDeploymentAdapter
from src.services.deployment_check import (
    check_and_repair_deployment,
    detect_runtime_errors,
    is_repairable_app_error,
)
from src.services.deployment_queue import pop_deployment_job
from src.services.deployments import (
    append_deployment_log,
    store_deployment_error,
    truncate_logs_ref,
)
from src.services.docker_control_actions import run_control_action
from src.services.docker_control_queue import (
    pop_control_job,
    push_control_result,
    worker_inline_docker,
)
from src.services.email import send_branded_email
from src.services.email_templates import deploy_failed_email, project_deployed_email
from src.services.image_janitor import sweep_temporary_containers, sweep_unrecognized_images
from src.services.project_services import (
    build_connection_env,
    ensure_service_containers,
)
from src.services.project_subdomain import ensure_deploy_subdomain, resolve_deploy_subdomain
from src.services.runtime_reconciler import reconcile_live_projects
from src.services.telegram_profile import TelegramProfileError, fetch_bot_profile

logger = logging.getLogger(__name__)

BILLING_SWEEP_INTERVAL_SECONDS = 300
STALE_SWEEP_INTERVAL_SECONDS = 60
IMAGE_SWEEP_INTERVAL_SECONDS = 3600
RUNTIME_SWEEP_INTERVAL_SECONDS = 300


def process_billing_sweep() -> None:
    db: Session = SessionLocal()
    try:
        run_billing_maintenance(db)
    finally:
        db.close()


def process_image_sweep() -> None:
    adapter = DockerDeploymentAdapter()
    sweep_temporary_containers(adapter.client)
    sweep_unrecognized_images(adapter.client)


def process_runtime_sweep() -> None:
    db: Session = SessionLocal()
    try:
        reconcile_live_projects(db)
    finally:
        db.close()


def _run_codex_job(job: dict) -> None:
    from src.services.agent.codex_worker import execute_codex_run

    job_id = job.get("job_id")
    logger.info(
        "Codex job starting id=%s project=%s model=%s",
        job_id,
        job.get("project_id"),
        job.get("model"),
    )
    try:
        execute_codex_run(job)
        logger.info("Codex job finished id=%s", job_id)
    except Exception:  # noqa: BLE001 - execute_codex_run already reports failure via Redis
        # Only reachable if relaying to Redis itself failed (execute_codex_run's own finally
        # already turns a Codex/Docker-side failure into an infra_error event, not an
        # exception) - the chat stream got no error frame in that case, so this is the one
        # place a run_id's fate is otherwise unrecoverable after the fact.
        logger.exception("Codex job %s failed to relay events", job_id)


def _spawn_codex_run(job: dict) -> None:
    # A coding turn can run for minutes - handle it on its own thread so the main loop keeps
    # popping other control/deployment jobs instead of stalling behind it. Not routed through
    # run_control_action: that returns one dict for a Redis RPC result key nobody polls here -
    # codex_run's caller (agent/codex_runtime.py) polls a live events list instead.
    logger.info("Codex job queued on worker thread id=%s", job.get("job_id"))
    threading.Thread(
        target=_run_codex_job, args=(job,), daemon=True, name=f"codex-{job.get('job_id', '')[:12]}"
    ).start()


def process_control_job(job: dict) -> None:
    job_id = job.get("job_id", "")
    action = job.get("action")
    project_id = job.get("project_id")
    if action == "codex_run":
        _spawn_codex_run(job)
        return
    # Drop Redis envelope keys so run_control_action only sees action-specific extras.
    extra = {
        key: value for key, value in job.items() if key not in {"job_id", "action", "project_id"}
    }
    with worker_inline_docker():
        result = run_control_action(action=action, project_id=project_id, extra=extra)
    push_control_result(job_id, result)


def process_job(job: dict) -> None:
    db: Session = SessionLocal()
    with worker_inline_docker():
        _process_job_body(db, job)


def _process_job_body(db: Session, job: dict) -> None:
    try:
        deployment = db.get(Deployment, job["deployment_id"])
        if not deployment:
            return
        if deployment.status in {"cancelled", "stopped"}:
            return
        project = db.get(Project, job["project_id"])
        if not project:
            deployment.status = "failed"
            deployment.finished_at = datetime.now(UTC)
            db.commit()
            return
        if project.status == "stopped":
            deployment.status = "cancelled"
            deployment.finished_at = datetime.now(UTC)
            db.commit()
            return

        deployment.status = "running"
        deployment.started_at = datetime.now(UTC)
        append_deployment_log(deployment, "Запуск сборки…\n")
        db.add(deployment)
        db.commit()

        def _on_build_log(chunk: str) -> None:
            # Flush live build output so the deployments page can poll log_text.
            append_deployment_log(deployment, chunk)
            db.add(deployment)
            try:
                db.commit()
            except Exception:  # noqa: BLE001 - never fail the build because of log flush
                db.rollback()

        image_ref, environment = build_project_image(db, project, on_log=_on_build_log)
        append_deployment_log(deployment, "\nОбраз собран. Запускаю контейнер…\n")
        db.add(deployment)
        db.commit()
        if not project.deploy_subdomain:
            ensure_deploy_subdomain(db, project, prompt=project.description or None)
            db.commit()
        subdomain = resolve_deploy_subdomain(project)
        has_website = project.type in ("website", "mixed")
        has_bot = project.type in ("telegram_bot", "mixed")
        expose_http = has_website
        telegram_url = None
        if has_bot:
            token = environment.get("TELEGRAM_BOT_TOKEN")
            if not token:
                raise RuntimeError("Telegram bot token is not configured")
            try:
                telegram_url = fetch_bot_profile(token).public_url
            except TelegramProfileError as telegram_exc:
                raise RuntimeError(str(telegram_exc)) from telegram_exc

        adapter = DockerDeploymentAdapter()
        services = db.query(ProjectService).filter(ProjectService.project_id == project.id).all()
        service_network = None
        if services:
            service_network = adapter.ensure_private_network(str(project.id))
            ensure_service_containers(adapter.client, str(project.id), services)
            environment.update(build_connection_env(db, project))

        result = adapter.deploy(
            DeployRequest(
                project_id=str(project.id),
                image_ref=image_ref,
                subdomain=subdomain,
                environment=environment,
                expose_http=expose_http,
                service_network=service_network,
            )
        )

        # Confirm the process stays up after start - "docker run succeeded" is not enough
        # (bots/sites often crash on first import or missing env within a few seconds).
        append_deployment_log(deployment, "Контейнер создан. Проверяю, что процесс не падает…\n")
        db.add(deployment)
        db.commit()
        startup_logs = adapter.verify_still_running(result["container_id"], settle_seconds=5.0)
        startup_error = detect_runtime_errors(startup_logs)
        if startup_error:
            append_deployment_log(deployment, f"\n{startup_error[-8000:]}\n")
            try:
                adapter.stop_project(str(project.id))
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                "Контейнер запустился, но сразу выдал ошибку в runtime-логах:\n"
                f"{startup_error[-8000:]}"
            )

        append_deployment_log(deployment, "Контейнер стабилен после старта.\n")
        deployment.status = "completed"
        deployment.container_id = result["container_id"]
        deployment.logs_ref = result["logs_ref"]
        deployment.error_text = None
        deployment.image_ref = result["image_ref"]
        deployment.finished_at = datetime.now(UTC)
        db.refresh(project)
        if project.status == "stopped":
            DockerDeploymentAdapter().stop_project(str(project.id))
            deployment.status = "cancelled"
            deployment.finished_at = datetime.now(UTC)
            db.add(deployment)
            db.commit()
            return
        project.deployment_url = result["url"] if expose_http else telegram_url
        project.status = "live"
        if expose_http:
            try:
                dns_host = sync_dns_for_website_deploy(subdomain)
                if dns_host:
                    dns_note = user_facing_dns_note(subdomain, host=dns_host)
                    project.logs = (
                        f"{project.logs}\n{dns_note}".strip() if project.logs else dns_note
                    )
            except CloudflareDnsError as dns_exc:
                dns_note = user_facing_dns_note(subdomain, error=str(dns_exc))
                project.logs = f"{project.logs}\n{dns_note}".strip() if project.logs else dns_note
        if has_website and has_bot and telegram_url:
            # deployment_url holds the site's public link (the primary "open project" link) -
            # the bot's own link has nowhere else to live yet, so it goes into the log feed.
            bot_note = f"Telegram-бот доступен: {telegram_url}"
            project.logs = f"{project.logs}\n{bot_note}".strip() if project.logs else bot_note
        db.add(project)
        db.commit()

        _notify_project_deployed(db, project)

        if not job.get("skip_auto_check"):
            # Extra pass after we already verified the container stayed up: catches errors that
            # only appear slightly later. Manual "Проверить и исправить" covers user-triggered bugs.
            try:
                check_and_repair_deployment(db, project)
            except Exception:  # noqa: BLE001 - a broken self-check must never fail the deploy
                logger.exception("Post-deploy self-check/repair failed for project %s", project.id)
    except Exception as exc:
        _mark_deployment_failed(db, job, exc)
    finally:
        db.close()


def _project_app_url(project_id) -> str:
    return f"{settings.resolved_frontend_url.rstrip('/')}/app/projects/{project_id}"


def _notify_project_deployed(db: Session, project: Project) -> None:
    user = db.get(User, project.user_id)
    if not user or not user.email:
        return
    open_url = _project_app_url(project.id)
    content = project_deployed_email(
        project_name=project.name,
        project_url=project.deployment_url,
        open_url=open_url,
    )
    try:
        send_branded_email(
            to=user.email, subject=content.subject, plain=content.plain, html=content.html
        )
    except Exception:  # noqa: BLE001 - email must never fail the deploy
        logger.exception("Failed to send deploy success email for project %s", project.id)


def _notify_deploy_failed(db: Session, project: Project | None, *, failed_at: datetime) -> None:
    if not project:
        return
    user = db.get(User, project.user_id)
    if not user or not user.email:
        return
    content = deploy_failed_email(
        project_name=project.name,
        failed_at=failed_at,
        check_url=f"{_project_app_url(project.id)}/deployments",
        summary="Контейнер не прошёл проверку после старта или сборка завершилась с ошибкой.",
    )
    try:
        send_branded_email(
            to=user.email, subject=content.subject, plain=content.plain, html=content.html
        )
    except Exception:  # noqa: BLE001 - email must never fail the worker
        logger.exception("Failed to send deploy failure email for project %s", project.id)


def _mark_deployment_failed(db: Session, job: dict, exc: BaseException) -> None:
    """Always terminalize the deployment row, even if logging the error is awkward."""
    deployment = db.get(Deployment, job.get("deployment_id"))
    if not deployment:
        return
    full_error = str(exc)
    try:
        deployment.status = "failed"
        store_deployment_error(deployment, full_error)
        deployment.finished_at = datetime.now(UTC)
        project = db.get(Project, job.get("project_id"))
        if project and project.status in {"deploying", "live"}:
            # Generic deploy failure is not "needs secrets" - leave that for missing tokens.
            project.status = "ready"
            note = f"Deployment failed: {truncate_logs_ref(full_error)}"
            project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
            db.add(project)
        db.add(deployment)
        db.commit()
        _notify_deploy_failed(db, project, failed_at=deployment.finished_at or datetime.now(UTC))
    except Exception:  # noqa: BLE001 - last-resort hard fail without long logs_ref
        db.rollback()
        deployment = db.get(Deployment, job.get("deployment_id"))
        if not deployment:
            return
        deployment.status = "failed"
        deployment.logs_ref = "Deployment failed (see worker logs)"
        deployment.error_text = full_error[:50_000] if full_error else "Deployment failed"
        deployment.finished_at = datetime.now(UTC)
        project = db.get(Project, job.get("project_id"))
        if project and project.status in {"deploying", "live"}:
            project.status = "ready"
            db.add(project)
        db.add(deployment)
        db.commit()
        _notify_deploy_failed(db, project, failed_at=deployment.finished_at or datetime.now(UTC))
        return

    project = db.get(Project, job.get("project_id"))
    if project and not job.get("skip_auto_check") and is_repairable_app_error(full_error):
        try:
            # Pass the FULL error so repair is not limited to the 500-char logs_ref hint.
            # Runs under worker_inline_docker so build_project/logs do not self-deadlock.
            check_and_repair_deployment(db, project, force_error=full_error)
        except Exception:  # noqa: BLE001 - never crash the worker on repair failure
            pass


def reap_stale_deployments() -> int:
    """Mark queued/running deployments older than deployment_timeout_seconds as failed."""
    timeout = max(30, int(getattr(settings, "deployment_timeout_seconds", 120) or 120))
    cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
    db: Session = SessionLocal()
    reaped = 0
    try:
        candidates = db.query(Deployment).filter(Deployment.status.in_(("queued", "running"))).all()
        for deployment in candidates:
            # Null started_at = orphan from older code paths; treat as immediately stale.
            if deployment.started_at is not None and deployment.started_at >= cutoff:
                continue
            prior_status = deployment.status
            deployment.status = "failed"
            store_deployment_error(
                deployment,
                f"Deployment timed out after {timeout}s (stuck in {prior_status})",
            )
            deployment.finished_at = datetime.now(UTC)
            project = db.get(Project, deployment.project_id)
            if project and project.status == "deploying":
                # Only clear deploying if no other active deploy remains.
                other_active = (
                    db.query(Deployment)
                    .filter(
                        Deployment.project_id == project.id,
                        Deployment.id != deployment.id,
                        Deployment.status.in_(("queued", "running")),
                    )
                    .first()
                )
                if not other_active:
                    project.status = "ready"
                    note = f"Deployment timed out: {deployment.id}"
                    project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
                    db.add(project)
            db.add(deployment)
            reaped += 1
        if reaped:
            db.commit()
    finally:
        db.close()
    return reaped


def run() -> None:
    configure_logging()
    logger.info("Deployment worker starting")
    last_billing_sweep = 0.0
    last_stale_sweep = 0.0
    last_image_sweep = 0.0
    last_runtime_sweep = 0.0
    try:
        reap_stale_deployments()
    except Exception:  # noqa: BLE001
        logger.exception("Initial stale-deployment sweep failed")
    try:
        process_runtime_sweep()
    except Exception:  # noqa: BLE001
        logger.exception("Initial runtime reconciliation failed")
    while True:
        control_job = pop_control_job(timeout_seconds=2)
        if control_job:
            try:
                process_control_job(control_job)
            except Exception:  # noqa: BLE001 - never kill the worker loop
                logger.exception("Control job failed: %r", control_job)
            continue
        job = pop_deployment_job(timeout_seconds=2)
        if job:
            try:
                process_job(job)
            except Exception:  # noqa: BLE001 - process_job should self-contain, but belt+suspenders
                logger.exception("Deployment job crashed the worker loop: %r", job)
                try:
                    db = SessionLocal()
                    try:
                        _mark_deployment_failed(
                            db, job, RuntimeError("Worker crashed during deploy")
                        )
                    finally:
                        db.close()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Could not mark deployment %s failed after worker crash",
                        job.get("deployment_id"),
                    )

        now = time.monotonic()
        if now - last_stale_sweep >= STALE_SWEEP_INTERVAL_SECONDS:
            last_stale_sweep = now
            try:
                reap_stale_deployments()
            except Exception:  # noqa: BLE001
                logger.exception("Stale-deployment sweep failed")
        if now - last_billing_sweep >= BILLING_SWEEP_INTERVAL_SECONDS:
            last_billing_sweep = now
            try:
                process_billing_sweep()
            except Exception:  # noqa: BLE001 - never let a billing hiccup kill the worker loop
                logger.exception("Billing sweep failed")
        if now - last_image_sweep >= IMAGE_SWEEP_INTERVAL_SECONDS:
            last_image_sweep = now
            try:
                process_image_sweep()
            except Exception:  # noqa: BLE001 - never let a Docker hiccup kill the worker loop
                logger.exception("Image janitor sweep failed")
        if now - last_runtime_sweep >= RUNTIME_SWEEP_INTERVAL_SECONDS:
            last_runtime_sweep = now
            try:
                process_runtime_sweep()
            except Exception:  # noqa: BLE001 - never let Docker drift kill the worker loop
                logger.exception("Runtime reconciliation failed")


if __name__ == "__main__":
    run()
