"""Post-deploy self-verification and failed-deploy repair.

Fetches container logs (or the deployment error text when the job never got a
container) and feeds real errors back to the coding agent to fix and redeploy.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.orm import Session

from src.db.models.chat import Chat
from src.db.models.deployment import Deployment
from src.db.models.message import Message
from src.db.models.project import Project
from src.services.agent.async_utils import run_async
from src.services.agent.events import AgentDone, TextDelta, ToolCallRequested
from src.services.agent.loop import CodingAgentSession
from src.services.agent.prompt import build_repair_prompt, build_runtime_repair_prompt
from src.services.agent.tools import WorkspaceTools
from src.services.agentic_artifacts import (
    _resolve_provider_and_key,
    _write_manifest,
    ensure_required_files,
)
from src.services.artifacts import ArtifactError
from src.services.docker_control_queue import submit_control_job
from src.services.project_git import commit_snapshot
from src.services.sse_heartbeat import with_heartbeat
from src.services.workspace import project_dir as _project_dir

_ERROR_PATTERNS = (
    re.compile(r"Traceback \(most recent call last\):"),
    re.compile(r"\bCRITICAL\b"),
    re.compile(r"\bERROR\b.{0,200}(Error|Exception)"),
    re.compile(r"^\S+Error: ", re.MULTILINE),
    re.compile(r"^\S+Exception: ", re.MULTILINE),
)

_BUILD_HINTS = (
    "docker",
    "build failed",
    "dockerfile",
    "pip install",
    "requirements.txt",
    "no module named",
    "pg_config",
    "error building",
)

# Platform/infra failures - agent cannot fix these by editing project files.
_INFRA_PATTERNS = (
    re.compile(r"cannot connect to (the )?docker", re.I),
    re.compile(r"error while fetching server api version", re.I),
    re.compile(r"docker\.sock", re.I),
    re.compile(r"no space left on device", re.I),
    re.compile(r"deployment timed out", re.I),
    re.compile(r"timed out waiting for deployment", re.I),
    re.compile(r"worker crashed during deploy", re.I),
    re.compile(r"temporary failure in name resolution", re.I),
    re.compile(r"connection refused.*(?:docker|2375|2376)", re.I),
    re.compile(r"error response from daemon", re.I),
)

# Generous budget so repair sees pip/apt tails, not a 500-char logs_ref stub.
_ERROR_BUDGET = 12_000


def detect_runtime_errors(logs: str) -> str | None:
    """Returns a relevant excerpt around the first error match, or None if the log looks clean.
    Deliberately narrow patterns (real tracebacks/exceptions) to avoid false positives on
    ordinary INFO/DEBUG lines that happen to contain the word "error" in prose."""
    if not logs:
        return None
    for pattern in _ERROR_PATTERNS:
        match = pattern.search(logs)
        if match:
            start = max(0, match.start() - 500)
            return logs[start:]
    return None


def _looks_like_build_failure(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _BUILD_HINTS)


def _looks_like_infra_failure(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INFRA_PATTERNS)


def is_repairable_app_error(text: str) -> bool:
    """True when logs look like an app/build bug the coding agent can fix.

    Clear Traceback / ImportError / RuntimeError / build failures → repair.
    Pure infra (Docker daemon down, deploy timeout, worker crash) → skip auto-repair.
    If both appear, prefer repair when a real app traceback is present.
    """
    if not (text or "").strip():
        return False
    app_excerpt = detect_runtime_errors(text)
    if app_excerpt:
        return True
    if _looks_like_infra_failure(text):
        return False
    if _looks_like_build_failure(text):
        return True
    return bool(re.search(r"\b(Error|Exception|Traceback)\b", text))


def _latest_checkable_deployment(db: Session, project: Project) -> Deployment | None:
    return (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project.id,
            Deployment.status.in_(("completed", "failed")),
        )
        .order_by(Deployment.finished_at.desc().nullslast(), Deployment.started_at.desc())
        .first()
    )


def _collect_error_excerpt(
    deployment: Deployment | None, *, force_error: str | None = None
) -> tuple[str | None, bool]:
    """Returns (error_excerpt, from_build_failure)."""
    if force_error and force_error.strip():
        return force_error.strip()[-_ERROR_BUDGET:], _looks_like_build_failure(force_error)

    if not deployment:
        return None, False

    # Prefer the full stored failure text over the truncated logs_ref hint.
    if deployment.status == "failed" and deployment.error_text:
        text = deployment.error_text.strip()
        if text:
            return text[-_ERROR_BUDGET:], _looks_like_build_failure(text)

    # Failed jobs often store the exception text on logs_ref before any container exists
    # (legacy rows without error_text).
    if deployment.status == "failed" and deployment.logs_ref:
        text = deployment.logs_ref.strip()
        if text and not text.startswith("docker://"):
            return text[-_ERROR_BUDGET:], _looks_like_build_failure(text)

    # Live build log may still contain the pip/docker failure tail.
    if deployment.status == "failed" and deployment.log_text:
        text = deployment.log_text.strip()
        if text:
            return text[-_ERROR_BUDGET:], _looks_like_build_failure(text)

    if deployment.container_id:
        result = submit_control_job(
            action="logs",
            project_id=str(deployment.project_id),
            timeout_seconds=45,
            extra={"container_id": deployment.container_id, "tail": 400},
        )
        if result and result.get("ok"):
            runtime = detect_runtime_errors(result.get("logs", "") or "")
            if runtime:
                return runtime[-_ERROR_BUDGET:], False
            if deployment.status == "failed" and result.get("logs"):
                # Container existed but patterns didn't match - still give agent the tail.
                return str(result.get("logs"))[-_ERROR_BUDGET:], False

    return None, False


def _latest_chat(db: Session, project: Project) -> Chat | None:
    return (
        db.query(Chat)
        .filter(Chat.project_id == project.id)
        .order_by(Chat.created_at.desc())
        .first()
    )


def _append_chat_message(db: Session, chat: Chat | None, *, role: str, content: str) -> None:
    if not chat or not content.strip():
        return
    db.add(Message(chat_id=chat.id, role=role, content_markdown=content.strip()))


def _tool_status_label(name: str, arguments: dict[str, Any] | None = None) -> str:
    args = arguments or {}
    if name == "read_file":
        return f"Читаю {args.get('path', 'файл')}"
    if name == "write_file":
        return f"Пишу {args.get('path', 'файл')}"
    if name == "edit_file":
        return f"Правирую {args.get('path', 'файл')}"
    if name == "build_project":
        return "Проверяю сборку Docker-образа"
    if name == "list_files":
        return "Смотрю файлы проекта"
    if name == "command_execution":
        command = " ".join(str(args.get("command", "")).split())
        if len(command) > 100:
            command = command[:100] + "…"
        return f"Выполняю: {command}" if command else "Выполняю команду"
    if name == "file_change":
        files = args.get("files", "")
        return f"Правлю: {files}" if files else "Правлю файлы"
    return f"Инструмент: {name}"


async def _run_repair_events(
    project: Project, root, error_log: str, *, build_failure: bool
) -> AsyncIterator[tuple[str, Any]]:
    """Yields ('status', dict) | ('chunk', str) | ('done', summary_str)."""
    provider_name, model, api_key = _resolve_provider_and_key()
    if not api_key:
        raise ArtifactError("No AI provider key configured for automatic repair")

    workspace = WorkspaceTools(root, project_id=str(project.id))
    system_prompt = (
        build_repair_prompt(project)
        if build_failure
        else build_runtime_repair_prompt(project, error_log)
    )
    session = CodingAgentSession(
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        workspace=workspace,
        system_prompt=system_prompt,
    )
    if build_failure:
        user_message = (
            "Деплой/сборка упали с ошибкой:\n\n"
            f"{error_log[-_ERROR_BUDGET:]}\n\n"
            "Это актуальный лог последней ошибки. Исправь код и зависимости так, чтобы "
            "сборка и запуск прошли. Не спрашивай разрешения - сразу правь файлы и вызови "
            "build_project."
        )
    else:
        user_message = "Проверь и исправь ошибку из runtime-логов выше."

    final_text = ""
    yield (
        "status",
        {"phase": "thinking", "label": "Анализирую ошибку деплоя", "state": "running"},
    )
    async for event in with_heartbeat(session.run(history=[], user_message=user_message)):
        if isinstance(event, str):
            yield ("ping", event)
        elif isinstance(event, TextDelta):
            final_text += event.text
            yield ("chunk", event.text)
        elif isinstance(event, ToolCallRequested):
            yield (
                "status",
                {
                    "phase": "tool",
                    "label": _tool_status_label(event.name, event.arguments),
                    "state": "running",
                },
            )
        elif isinstance(event, AgentDone) and event.reason == "error":
            raise ArtifactError(f"AI repair failed: {event.error}")
    yield ("done", final_text)


async def _run_repair(project: Project, root, error_log: str, *, build_failure: bool) -> str:
    final_text = ""
    async for kind, payload in _run_repair_events(
        project, root, error_log, build_failure=build_failure
    ):
        if kind == "chunk":
            final_text += str(payload)
        elif kind == "done":
            final_text = str(payload) or final_text
    return final_text


def check_and_repair_deployment(
    db: Session,
    project: Project,
    *,
    force_error: str | None = None,
    chat: Chat | None = None,
) -> dict:
    """Inspect the latest completed/failed deployment and repair if an error is found.

    Safe to call repeatedly. A clean completed deployment is a no-op. Failed deployments
    are checked via error_text/logs_ref and/or container logs, then auto-fixed and redeployed once.
    """
    chat = chat or _latest_chat(db, project)
    deployment = _latest_checkable_deployment(db, project)
    if not deployment and not force_error:
        return {
            "checked": False,
            "found_errors": False,
            "fixed": False,
            "summary": "Нет деплоя для проверки (ни успешного, ни упавшего).",
        }

    error_excerpt, build_failure = _collect_error_excerpt(deployment, force_error=force_error)

    if not error_excerpt:
        if deployment and deployment.status == "failed":
            summary = (
                "Деплой упал, но детальный лог ошибки недоступен. "
                "Нажмите «Собрать и запустить» или опишите проблему в чате."
            )
            _append_chat_message(db, chat, role="assistant", content=summary)
            db.commit()
            return {
                "checked": True,
                "found_errors": True,
                "fixed": False,
                "summary": summary,
            }
        return {
            "checked": True,
            "found_errors": False,
            "fixed": False,
            "summary": "Ошибок в логах не найдено - деплой выглядит исправным.",
        }

    if not is_repairable_app_error(error_excerpt):
        summary = (
            "Деплой упал из‑за инфраструктурной ошибки платформы — "
            "автоисправление кода здесь не поможет. Попробуйте «Собрать и запустить» позже "
            "или напишите в поддержку, если повторяется."
        )
        if deployment and deployment.status == "failed":
            _append_chat_message(db, chat, role="assistant", content=summary)
            db.commit()
        return {
            "checked": True,
            "found_errors": True,
            "fixed": False,
            "skipped_infra": True,
            "summary": summary,
        }

    # Tell the user repair started (auto path used to only append after LLM finished).
    _append_chat_message(
        db,
        chat,
        role="assistant",
        content=("Нашёл ошибку запуска в логах — передаю её агенту для анализа и исправления…"),
    )
    db.commit()

    root = _project_dir(project.id)
    try:
        summary = run_async(_run_repair(project, root, error_excerpt, build_failure=build_failure))
        ensure_required_files(project, root)
    except ArtifactError as exc:
        fail_summary = f"Найдена ошибка, но автоисправление не удалось: {exc}"
        _append_chat_message(db, chat, role="assistant", content=fail_summary)
        db.commit()
        return {
            "checked": True,
            "found_errors": True,
            "fixed": False,
            "summary": fail_summary,
        }

    _write_manifest(project, root, source="deploy-repair")
    try:
        commit_snapshot(root, message=f"Автоисправление после ошибки деплоя: {project.name}")
    except Exception:  # noqa: BLE001 - git snapshotting is best-effort
        pass

    from src.services.deployments import create_deployment_for_project

    # skip_auto_check=True: this redeploy is itself the result of a check - don't chain another
    # automatic check-and-repair cycle after it lands, to bound this to one repair attempt.
    try:
        create_deployment_for_project(db, project, skip_auto_check=True)
    except Exception as exc:  # noqa: BLE001 - still report that code was fixed
        clean_summary = summary.strip() or "правки применены"
        fail_summary = (
            f"Код исправлен ({clean_summary}), "
            f"но повторный запуск не удалось поставить в очередь: {exc}"
        )
        _append_chat_message(db, chat, role="assistant", content=fail_summary)
        db.commit()
        return {
            "checked": True,
            "found_errors": True,
            "fixed": True,
            "summary": fail_summary,
        }

    clean_summary = summary.strip() or "исправлена ошибка деплоя"
    note = (
        f"Нашёл ошибку запуска и исправил: {clean_summary}. "
        "Ставлю проект в очередь на повторный запуск."
    )
    _append_chat_message(db, chat, role="assistant", content=note)
    db.commit()

    return {"checked": True, "found_errors": True, "fixed": True, "summary": clean_summary}


async def iter_repair_sse(
    db: Session,
    project: Project,
    chat: Chat,
    *,
    force_error: str | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames while repairing; persists user+assistant messages to chat."""
    excerpt_from_ui = bool(force_error and force_error.strip())
    user_note = (
        "Проверить и исправить по логам с страницы логов"
        if excerpt_from_ui
        else "Проверить и исправить последний деплой"
    )
    _append_chat_message(db, chat, role="user", content=user_note)
    db.commit()

    def _sse_chunk(chunk: str) -> str:
        return f"data: {json.dumps({'chunk': chunk})}\n\n"

    def _sse_status(phase: str, label: str, state: str = "running") -> str:
        return (
            f"data: {json.dumps({'status': {'phase': phase, 'label': label, 'state': state}})}\n\n"
        )

    deployment = _latest_checkable_deployment(db, project)
    error_excerpt, build_failure = _collect_error_excerpt(deployment, force_error=force_error)

    if not error_excerpt:
        if deployment and deployment.status == "failed":
            summary = (
                "Деплой упал, но детальный лог ошибки недоступен. "
                "Попробуйте «Собрать и запустить» или опишите проблему здесь в чате."
            )
            yield _sse_status("error", "Лог ошибки недоступен", "error")
            yield _sse_chunk(summary)
            _append_chat_message(db, chat, role="assistant", content=summary)
            db.commit()
            yield "data: [DONE]\n\n"
            return
        summary = "Ошибок в логах не найдено — деплой выглядит исправным."
        yield _sse_status("done", "Ошибок не найдено", "done")
        yield _sse_chunk(summary)
        _append_chat_message(db, chat, role="assistant", content=summary)
        db.commit()
        yield "data: [DONE]\n\n"
        return

    if not is_repairable_app_error(error_excerpt):
        summary = (
            "В переданных логах видна инфраструктурная ошибка платформы — "
            "правка кода здесь не поможет. Попробуйте «Собрать и запустить» позже."
        )
        yield _sse_status("error", "Инфраструктурная ошибка", "error")
        yield _sse_chunk(summary)
        _append_chat_message(db, chat, role="assistant", content=summary)
        db.commit()
        yield "data: [DONE]\n\n"
        return

    root = _project_dir(project.id)
    assistant_full = ""
    try:
        async for kind, payload in _run_repair_events(
            project, root, error_excerpt, build_failure=build_failure
        ):
            if kind == "ping":
                yield str(payload)
            elif kind == "status":
                status = payload if isinstance(payload, dict) else {}
                yield _sse_status(
                    str(status.get("phase") or "thinking"),
                    str(status.get("label") or "Работаю"),
                    str(status.get("state") or "running"),
                )
            elif kind == "chunk":
                text = str(payload)
                assistant_full += text
                yield _sse_chunk(text)
            elif kind == "done":
                assistant_full = str(payload) or assistant_full
        ensure_required_files(project, root)
    except ArtifactError as exc:
        summary = f"Найдена ошибка, но автоисправление не удалось: {exc}"
        yield _sse_status("error", "Автоисправление не удалось", "error")
        yield _sse_chunk(summary)
        _append_chat_message(db, chat, role="assistant", content=summary)
        db.commit()
        yield "data: [DONE]\n\n"
        return

    _write_manifest(project, root, source="deploy-repair")
    try:
        commit_snapshot(root, message=f"Автоисправление после ошибки деплоя: {project.name}")
    except Exception:  # noqa: BLE001
        pass

    from src.services.deployments import create_deployment_for_project

    yield _sse_status("deploy", "Ставлю исправленный проект в очередь запуска", "running")
    try:
        create_deployment_for_project(db, project, skip_auto_check=True)
    except Exception as exc:  # noqa: BLE001
        fail = (
            f"{assistant_full.strip() or 'Код исправлен'}, "
            f"но повторный запуск не удалось поставить в очередь: {exc}"
        )
        yield _sse_status("error", "Не удалось поставить деплой", "error")
        yield _sse_chunk(f"\n\n{fail}" if assistant_full else fail)
        _append_chat_message(db, chat, role="assistant", content=fail)
        db.commit()
        yield "data: [DONE]\n\n"
        return

    footer = "\n\nСтавлю проект в очередь на повторный запуск."
    if not assistant_full.strip():
        assistant_full = "Исправил ошибку деплоя."
    assistant_full = assistant_full.rstrip() + footer
    yield _sse_chunk(footer)
    yield _sse_status("done", "Повторный запуск поставлен в очередь", "done")
    _append_chat_message(db, chat, role="assistant", content=assistant_full)
    db.commit()
    yield "data: [DONE]\n\n"
