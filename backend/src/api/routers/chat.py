import asyncio
import json
import logging
import re
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.chat import (
    ChatCreateResponse,
    MessageCreateRequest,
    MessageResponse,
    StreamRequest,
)
from src.api.dto.files import RepairStreamRequest
from src.api.mappers.chat_files import chat_file_to_response
from src.core.config import settings
from src.db.models.chat import Chat
from src.db.models.chat_file import ChatFile
from src.db.models.deployment import Deployment
from src.db.models.message import Message
from src.db.models.moderation_event import ModerationEvent
from src.db.models.project import Project
from src.db.models.project_service import ProjectService
from src.db.models.user import User
from src.db.session import SessionLocal, get_db
from src.services.agentic_artifacts import (
    ensure_dockerfile,
    ensure_required_files,
    thin_bot_architecture_warning,
    workspace_has_agent_code,
)
from src.services.artifacts import ArtifactError, _telegram_token
from src.services.byok import resolve_turn_api_key, resolve_user_api_key
from src.services.credit_gate import out_of_credits_detail
from src.services.deployments import create_deployment_for_project
from src.services.file_context import (
    attach_files_to_message,
    build_attachment_context,
    serialize_message_metadata,
)
from src.services.model_access import ModelNotAllowedError, resolve_model_for_user
from src.services.moderation import (
    check_project_safety,
    is_token_related_block_reason,
)
from src.services.orchestration import engine as orchestration_engine
from src.services.orchestration import events_bus as orchestration_events_bus
from src.services.orchestration.repository import OrchestrationRunRepository
from src.services.project_git import ProjectGitError, commit_snapshot
from src.services.project_intent import (
    can_update_project_type,
    reconcile_type_with_workspace,
    update_project_type_from_prompt,
)
from src.services.project_runtime import RunningProjectLimitError, block_project, unblock_project
from src.services.project_services import user_facing_secret_keys
from src.services.project_subdomain import (
    assert_subdomain_available,
    ensure_deploy_subdomain,
    normalize_deploy_subdomain,
)
from src.services.prompt_guard import prepare_agent_user_message, sanitize_user_message
from src.services.secrets import capture_telegram_tokens_from_text, ensure_secret_placeholder
from src.services.sse_heartbeat import SSE_PING, Ticker, with_heartbeat
from src.services.workspace import project_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/chats", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class _StopDeployment(RuntimeError):
    pass


def _authorize_chat(
    db: Session, project_id: UUID, chat_id: UUID, current_user: User
) -> tuple[Project, Chat]:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    chat = db.get(Chat, chat_id)
    if not chat or chat.project_id != project_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return project, chat


def _validate_attachments(
    db: Session,
    *,
    project_id: UUID,
    chat_id: UUID,
    user_id: UUID,
    attachment_ids: list[UUID],
    allow_linked: bool = False,
) -> None:
    if not attachment_ids:
        return
    rows = (
        db.query(ChatFile)
        .filter(
            ChatFile.id.in_(attachment_ids),
            ChatFile.project_id == project_id,
            ChatFile.chat_id == chat_id,
            ChatFile.user_id == user_id,
        )
        .all()
    )
    if len(rows) != len(attachment_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attachments")
    if not allow_linked and any(row.message_id is not None for row in rows):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Attachment already linked"
        )


def _compose_user_message(*, content: str, attachment_ids: list[UUID], db: Session) -> str:
    """Builds the raw text handed to the coding agent as this turn's user message.

    Persona/behavior rules live in the system prompt (src/services/agent/prompt.py) now,
    not here - this only assembles what the user actually said plus attachment context.
    """

    attachment_context = build_attachment_context(db, attachment_ids, max_chars=40_000)
    parts: list[str] = []
    if content.strip():
        parts.append(content.strip())
    if attachment_context:
        parts.append(attachment_context)
    return (
        "\n\n".join(parts) if parts else "Пользователь прикрепил файлы без текста - изучи вложения."
    )


# Codex runs plain shell instead of the platform's own list_files/read_file/... tools (see
# agent/codex_runtime.py's bridge instructions - there's no structured tool name to read intent
# from, only a raw command string), so the friendly label has to be reconstructed from the
# command text itself. Checked in order, first match wins - more specific patterns (docker,
# package installs) before generic ones (any "python3" invocation), so e.g. `python3 -m pip
# install requests` reports as "Устанавливаю зависимости", not "Проверяю код".
_COMMAND_LABEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdocker\s+build\b"), "Собираю Docker-образ"),
    (re.compile(r"\bdocker\s+run\b"), "Запускаю тестовый контейнер"),
    (re.compile(r"\bdocker\s+(ps|logs|exec|inspect)\b"), "Проверяю контейнер"),
    (re.compile(r"\bdocker\s+images\b"), "Проверяю образы"),
    (re.compile(r"\bpip3?\s+install\b"), "Устанавливаю зависимости"),
    (re.compile(r"\bnpm\s+(install|ci)\b"), "Устанавливаю зависимости"),
    (re.compile(r"\bcurl\b|\bwget\b"), "Проверяю ответ сервера"),
    (re.compile(r"\bpytest\b"), "Запускаю тесты"),
    (re.compile(r"\bpython3?\b"), "Проверяю код"),
    (
        re.compile(r"\b(rg|grep)\s+--files\b|\bfind\b[^|]*-type\s+f\b|^ls\b"),
        "Изучаю структуру проекта",
    ),
    (re.compile(r"\b(sed\s+-n|cat|head|tail)\b"), "Читаю файлы проекта"),
]


def _friendly_command_label(command: str) -> str:
    # Codex's own shell wrapper - pure noise to the non-technical users this product targets,
    # never useful signal even for a technical one.
    cleaned = re.sub(r"^/bin/(ba)?sh\s+-lc\s+", "", command.strip())
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1]
    cleaned = " ".join(cleaned.split())
    for pattern, label in _COMMAND_LABEL_PATTERNS:
        if pattern.search(cleaned):
            return label
    if len(cleaned) > 80:
        cleaned = cleaned[:80] + "…"
    return f"Выполняю: {cleaned}" if cleaned else "Выполняю команду"


def _tool_status_label(name: str, arguments: dict) -> str:
    path = arguments.get("path", "") if isinstance(arguments, dict) else ""
    if name == "list_files":
        return "Изучаю структуру проекта" + (f": {path}" if path and path != "." else "")
    if name == "read_file":
        return f"Читаю {path}"
    if name == "write_file":
        return f"Пишу {path}"
    if name == "edit_file":
        return f"Правлю {path}"
    if name == "delete_file":
        return f"Удаляю {path}"
    if name == "product_brief":
        return "Анализирую задачу и формирую бриф"
    if name == "preview_project":
        return "Проверяю проект в браузере"
    if name == "design_review":
        return "Проверяю качество продукта"
    if name == "request_secret":
        key = arguments.get("key", "") if isinstance(arguments, dict) else ""
        return f"Запрашиваю секрет {key}" if key else "Запрашиваю секрет"
    if name == "request_service":
        kind = arguments.get("kind", "") if isinstance(arguments, dict) else ""
        return f"Запрашиваю сервис {kind}" if kind else "Запрашиваю сервис"
    if name == "command_execution":
        command = arguments.get("command", "") if isinstance(arguments, dict) else ""
        return _friendly_command_label(command) if command else "Выполняю команду"
    if name == "file_change":
        files = arguments.get("files", "") if isinstance(arguments, dict) else ""
        return f"Правлю: {files}" if files else "Правлю файлы"
    return f"Инструмент: {name}"


def _generated_files(path: Path, limit: int = 12) -> list[str]:
    files: list[str] = []
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(path).as_posix()
        if rel.startswith(".git/"):
            continue
        files.append(rel)
        if len(files) >= limit:
            break
    return files


def _extract_deploy_subdomain(text: str) -> str | None:
    """
    Extract requested deploy subdomain from user text.

    Supported patterns:
    - test.airuntime.ru
    - https://test.airuntime.ru
    - поддомен: test / домен: test
    - subdomain = test
    - Free-form Russian like "домен хочу test" or "пусть домен будет test" - falls back to
      the first Latin-alphabet token found shortly after the word "домен"/"поддомен".
    """

    base_domain = settings.resolved_app_domain
    text_l = text.strip().lower()
    if not text_l:
        return None

    base_domain_escaped = re.escape(base_domain)
    base_domain_labels = {label for label in base_domain.split(".") if label}
    for pattern in [
        rf"https?://([a-z0-9-]{{3,48}})\.{base_domain_escaped}",
        rf"\b([a-z0-9-]{{3,48}})\.{base_domain_escaped}\b",
        r"\bпод ?домен\w*\s*[:=]?\s*([a-z0-9-]{3,48})\b",
        r"\bдомен\w*\s*[:=]?\s*([a-z0-9-]{3,48})\b",
        r"\bsubdomain\s*[:=]?\s*([a-z0-9-]{3,48})\b",
    ]:
        match = re.search(pattern, text_l, flags=re.IGNORECASE)
        if match and match.group(1) not in base_domain_labels:
            return match.group(1)

    # Loose fallback: user mentioned "домен"/"поддомен" but phrased it as a sentence
    # ("домен хочу morning-coffee") rather than "домен: x" - grab the first Latin-script
    # token within a short window after the word, skipping the base domain's own labels.
    domain_word = re.search(r"под ?домен\w*|домен\w*|subdomain", text_l)
    if domain_word:
        window = text_l[domain_word.end() : domain_word.end() + 60]
        for candidate in re.finditer(r"\b([a-z][a-z0-9-]{2,47})\b", window):
            token = candidate.group(1)
            if token not in base_domain_labels and token not in {"the", "for", "and"}:
                return token
    return None


_DEPLOYMENT_TERMINAL = frozenset({"completed", "failed", "cancelled", "stopped"})
_DEPLOY_WAIT_SECONDS = 300
_DEPLOY_POLL_SECONDS = 2.0
# Post-deploy auto-check sleeps ~3s then may start a repair redeploy - wait past that
# before declaring success so chat doesn't claim "live" while the follow-up fails.
_DEPLOY_SETTLE_SECONDS = 6.0
# After a failed deploy the worker may hand logs to the repair agent (can take minutes).
_REPAIR_FOLLOW_SECONDS = 240.0


async def _follow_repair_redeploy(
    db: Session,
    *,
    project: Project,
    failed_deployment: Deployment,
    deadline: float,
    last_label: str,
):
    """After a failed deploy, wait for worker auto-repair to queue a follow-up deploy.

    Yields SSE status frames while waiting. Final yield is
    ("done", deployment_to_report, last_status_label).
    """
    watched_failed_id = failed_deployment.id
    failed_started = failed_deployment.started_at
    label = last_label
    ping = Ticker(15.0)

    yield _sse_status("deploy", "Анализирую ошибку запуска и исправляю", "running")
    await asyncio.sleep(_DEPLOY_SETTLE_SECONDS)
    db.expire_all()
    db.refresh(project)

    def _find_newer() -> Deployment | None:
        candidate = (
            db.query(Deployment)
            .filter(
                Deployment.project_id == project.id,
                Deployment.id != watched_failed_id,
            )
            .order_by(Deployment.started_at.desc().nullslast())
            .first()
        )
        if (
            candidate is not None
            and candidate.started_at is not None
            and failed_started is not None
            and candidate.started_at >= failed_started
        ):
            return candidate
        return None

    newer = _find_newer()
    if newer is None:
        repair_deadline = min(deadline, time.monotonic() + _REPAIR_FOLLOW_SECONDS)
        while time.monotonic() < repair_deadline:
            await asyncio.sleep(_DEPLOY_POLL_SECONDS)
            if ping.due():
                yield SSE_PING
            db.expire_all()
            newer = _find_newer()
            if newer is not None:
                break
        else:
            yield ("done", failed_deployment, label)
            return

    next_label = _deploy_status_label(newer.status)
    if next_label != label:
        state = (
            "done"
            if newer.status == "completed"
            else "error"
            if newer.status == "failed"
            else "running"
        )
        yield _sse_status("deploy", next_label, state)
        label = next_label

    if newer.status not in _DEPLOYMENT_TERMINAL:
        while time.monotonic() < deadline:
            await asyncio.sleep(_DEPLOY_POLL_SECONDS)
            if ping.due():
                yield SSE_PING
            db.expire_all()
            newer = db.get(Deployment, newer.id)
            if newer is None:
                break
            next_label = _deploy_status_label(newer.status)
            if next_label != label:
                state = (
                    "done"
                    if newer.status == "completed"
                    else "error"
                    if newer.status == "failed"
                    else "running"
                )
                yield _sse_status("deploy", next_label, state)
                label = next_label
            if newer.status in _DEPLOYMENT_TERMINAL:
                break

    yield ("done", newer if newer is not None else failed_deployment, label)


def _queue_deployment_or_notify_limit(
    db: Session, project: Project
) -> tuple[Deployment | None, str | None]:
    try:
        deployment = create_deployment_for_project(db, project)
        return deployment, None
    except RunningProjectLimitError as exc:
        project.status = "ready"
        note = (
            f"{exc}\n"
            "Файлы проекта сохранены. Остановите один из запущенных проектов "
            "и запустите этот вручную на странице «Деплои»."
        )
        project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
        db.add(project)
        return None, str(exc)


async def _stream_chat_deploy(
    db: Session,
    *,
    project: Project,
    has_website: bool,
    has_bot: bool,
    append_visible,
):
    """Queue + wait for deploy using the same create_deployment_for_project as the Deployments tab."""
    deployment, limit_message = _queue_deployment_or_notify_limit(db, project)
    db.commit()
    if limit_message:
        yield _sse_status("limit", limit_message, "error")
        yield append_visible(f"\n\n{limit_message}")
        return
    if deployment is None:
        yield append_visible(_launch_failure_message(None, project))
        yield _sse_status("error", "Запуск не удался", "error")
        return

    yield _sse_status("deploy", "Ставлю проект в очередь запуска", "running")
    deadline = time.monotonic() + _DEPLOY_WAIT_SECONDS
    last_label = ""
    watched_id = deployment.id
    deploy_ping = Ticker(15.0)
    while time.monotonic() < deadline:
        await asyncio.sleep(_DEPLOY_POLL_SECONDS)
        if deploy_ping.due():
            yield SSE_PING
        db.expire_all()
        deployment = db.get(Deployment, watched_id)
        if deployment is None:
            break
        label = _deploy_status_label(deployment.status)
        if label != last_label:
            state = (
                "done"
                if deployment.status == "completed"
                else "error"
                if deployment.status == "failed"
                else "running"
            )
            yield _sse_status("deploy", label, state)
            last_label = label
        if deployment.status in _DEPLOYMENT_TERMINAL:
            break

    if deployment is not None and deployment.status == "failed":
        async for item in _follow_repair_redeploy(
            db,
            project=project,
            failed_deployment=deployment,
            deadline=deadline,
            last_label=last_label,
        ):
            if isinstance(item, tuple):
                _, deployment, last_label = item
            else:
                yield item

    # Worker auto-check can flip project live→deploying right after a completed deploy.
    if deployment is not None and deployment.status == "completed":
        await asyncio.sleep(_DEPLOY_SETTLE_SECONDS)
        db.expire_all()
        db.refresh(project)
        deployment = db.get(Deployment, deployment.id) or deployment
        if project.status == "deploying":
            async for item in _follow_repair_redeploy(
                db,
                project=project,
                failed_deployment=deployment,
                deadline=deadline,
                last_label=last_label,
            ):
                if isinstance(item, tuple):
                    _, deployment, last_label = item
                else:
                    yield item
            db.expire_all()
            db.refresh(project)

    db.expire_all()
    db.refresh(project)
    if _chat_deploy_succeeded(deployment, project):
        yield append_visible(
            _launch_success_message(project, has_website=has_website, has_bot=has_bot)
        )
        yield _sse_status("done", "Проект запущен и работает", "done")
    else:
        yield append_visible(_launch_failure_message(deployment, project))
        yield _sse_status("error", "Запуск не удался", "error")


def _deploy_status_label(deployment_status: str) -> str:
    if deployment_status == "queued":
        return "В очереди на запуск"
    if deployment_status == "running":
        return "Собираю образ и запускаю контейнер"
    if deployment_status == "completed":
        return "Контейнер запущен"
    if deployment_status == "failed":
        return "Запуск не удался"
    return f"Статус деплоя: {deployment_status}"


def _launch_success_message(project: Project, *, has_website: bool, has_bot: bool) -> str:
    url = (project.deployment_url or "").strip()
    if has_website and has_bot:
        if url:
            return f"\n\nСайт и бот запущены и работают. Сайт: {url}"
        return "\n\nСайт и бот запущены и работают."
    if has_website:
        if url:
            return f"\n\nСайт запущен и доступен: {url}"
        return "\n\nСайт запущен и работает."
    if url:
        return f"\n\nБот запущен и работает: {url}"
    return "\n\nБот запущен и работает."


def _launch_failure_message(deployment: Deployment | None, project: Project) -> str:
    detail = ""
    if deployment and deployment.error_text:
        detail = deployment.error_text.strip()[-4000:]
    elif deployment and deployment.logs_ref and not deployment.logs_ref.startswith("docker://"):
        detail = deployment.logs_ref.strip()
    elif project.logs:
        # Prefer the last deployment-related note from project logs.
        for line in reversed(project.logs.strip().splitlines()):
            if "Deployment failed" in line or "failed" in line.lower():
                detail = line.strip()
                break
        if not detail:
            detail = project.logs.strip().splitlines()[-1][:500]
    if detail:
        return f"\n\nНе удалось запустить проект: {detail}"
    return "\n\nНе удалось запустить проект. Подробности — во вкладках «Деплои» и «Логи»."


def _workspace_is_deployable(project: Project, artifact_path: Path) -> bool:
    """Same bar as Deployments tab: a Dockerfile is enough to queue a build from disk."""
    return (artifact_path / "Dockerfile").exists() or workspace_has_agent_code(
        artifact_path, project
    )


def _commit_workspace_snapshot(project: Project, artifact_path: Path, message: str) -> None:
    try:
        ensure_dockerfile(project, artifact_path)
        commit_snapshot(artifact_path, message=message)
    except ProjectGitError as git_exc:
        logger.warning("Snapshot commit failed for project %s: %s", project.id, git_exc)
    except Exception as exc:  # noqa: BLE001 - never block chat on snapshot plumbing
        logger.warning("Snapshot prepare failed for project %s: %s", project.id, exc)


def _chat_deploy_succeeded(deployment: Deployment | None, project: Project) -> bool:
    """Chat used to require project.status==live, which races with the worker auto-check that
    briefly flips live→deploying after a successful container start — users saw a hard error
    even though Deployments already showed success."""
    if deployment is None:
        return False
    if deployment.status != "completed":
        return False
    if project.status in {"live", "deploying", "ready"}:
        return True
    return bool(project.deployment_url)


def _message_response(db: Session, message: Message) -> MessageResponse:
    files = db.query(ChatFile).filter(ChatFile.message_id == message.id).all()
    return MessageResponse(
        id=message.id,
        role=message.role,
        content_markdown=message.content_markdown,
        created_at=message.created_at,
        attachments=[chat_file_to_response(file) for file in files],
    )


def _sse_chunk(chunk: str) -> str:
    return f"data: {json.dumps({'chunk': chunk})}\n\n"


def _sse_status(phase: str, label: str, state: str = "running") -> str:
    return f"data: {json.dumps({'status': {'phase': phase, 'label': label, 'state': state}})}\n\n"


def _friendly_run_failure(detail: str) -> str:
    """Map engine error_message strings to something a non-technical user can act on."""
    key = detail.strip().lower()
    if not key:
        return "Не удалось выполнить запрос. Подробности — в панели «Оркестрация»."
    if key in {"replan limit reached", "replan_limit_reached"}:
        return (
            "Агент несколько раз пытался исправить проверку и остановился. "
            "Напишите, что поправить, или задеплойте текущие файлы во вкладке «Деплои»."
        )
    if key in {"loop_detected", "loop detected"}:
        return (
            "Агент несколько раз повторял одну и ту же ошибку. "
            "Уточните запрос в чате и нажмите «Продолжить»."
        )
    if key.startswith("budget") or "budget_exceeded" in key or "бюджет" in key:
        if "₽" in detail or "кредит" in detail.lower():
            return detail
        return "Закончился бюджет запуска. Пополните баланс или подключите свой API-ключ."
    if "deadlock" in key:
        return "План задач зашёл в тупик. Отправьте запрос ещё раз чуть конкретнее."
    # English/machine-looking strings get a Russian wrapper; already-localized text passes through.
    if detail == key or re.fullmatch(r"[a-z0-9][a-z0-9_ ./-]*", key):
        return f"Не удалось выполнить запрос ({detail}). Попробуйте отправить задачу снова."
    return f"Не удалось выполнить запрос: {detail}"


# A run_orchestration() call returns as soon as it hits waiting_for_user (see engine.py) - no
# further RunEvents will ever arrive for it until something calls resume_task_after_user_input
# and re-launches the engine. events_bus.TERMINAL_EVENT_TYPES only covers the three truly-final
# run outcomes (a live tail correctly stays open past waiting_for_user for orchestration.py's
# own /events endpoint - a client is free to just disconnect there) - but THIS generator's HTTP
# request is expected to eventually finish and send [DONE], the same way the non-orchestrated
# turn below always does, so it must stop tailing on waiting_for_user too rather than blocking on
# events that will never come.
_CHAT_STREAM_STOPPING_EVENT_TYPES = orchestration_events_bus.TERMINAL_EVENT_TYPES | {
    "waiting_for_secret",
    "waiting_for_user",
}


async def _orchestration_event_source(
    *,
    db: Session,
    project: Project,
    chat_id: UUID,
    current_user: User,
    original_request: str,
    provider_name: str,
    model: str,
    api_key: str,
    attachment_ids: list[UUID] | None = None,
    moderation_text: str = "",
):
    """The (only) chat-turn path: a persisted, DB-backed multi-task orchestration run. Ported the
    pre-engine event_source()'s required-files/Dockerfile-self-heal/thin-architecture/Telegram-
    token safety net in here directly rather than trusting it to be fully redundant with per-task
    validation - see the run_completed branch below.

    Credits are charged per-task inside the engine itself (budget.py's charge_credits_for_run) -
    this must NOT also call record_usage() again at the end."""
    assistant_full = ""

    def append_visible(text: str) -> str:
        nonlocal assistant_full
        assistant_full += text
        return _sse_chunk(text)

    yield _sse_status("thinking", "AIRuntime осмысляет задачу")

    if moderation_text.strip():
        verdict = await check_project_safety(
            text=moderation_text,
            provider_name=provider_name,
            model=model,
            api_key=api_key,
        )
        if verdict.blocked:
            reason = f"{verdict.category}: {verdict.reason}" if verdict.reason else verdict.category
            block_project(db, project, reason=reason)
            db.add(
                ModerationEvent(
                    project_id=project.id,
                    project_name=project.name,
                    user_id=current_user.id,
                    action="flagged",
                    category=verdict.category,
                    reason=verdict.reason,
                )
            )
            db.commit()
            yield _sse_status(
                "error",
                f"Проект заблокирован модерацией: {reason}",
                "error",
            )
            return

    yield _sse_status("thinking", "Составляю план работ")

    run = OrchestrationRunRepository(db).create(
        project_id=project.id,
        chat_id=chat_id,
        user_id=current_user.id,
        original_request=original_request,
        provider=provider_name,
        model=model,
        metadata_json=json.dumps(
            {
                "triggered_by": "chat",
                "attachment_ids": [str(item) for item in (attachment_ids or [])],
            },
            ensure_ascii=False,
        )
        if attachment_ids
        else json.dumps({"triggered_by": "chat"}, ensure_ascii=False),
    )
    db.commit()
    run_id = run.id
    orchestration_engine.launch_run_in_background(
        run_id, provider_name=provider_name, model=model, api_key=api_key
    )

    ping = Ticker(15.0)
    terminal_event_type: str | None = None
    task_titles: dict[str, str] = {}
    async for envelope in orchestration_events_bus.stream_events(SessionLocal, str(run_id)):
        event_type = envelope["event_type"]
        payload = envelope.get("payload") or {}
        task_id = envelope.get("task_id")
        if ping.due():
            yield SSE_PING

        if event_type == "planning_started":
            yield _sse_status("thinking", "Составляю план работ")
        elif event_type in ("plan_created", "plan_revised"):
            count = payload.get("task_count")
            yield _sse_status(
                "thinking", f"План готов: {count} задач(и)" if count else "План составлен"
            )
        elif event_type == "task_started":
            title = payload.get("title") or payload.get("local_id") or "Задача"
            if task_id:
                task_titles[task_id] = title
            yield _sse_status("tool", title, "running")
        elif event_type == "task_completed":
            yield _sse_status("tool", f"✓ {task_titles.get(task_id, 'Задача')}", "done")
        elif event_type == "task_progress":
            # Mid-task notes worth showing the user in plain language - notably the engine's
            # "Подключаю сервисы: postgres" after auto-provisioning a requested service, which
            # the pre-engine turn handler used to report itself.
            note = payload.get("note")
            if note:
                yield _sse_status("tool", str(note), "running")
        elif event_type == "budget_updated":
            spent = payload.get("credits_used")
            limit = payload.get("credit_budget")
            if spent is not None:
                cost_rub = payload.get("cost_rub")
                model_name = payload.get("model")
                label = f"Потрачено: {spent} кредитов"
                if isinstance(cost_rub, int | float):
                    label += f" ({cost_rub:.2f} ₽)"
                if model_name:
                    label += f" · {model_name}"
                if limit:
                    label += f" из {limit}"
                # Only escalate to a visible warning when the run is genuinely near the ceiling -
                # a loud running total on every task would just be noise otherwise.
                if payload.get("status") == "approaching":
                    yield _sse_status("limit", f"{label} — бюджет почти исчерпан", "error")
                else:
                    yield _sse_status("tool", label, "running")
        elif event_type == "task_repairing":
            yield _sse_status("tool", "Исправляю ошибку и повторяю", "running")
        elif event_type == "task_failed":
            yield _sse_status("tool", f"⚠ {task_titles.get(task_id, 'Задача')}", "error")
        elif event_type == "waiting_for_secret":
            # A task may ask for a credential belonging to a service it (or an earlier task)
            # already provisioned via request_service - e.g. POSTGRES_PASSWORD. The platform
            # generates and wires those up itself, so the user must never be asked to fill one
            # in. Same suppression the pre-engine turn handler applied.
            existing_service_kinds = {
                row.kind
                for row in db.query(ProjectService)
                .filter(ProjectService.project_id == project.id)
                .all()
            }
            lines = []
            for key in user_facing_secret_keys(
                list(payload.get("requested_secrets") or []),
                service_kinds=existing_service_kinds,
            ):
                secret_row, created = ensure_secret_placeholder(
                    db, project, key, "Требуется для продолжения выполнения задачи"
                )
                if created:
                    lines.append(f"- **{secret_row.key}**")
            db.commit()
            if lines:
                yield append_visible(
                    "\n\nЧтобы продолжить, заполните в настройках проекта "
                    "(вкладка «Настройки») эти значения:\n" + "\n".join(lines)
                )
                yield _sse_status("needs_configuration", "Нужны данные от вас", "error")
            else:
                yield append_visible(
                    "\n\nАгент запросил данные, которые платформа создаёт сама. "
                    "Напишите «продолжить» — секреты сервиса заполнять не нужно."
                )
                yield _sse_status("questions", "Нужен ответ от вас", "error")
        elif event_type == "waiting_for_user":
            reason = str(payload.get("reason") or "").strip().lower()
            detail = str(payload.get("detail") or "").strip()
            if reason == "budget_exceeded" or "budget" in reason or "кредит" in detail.lower():
                message = detail or (
                    "Закончились кредиты — работа агента приостановлена. "
                    "Пополните баланс в профиле или подключите свой API-ключ, затем продолжите в «Оркестрация»."
                )
                yield append_visible(f"\n\n{message}")
                yield _sse_status("limit", "Закончились кредиты", "error")
            elif detail:
                yield append_visible(f"\n\n{detail}")
                yield _sse_status("questions", "Нужен ответ от вас", "error")
            else:
                yield append_visible(
                    "\n\nВыполнение приостановлено — нужно ваше действие, чтобы продолжить."
                )
                yield _sse_status("questions", "Нужен ответ от вас", "error")

        if event_type in _CHAT_STREAM_STOPPING_EVENT_TYPES:
            terminal_event_type = event_type
            break

    db.expire_all()
    db.refresh(project)
    run = OrchestrationRunRepository(db).get(run_id)

    if terminal_event_type == "run_completed":
        # Deployability matches the Deployments tab (Dockerfile / agent code on disk).
        # ensure_required_files is advisory here — a hard stop made chat refuse to queue while
        # the same tree deployed fine from «Деплои».
        artifact_path = project_dir(project.id)
        yield _sse_status("verify", "Проверяю готовые файлы проекта")
        files_ok = _workspace_is_deployable(project, artifact_path)
        try:
            if reconcile_type_with_workspace(
                project, artifact_path, has_bot_secret=bool(_telegram_token(db, project))
            ):
                db.add(project)
                db.commit()
                db.refresh(project)
            ensure_required_files(project, artifact_path)
        except ArtifactError as verify_exc:
            if files_ok:
                ensure_dockerfile(project, artifact_path)
                yield append_visible(
                    f"\n\nПроверка entrypoint неполная ({verify_exc}), но код агента сохранён — "
                    "ставлю в очередь запуска как во вкладке «Деплои»."
                )
            else:
                yield _sse_status("verify", "Не хватает файлов проекта", "error")
                yield append_visible(
                    f"\n\nАгент не создал обязательные файлы ({verify_exc}). "
                    "Опишите задачу ещё раз или уточните, что нужно дописать."
                )

        if files_ok:
            thin_arch = thin_bot_architecture_warning(artifact_path, project)
            if thin_arch:
                yield _sse_status("verify", "Архитектура выглядит слишком тонкой", "error")
                yield append_visible(f"\n\n{thin_arch}")
            await asyncio.to_thread(
                _commit_workspace_snapshot,
                project,
                artifact_path,
                f"{project.name}: {original_request}",
            )

            project.status = "ready"
            db.add(project)
            has_website = project.type in ("website", "mixed")
            has_bot = project.type in ("telegram_bot", "mixed")
            if reconcile_type_with_workspace(
                project, artifact_path, has_bot_secret=bool(_telegram_token(db, project))
            ):
                has_website = project.type in ("website", "mixed")
                has_bot = project.type in ("telegram_bot", "mixed")
                db.add(project)
            if has_bot and not _telegram_token(db, project):
                ensure_secret_placeholder(
                    db, project, "TELEGRAM_BOT_TOKEN", "Нужен для запуска Telegram-бота"
                )
                project.status = "needs_configuration"
                db.add(project)
                db.commit()
                token_help_url = (
                    f"{settings.resolved_frontend_url}/help/telegram-token?projectId={project.id}"
                )
                token_note = (
                    "Код бота готов, но запуск остановлен: добавьте секрет TELEGRAM_BOT_TOKEN "
                    "в настройках проекта и повторите запуск.\n\n"
                    "Как получить токен: откройте @BotFather в Telegram, выполните /newbot "
                    f"и скопируйте выданный token. [Подробная инструкция]({token_help_url})"
                )
                yield _sse_status("needs_configuration", "Нужен TELEGRAM_BOT_TOKEN", "error")
                yield append_visible(f"\n\n{token_note}")
            elif has_website or has_bot:
                async for item in _stream_chat_deploy(
                    db,
                    project=project,
                    has_website=has_website,
                    has_bot=has_bot,
                    append_visible=append_visible,
                ):
                    yield item
            else:
                yield _sse_status("done", "Готово", "done")
    elif terminal_event_type == "run_failed":
        detail = (run.error_message or "").strip() if run else ""
        friendly = _friendly_run_failure(detail)
        artifact_path = project_dir(project.id)
        has_code = _workspace_is_deployable(project, artifact_path)
        if has_code:
            # Persist whatever is on disk so «Файлы»/Versions light up even when auto-QA failed.
            await asyncio.to_thread(
                _commit_workspace_snapshot,
                project,
                artifact_path,
                f"{project.name}: snapshot after failed run",
            )
            has_website = project.type in ("website", "mixed")
            has_bot = project.type in ("telegram_bot", "mixed")
            if reconcile_type_with_workspace(
                project, artifact_path, has_bot_secret=bool(_telegram_token(db, project))
            ):
                has_website = project.type in ("website", "mixed")
                has_bot = project.type in ("telegram_bot", "mixed")
                db.add(project)
            if has_bot and not _telegram_token(db, project):
                ensure_secret_placeholder(
                    db, project, "TELEGRAM_BOT_TOKEN", "Нужен для запуска Telegram-бота"
                )
                project.status = "needs_configuration"
                db.add(project)
                db.commit()
                yield append_visible(
                    f"\n\n{friendly}\n\nФайлы сохранены. Для запуска бота добавьте TELEGRAM_BOT_TOKEN "
                    "в настройках."
                )
                yield _sse_status("needs_configuration", "Нужен TELEGRAM_BOT_TOKEN", "error")
            elif has_website or has_bot:
                yield append_visible(
                    f"\n\n{friendly}\n\nФайлы уже на диске — запускаю деплой, как во вкладке «Деплои»."
                )
                async for item in _stream_chat_deploy(
                    db,
                    project=project,
                    has_website=has_website,
                    has_bot=has_bot,
                    append_visible=append_visible,
                ):
                    yield item
            else:
                yield append_visible(f"\n\n{friendly}")
                yield _sse_status("error", "Не удалось выполнить запрос", "error")
        else:
            yield append_visible(f"\n\n{friendly}")
            yield _sse_status("error", "Не удалось выполнить запрос", "error")
    elif terminal_event_type == "run_cancelled":
        yield append_visible("\n\nВыполнение остановлено.")
        yield _sse_status("error", "Остановлено", "error")
    # waiting_for_secret/waiting_for_user already yielded their own status/message above.

    assistant_message = Message(
        id=uuid4(), chat_id=chat_id, role="assistant", content_markdown=assistant_full
    )
    db.add(assistant_message)
    db.commit()
    yield "data: [DONE]\n\n"


@router.post("", response_model=ChatCreateResponse)
def create_chat(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Chat:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    chat = Chat(project_id=project.id, title=f"{project.name} chat")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@router.get("", response_model=list[ChatCreateResponse])
def list_chats(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Chat]:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return (
        db.query(Chat).filter(Chat.project_id == project_id).order_by(Chat.created_at.desc()).all()
    )


@router.get("/{chat_id}/messages", response_model=list[MessageResponse])
def list_messages(
    project_id: UUID,
    chat_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageResponse]:
    _authorize_chat(db, project_id, chat_id, current_user)
    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [_message_response(db, message) for message in messages]


@router.post("/{chat_id}/messages", response_model=MessageResponse)
def create_message(
    project_id: UUID,
    chat_id: UUID,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    project, _chat = _authorize_chat(db, project_id, chat_id, current_user)
    _validate_attachments(
        db,
        project_id=project.id,
        chat_id=chat_id,
        user_id=current_user.id,
        attachment_ids=payload.attachment_ids,
    )
    content = payload.content.strip()
    if content:
        try:
            content = sanitize_user_message(content)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        # Persist BotFather tokens into secrets and keep the raw value out of chat history.
        content = capture_telegram_tokens_from_text(db, project, content)
    display_content = content or "Shared attachments"
    message = Message(
        chat_id=chat_id,
        role="user",
        content_markdown=display_content,
        metadata_json=serialize_message_metadata(payload.attachment_ids),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    try:
        attach_files_to_message(db, message_id=message.id, attachment_ids=payload.attachment_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _message_response(db, message)


async def _stream_events(
    *,
    db: Session,
    project: Project,
    chat_id: UUID,
    current_user: User,
    user_message: str,
    attachment_ids: list[UUID],
    provider_override: str | None = None,
    model_override: str | None = None,
) -> StreamingResponse:
    if current_user.credits_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=out_of_credits_detail(db, current_user),
        )
    if current_user.is_banned:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт заблокирован администратором"
            + (f": {current_user.banned_reason}" if current_user.banned_reason else "."),
        )
    if project.status == "blocked":
        # Pasting a BotFather token used to be auto-flagged as scam - heal those blocks in place
        # so the user does not need an admin to continue.
        if is_token_related_block_reason(project.blocked_reason):
            unblock_project(db, project)
            db.add(
                ModerationEvent(
                    project_id=project.id,
                    project_name=project.name,
                    user_id=current_user.id,
                    action="unblocked",
                    category="",
                    reason="Авто: ложное срабатывание на токен Telegram-бота",
                )
            )
            db.commit()
            db.refresh(project)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Проект заблокирован модерацией"
                + (f": {project.blocked_reason}" if project.blocked_reason else "."),
            )

    content = user_message.strip()
    if content:
        try:
            content = sanitize_user_message(content)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        content = capture_telegram_tokens_from_text(db, project, content)

    # Only allowed to move away from the initial guess while nothing has been generated yet -
    # once a project has real files/a deploy, a later message mentioning an unrelated word
    # (e.g. any form of "работать") must not silently flip it to a different project type.
    if (
        content
        and can_update_project_type(project)
        and update_project_type_from_prompt(project, content)
    ):
        db.add(project)
        db.commit()
        db.refresh(project)

    # Not gated on project.type == "website": a domain mention is deliberate user intent that
    # must never be silently dropped, even if the project's current type classification (set
    # from an earlier message, or about to change) doesn't happen to be "website" right now.
    if content:
        extracted_subdomain = _extract_deploy_subdomain(content)
        if extracted_subdomain:
            normalized = normalize_deploy_subdomain(extracted_subdomain)
            assert_subdomain_available(db, normalized, exclude_project_id=str(project.id))
            project.deploy_subdomain = normalized
            db.add(project)
            db.commit()
            db.refresh(project)

    # If the user did not name a domain, allocate a readable subdomain from the project
    # name / prompt essence (still uniqueness-checked) instead of name-<uuid>.
    if not project.deploy_subdomain and ensure_deploy_subdomain(
        db, project, prompt=content or None
    ):
        db.commit()
        db.refresh(project)

    user_agent_message = _compose_user_message(
        content=content, attachment_ids=attachment_ids, db=db
    )
    # Content already passed sanitize_user_message (injection + hard size). For the agent,
    # keep the useful tail of oversized log pastes instead of 400/422 rejecting the turn.
    safe_message = prepare_agent_user_message(user_agent_message)

    # Resolved first: a user on their own key is not subject to the plan's model allowlist.
    byok_provider = (provider_override or settings.provider_name).strip().lower()
    has_own_key = bool(resolve_user_api_key(db, current_user, byok_provider))
    try:
        provider_name, model = resolve_model_for_user(
            db,
            current_user,
            provider_override=provider_override,
            model_override=model_override,
            has_own_key=has_own_key,
        )
    except ModelNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # BYOK first: the user's own key means the provider bills them, not us.
    api_key = resolve_turn_api_key(db, current_user, provider_name) or ""

    return StreamingResponse(
        with_heartbeat(
            _orchestration_event_source(
                db=db,
                project=project,
                chat_id=chat_id,
                current_user=current_user,
                original_request=safe_message,
                provider_name=provider_name,
                model=model,
                api_key=api_key,
                attachment_ids=attachment_ids,
                moderation_text=content,
            )
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{chat_id}/stream")
async def stream_reply_post(
    project_id: UUID,
    chat_id: UUID,
    payload: StreamRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    project, _chat = _authorize_chat(db, project_id, chat_id, current_user)
    _validate_attachments(
        db,
        project_id=project.id,
        chat_id=chat_id,
        user_id=current_user.id,
        attachment_ids=payload.attachment_ids,
        allow_linked=True,
    )
    return await _stream_events(
        db=db,
        project=project,
        chat_id=chat_id,
        current_user=current_user,
        user_message=payload.content,
        attachment_ids=payload.attachment_ids,
        provider_override=payload.provider,
        model_override=payload.model,
    )


@router.post("/{chat_id}/repair-stream")
async def stream_repair_post(
    project_id: UUID,
    chat_id: UUID,
    payload: RepairStreamRequest = Body(default_factory=RepairStreamRequest),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run check-and-repair with the same live SSE status/chunks as a normal chat turn."""
    from src.services.deployment_check import iter_repair_sse

    project, chat = _authorize_chat(db, project_id, chat_id, current_user)
    if current_user.credits_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=out_of_credits_detail(db, current_user),
        )
    force_error = (payload.error_log or "").strip() or None

    async def event_source():
        async for frame in iter_repair_sse(db, project, chat, force_error=force_error):
            yield frame

    return StreamingResponse(
        with_heartbeat(event_source()),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/{chat_id}/stream")
async def stream_reply_get(
    project_id: UUID,
    chat_id: UUID,
    q: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    project, _chat = _authorize_chat(db, project_id, chat_id, current_user)
    return await _stream_events(
        db=db,
        project=project,
        chat_id=chat_id,
        current_user=current_user,
        user_message=q,
        attachment_ids=[],
    )
