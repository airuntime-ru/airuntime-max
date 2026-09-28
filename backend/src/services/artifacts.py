from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import docker
from docker.errors import BuildError, DockerException
from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.project import Project
from src.db.models.secret import Secret
from src.services.project_git import with_project_git_lock
from src.services.secrets import TELEGRAM_BOT_TOKEN_KEY, decrypt_secret, normalize_secret_key

LogCallback = Callable[[str], None]


class ArtifactError(RuntimeError):
    pass


def _root() -> Path:
    root = Path(settings.generated_projects_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_dir(project_id: UUID | str) -> Path:
    safe_id = str(project_id)
    if not re.fullmatch(r"[a-fA-F0-9-]{32,36}", safe_id):
        raise ArtifactError("Invalid project id for artifact path")
    path = _root() / safe_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _image_tag(project: Project) -> str:
    prefix = {"website": "site", "telegram_bot": "bot", "mixed": "mixed"}.get(project.type, "app")
    return f"airuntime-generated-{prefix}-{str(project.id)[:12]}:latest"


def _telegram_token(db: Session, project: Project) -> str | None:
    rows = db.query(Secret).filter(Secret.project_id == project.id).all()
    for row in rows:
        if not row.encrypted_value:
            continue
        normalized_key = normalize_secret_key(row.key, project_type=project.type)
        if normalized_key == TELEGRAM_BOT_TOKEN_KEY:
            return decrypt_secret(row.encrypted_value)
    return None


def _all_secret_environment(db: Session, project: Project) -> dict[str, str]:
    """Every user-supplied secret (agent's request_secret tool call, filled in via the project's
    Settings tab) with a value on file, delivered to the deployed container as an env var keyed
    by its own Secret.key - already normalized to a valid env-var name at creation time (see
    secrets.py's ensure_secret_placeholder), so no re-normalization is needed here.

    Previously only TELEGRAM_BOT_TOKEN ever made it into the deployed container's environment -
    every other requested secret (a payment provider key, a third-party API token, ...) was
    collected from the user but silently never delivered. Service credentials (POSTGRES_PASSWORD
    etc.) are intentionally not duplicated here; deployment_worker.py layers
    project_services.build_connection_env() on top of this dict afterwards, and that later
    `.update()` correctly wins over any same-named entry from here."""
    rows = db.query(Secret).filter(Secret.project_id == project.id).all()
    return {row.key: decrypt_secret(row.encrypted_value) for row in rows if row.encrypted_value}


# Default Dockerfile only when the agent forgot to write one - not a content template.
MIXED_DOCKERFILE = """FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nginx \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /var/www/html \\
    && if [ -d public ]; then cp -a public/. /var/www/html/; fi \\
    && rm -f /etc/nginx/sites-enabled/default \\
    && printf 'server {\\n    listen 80;\\n    root /var/www/html;\\n    index index.html;\\n}\\n' > /etc/nginx/conf.d/site.conf
RUN printf '#!/bin/sh\\nset -e\\nnginx\\nexec python app.py\\n' > /docker-entrypoint.sh \\
    && chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
"""


def ensure_project_artifact(db: Session, project: Project, prompt: str = "") -> Path:
    """Require agent-written project files. Never invent website/bot templates."""
    del prompt  # kept for call-site compatibility
    path = _project_dir(project.id)
    if not (path / "Dockerfile").exists():
        raise ArtifactError(
            "В проекте ещё нет кода агента (нет Dockerfile). "
            "Сначала соберите или допишите проект в чате."
        )
    needs_bot = project.type in ("telegram_bot", "mixed")
    if needs_bot and not _telegram_token(db, project):
        raise ArtifactError("Telegram bot requires TELEGRAM_BOT_TOKEN secret before deployment")
    return path


def _docker_build(path: Path, tag: str, *, on_log: LogCallback | None = None) -> None:
    """Build with streaming log chunks so the deployments UI can poll live progress."""
    client = docker.from_env()
    stream = client.api.build(
        path=str(path),
        tag=tag,
        rm=True,
        pull=False,
        decode=True,
    )
    chunks: list[str] = []
    last_flush = time.monotonic()
    pending = ""

    def _emit(text: str) -> None:
        nonlocal pending, last_flush
        if not text:
            return
        chunks.append(text)
        pending += text
        if on_log and (len(pending) >= 400 or time.monotonic() - last_flush >= 1.0):
            on_log(pending)
            pending = ""
            last_flush = time.monotonic()

    try:
        for item in stream:
            if not isinstance(item, dict):
                continue
            if item.get("stream"):
                _emit(str(item["stream"]))
            if item.get("status"):
                line = str(item["status"])
                if item.get("progress"):
                    line = f"{line} {item['progress']}"
                _emit(line + "\n")
            if item.get("error"):
                _emit(str(item["error"]) + "\n")
                raise BuildError(str(item["error"]), build_log=[{"stream": c} for c in chunks])
            detail = item.get("errorDetail")
            if detail:
                message = detail.get("message") if isinstance(detail, dict) else detail
                if message:
                    _emit(str(message) + "\n")
                    raise BuildError(str(message), build_log=[{"stream": c} for c in chunks])
    finally:
        if on_log and pending:
            on_log(pending)


def _build_error_message(exc: DockerException) -> str:
    """Prefer the error-bearing tail of the Docker build log for repair agents.

    Earlier code kept only the last 20 lines, which often dropped the pip/apt cause when
    the build log was noisy. Keep a generous tail so each repair attempt sees the real error.
    """
    if isinstance(exc, BuildError):
        details: list[str] = []
        for item in getattr(exc, "build_log", None) or []:
            if isinstance(item, dict):
                stream = item.get("stream") or item.get("error") or ""
                if stream:
                    details.append(str(stream).rstrip())
        if details:
            joined = "\n".join(details)
            return joined[-12_000:] if len(joined) > 12_000 else joined
    return str(exc)


def _normalize_requirements_before_build(path: Path) -> None:
    """Deterministic package-name fixes so agent typos cannot break pip (e.g. binary-binary)."""
    from src.services.agentic_artifacts import normalize_psycopg2_requirements

    normalize_psycopg2_requirements(path)


def try_build_project_image(project: Project) -> dict:
    """Raw build attempt against the project's current files, no AI auto-repair - backs the
    interactive build_project agent tool (src/services/agent/tools.py) so the agent can see a
    real build log and fix things itself mid-conversation, instead of only reacting to the
    automatic build-then-repair pipeline that runs after its turn ends (build_project_image
    below, which does include AI auto-repair)."""
    path = _project_dir(project.id)
    if not (path / "Dockerfile").exists():
        return {"ok": False, "log": "No Dockerfile in the project yet - write one before building."}
    tag = _image_tag(project)
    log_parts: list[str] = []
    try:
        with with_project_git_lock(project.id):
            _normalize_requirements_before_build(path)
            _docker_build(path, tag, on_log=log_parts.append)
    except DockerException as exc:
        return {"ok": False, "log": _build_error_message(exc) or "".join(log_parts)[-12_000:]}
    return {"ok": True, "log": f"Build succeeded: {tag}"}


def build_project_image(
    db: Session,
    project: Project,
    prompt: str = "",
    *,
    on_log: LogCallback | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the agent-written project. On failure, AI-repair only - never replace with a stub."""
    with with_project_git_lock(project.id):
        path = ensure_project_artifact(db, project, prompt)
        tag = _image_tag(project)
        _normalize_requirements_before_build(path)
        try:
            if on_log:
                on_log(f"Собираю Docker-образ {tag}…\n")
            _docker_build(path, tag, on_log=on_log)
        except DockerException as exc:
            last_error = _build_error_message(exc)
            if on_log:
                on_log(f"\nСборка не удалась:\n{last_error[-4000:]}\n")
            fixed = False

            from src.services.agentic_artifacts import (
                REPAIR_ATTEMPTS,
                normalize_psycopg2_requirements,
                repair_artifact_with_agent,
                unpin_missing_pip_versions,
            )

            # Deterministic fixes before LLM: package-name typos + invented pins.
            renamed = normalize_psycopg2_requirements(path)
            unpinned = unpin_missing_pip_versions(path, last_error)
            if renamed or unpinned:
                bits = []
                if renamed:
                    bits.append(f"normalized psycopg2 deps ({', '.join(renamed)})")
                if unpinned:
                    bits.append(f"unpinned hallucinated versions: {', '.join(unpinned)}")
                note = "; ".join(bits)
                project.logs = f"{project.logs}\n{note}".strip() if project.logs else note
                db.add(project)
                db.commit()
                if on_log:
                    on_log(f"{note}\nПовторяю сборку…\n")
                try:
                    _docker_build(path, tag, on_log=on_log)
                    fixed = True
                except DockerException as unpin_exc:
                    last_error = _build_error_message(unpin_exc)
                    if on_log:
                        on_log(f"\nСборка снова не удалась:\n{last_error[-4000:]}\n")

            if not fixed:
                for attempt in range(REPAIR_ATTEMPTS):
                    try:
                        if on_log:
                            on_log(f"\nAI исправляет ошибку сборки (попытка {attempt + 1})…\n")
                        repaired_path = repair_artifact_with_agent(
                            db, project, last_error, attempt=attempt + 1
                        )
                        db.commit()
                        _normalize_requirements_before_build(repaired_path)
                        if on_log:
                            on_log("Повторяю сборку после правок…\n")
                        _docker_build(repaired_path, tag, on_log=on_log)
                        fixed = True
                        break
                    except DockerException as retry_exc:
                        last_error = _build_error_message(retry_exc)
                        normalize_psycopg2_requirements(path)
                        unpin_missing_pip_versions(path, last_error)
                        if on_log:
                            on_log(f"\nСборка снова не удалась:\n{last_error[-4000:]}\n")
                    except ArtifactError as repair_exc:
                        if on_log:
                            on_log(f"\nАвтоисправление не удалось: {repair_exc}\n")
                        break

            if not fixed:
                raise ArtifactError(
                    "Docker image build failed after repair attempts; agent code was kept "
                    f"(templates are disabled):\n{last_error}"
                ) from exc

    environment = _all_secret_environment(db, project)
    if project.type in ("telegram_bot", "mixed") and not environment.get(TELEGRAM_BOT_TOKEN_KEY):
        raise ArtifactError("Telegram bot token is missing")
    return tag, environment
