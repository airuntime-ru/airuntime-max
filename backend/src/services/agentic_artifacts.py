from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from src.db.models.chat import Chat
from src.db.models.message import Message
from src.db.models.project import Project
from src.services.agent.async_utils import run_async
from src.services.agent.loop import CodingAgentSession
from src.services.agent.prompt import build_repair_prompt
from src.services.agent.tools import WorkspaceTools
from src.services.artifacts import (
    MIXED_DOCKERFILE,
    ArtifactError,
)
from src.services.project_git import commit_snapshot
from src.services.provider.factory import resolve_provider_and_model
from src.services.system_settings import resolve_platform_api_key
from src.services.workspace import project_dir as _project_dir

MANIFEST_VERSION = 2

WEBSITE_REQUIRED = "public/index.html"
TELEGRAM_REQUIRED = "app.py"

# How many times to hand a build failure back to the AI before giving up.
REPAIR_ATTEMPTS = 5

# How much of the Docker build log to include in each repair turn.
_REPAIR_ERROR_CHARS = 12_000

_DEFAULT_WEBSITE_DOCKERFILE = "FROM nginx:1.27-alpine\nCOPY public/ /usr/share/nginx/html/\n"
_DEFAULT_TELEGRAM_DOCKERFILE = "\n".join(
    [
        "FROM python:3.12-slim",
        "WORKDIR /app",
        "COPY requirements.txt .",
        "RUN pip install --no-cache-dir -r requirements.txt",
        "COPY . .",
        'CMD ["python", "app.py"]',
        "",
    ]
)

_STUB_MARKERS = (
    "временная заглушка платформы",
    "передам его в рабочий сценарий проекта",
    "режим заглушки",
)


_TOKEN_ENV_RE = re.compile(
    r"TELEGRAM_BOT_TOKEN|getenv\(\s*[\"']TELEGRAM_BOT_TOKEN|environ(?:\.get)?\(\s*[\"']TELEGRAM_BOT_TOKEN"
)

# pip: "No matching distribution found for psycopg2-binary==2.19.3"
_PIP_MISSING_PIN_RE = re.compile(
    r"(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+"
    r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s\\]+)",
    re.IGNORECASE,
)

# Capture package name as a whole token (not a substring of e.g. psycopg2cffi).
_REQ_LINE_RE = re.compile(
    r"^(\s*)([A-Za-z0-9][A-Za-z0-9._-]*)((?:\[[^\]]*\])?)(.*)$",
)
_PSYCOPG2_CANONICAL = "psycopg2-binary"
_PSYCOPG2_NAME_RE = re.compile(r"^psycopg2(?:-binary)+$", re.IGNORECASE)


def _normalize_psycopg2_requirement_line(line: str) -> str | None:
    """Return a fixed requirements line, or None if this line needs no change.

    Idempotent: already-correct `psycopg2-binary` is left alone. Never produces
    `psycopg2-binary-binary`. Collapses repeated `-binary` suffixes and rewrites bare
    `psycopg2` (needs pg_config on slim images) to unpinned `psycopg2-binary`.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith(("-e ", "-r ", "--", "http://", "https://", "git+")):
        return None

    match = _REQ_LINE_RE.match(line)
    if not match:
        return None

    prefix, name, extras, rest = match.groups()
    name_lower = name.lower()
    if name_lower == _PSYCOPG2_CANONICAL:
        return None
    if name_lower != "psycopg2" and not _PSYCOPG2_NAME_RE.fullmatch(name_lower):
        return None

    # Bare psycopg2, or psycopg2-binary-binary(+): canonicalize and drop ==pins
    # (agents often invent nonexistent versions when rewriting).
    comment = ""
    body = rest
    if "#" in body:
        body, after = body.split("#", 1)
        comment = f"#{after}"
    body = body.rstrip()
    if body.lstrip().startswith("=="):
        body = ""
    fixed = f"{prefix}{_PSYCOPG2_CANONICAL}{extras}{body}"
    if comment:
        fixed = f"{fixed.rstrip()}  {comment}"
    return fixed


def normalize_psycopg2_requirements(root: Path) -> list[str]:
    """Package-name-aware fix for psycopg2 / psycopg2-binary-binary in requirements.txt.

    Returns human-readable change notes (empty if nothing changed).
    """
    req_path = root / "requirements.txt"
    if not req_path.exists():
        return []
    original = req_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    notes: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        fixed = _normalize_psycopg2_requirement_line(line)
        if fixed is None:
            new_lines.append(line)
            continue
        new_lines.append(fixed)
        notes.append(f"{line.strip()} -> {fixed.strip()}")
    if not notes:
        return []
    req_path.write_text(
        "\n".join(new_lines) + ("\n" if original.endswith("\n") else ""), encoding="utf-8"
    )
    return notes


def unpin_missing_pip_versions(root: Path, build_error: str) -> list[str]:
    """Drop hallucinated ==pins that pip rejected so the next build can take the latest stable.

    Returns the package names that were unpinned (empty if nothing changed).
    """
    packages = {match.group(1) for match in _PIP_MISSING_PIN_RE.finditer(build_error or "")}
    if not packages:
        return []
    req_path = root / "requirements.txt"
    if not req_path.exists():
        return []
    original = req_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    changed: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue
        updated = line
        for package in packages:
            # Match "pkg==1.2.3" or "pkg[extra]==1.2.3" (optional whitespace around ==).
            pattern = re.compile(
                rf"^(\s*{re.escape(package)}(?:\[[^\]]*\])?)\s*==\s*[^\s#]+(.*)$",
                re.IGNORECASE,
            )
            match = pattern.match(line)
            if match:
                updated = f"{match.group(1)}{match.group(2)}"
                if package not in changed:
                    changed.append(package)
                break
        new_lines.append(updated)
    if not changed:
        return []
    req_path.write_text(
        "\n".join(new_lines) + ("\n" if original.endswith("\n") else ""), encoding="utf-8"
    )
    return changed


def _resolve_provider_and_key(provider_name: str | None = None) -> tuple[str, str, str]:
    name, model = resolve_provider_and_model(provider_override=provider_name)
    api_key = resolve_platform_api_key(name) or ""
    return name, model, api_key


def _write_manifest(project: Project, root: Path, *, source: str) -> None:
    meta_dir = root / ".airuntime"
    meta_dir.mkdir(exist_ok=True)
    from src.services.workspace import list_workspace_files

    (meta_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "source": source,
                "project_id": str(project.id),
                "project_type": project.type,
                "files": list_workspace_files(root),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def workspace_has_agent_code(root: Path, project: Project) -> bool:
    """True when the agent already wrote real project files we must not wipe with a stub."""
    needs_website = project.type in ("website", "mixed")
    needs_bot = project.type in ("telegram_bot", "mixed")

    if needs_bot and (root / TELEGRAM_REQUIRED).exists():
        text = (root / TELEGRAM_REQUIRED).read_text(encoding="utf-8", errors="ignore")
        if not any(marker in text for marker in _STUB_MARKERS) and len(text.strip()) > 80:
            return True
        # Multi-module bot: entrypoint may be thin, other files hold logic.
        py_files = [p for p in root.rglob("*.py") if ".airuntime" not in p.parts]
        if len(py_files) > 1 and not any(marker in text for marker in _STUB_MARKERS):
            return True

    if needs_website and (root / WEBSITE_REQUIRED).exists():
        text = (root / WEBSITE_REQUIRED).read_text(encoding="utf-8", errors="ignore")
        if len(text.strip()) > 40:
            return True

    return False


def _reads_telegram_token(root: Path) -> bool:
    for path in root.rglob("*.py"):
        if ".airuntime" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _TOKEN_ENV_RE.search(text):
            return True
    return False


def _bot_python_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if ".airuntime" not in p.parts and "__pycache__" not in p.parts and ".git" not in p.parts
    ]


# Soft signal: a single huge app.py almost never matches a feature-rich user request.
_THIN_BOT_APP_LINES = 80


def thin_bot_architecture_warning(root: Path, project: Project) -> str | None:
    """Warn when a non-trivial bot was collapsed into a single app.py monolith.

    Does not fail the deploy contract (hello-world may be one file). Used for chat
    verify messaging and mid-loop build_project hints so the agent is not rewarded
    for "app.py + requirements.txt + Dockerfile" on real products.
    """
    if project.type not in ("telegram_bot", "mixed"):
        return None
    py_files = _bot_python_files(root)
    if len(py_files) > 1:
        return None
    app_path = root / TELEGRAM_REQUIRED
    if not app_path.exists():
        return None
    try:
        lines = len(app_path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return None
    if lines < _THIN_BOT_APP_LINES:
        return None
    return (
        "Архитектура слишком тонкая: почти вся логика в одном app.py "
        f"(~{lines} строк) без отдельных модулей. Для нетривиальных фич разнеси код "
        "(handlers/, services/, db/ и т.п.), оставь app.py тонким entrypoint и "
        "убедись, что Dockerfile делает `COPY . .`."
    )


def ensure_dockerfile(project: Project, root: Path) -> bool:
    """Write a default Dockerfile if missing. Returns True when a file was created."""
    dockerfile = root / "Dockerfile"
    if dockerfile.exists():
        return False
    needs_website = project.type in ("website", "mixed")
    needs_bot = project.type in ("telegram_bot", "mixed")
    if needs_website and needs_bot:
        dockerfile.write_text(MIXED_DOCKERFILE, encoding="utf-8")
    elif needs_website:
        dockerfile.write_text(_DEFAULT_WEBSITE_DOCKERFILE, encoding="utf-8")
    else:
        dockerfile.write_text(_DEFAULT_TELEGRAM_DOCKERFILE, encoding="utf-8")
    return True


def ensure_required_files(project: Project, root: Path) -> None:
    """Ensure deploy contract files exist; raise if a required entry file is missing.

    Platform contract for bots: entrypoint app.py (Docker CMD), TELEGRAM_BOT_TOKEN
    read from env somewhere in the tree, requirements.txt, and a Dockerfile that
    copies the whole project (`COPY . .`). This does NOT mean «one-file bot is
    enough» — architecture quality is steered by the system prompt and soft
    warnings from thin_bot_architecture_warning / build_project hints. Hello-world
    may be a small app.py; feature-rich bots should be multi-module.
    """

    needs_website = project.type in ("website", "mixed")
    needs_bot = project.type in ("telegram_bot", "mixed")
    if not needs_website and not needs_bot:
        raise ArtifactError(f"Unsupported project type: {project.type}")

    if needs_website and not (root / WEBSITE_REQUIRED).exists():
        raise ArtifactError(
            f"Agent did not produce the required {WEBSITE_REQUIRED} - website has no content"
        )

    if needs_bot:
        if not (root / TELEGRAM_REQUIRED).exists():
            raise ArtifactError(
                f"Agent did not produce the required entrypoint {TELEGRAM_REQUIRED} "
                "(thin launcher that Docker runs - not a license to put all logic in one file)"
            )
        if not _reads_telegram_token(root):
            raise ArtifactError(
                "Bot code must read TELEGRAM_BOT_TOKEN from the environment "
                "(os.environ / os.getenv)"
            )
        requirements_path = root / "requirements.txt"
        if not requirements_path.exists():
            raise ArtifactError(
                "requirements.txt is missing - list the bot framework and other dependencies"
            )

    ensure_dockerfile(project, root)
    if needs_bot:
        dockerfile = root / "Dockerfile"
        if dockerfile.exists():
            text = dockerfile.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*COPY\s+app\.py\b", text, re.MULTILINE) and "COPY . ." not in text:
                # Agent wrote a monolith-only Docker layer - rewrite to full-tree copy so
                # any modules they add later actually ship in the image.
                if needs_website:
                    dockerfile.write_text(MIXED_DOCKERFILE, encoding="utf-8")
                else:
                    dockerfile.write_text(_DEFAULT_TELEGRAM_DOCKERFILE, encoding="utf-8")


async def _run_agent_repair(project: Project, root: Path, build_error: str, *, attempt: int) -> str:
    provider_name, model, api_key = _resolve_provider_and_key()
    if not api_key:
        raise ArtifactError("No AI provider key configured for automatic repair")

    workspace = WorkspaceTools(root, project_id=str(project.id))
    session = CodingAgentSession(
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        workspace=workspace,
        system_prompt=build_repair_prompt(project),
    )
    error_body = (build_error or "")[-_REPAIR_ERROR_CHARS:]
    user_message = (
        f"Сборка Docker-образа этого проекта упала с ошибкой (попытка исправления {attempt}):\n\n"
        f"{error_body}\n\n"
        "Это актуальный лог ПОСЛЕДНЕЙ неудачной сборки. Прочитай requirements.txt и файлы "
        "из трассировки заново, внеси правки на диске и вызови build_project. "
        "Не заменяй проект шаблоном и не удаляй реализованную логику пользователя."
    )
    final_text = ""
    from src.services.agent.events import AgentDone, TextDelta

    async for event in session.run(history=[], user_message=user_message):
        if isinstance(event, TextDelta):
            final_text += event.text
        elif isinstance(event, AgentDone) and event.reason == "error":
            raise ArtifactError(f"AI repair failed: {event.error}")
    return final_text


def _append_chat_note(db: Session, project: Project, text: str) -> None:
    """Surface repair progress in chat (not project.logs)."""
    if not text.strip():
        return
    chat = (
        db.query(Chat)
        .filter(Chat.project_id == project.id)
        .order_by(Chat.created_at.desc())
        .first()
    )
    if not chat:
        return
    db.add(Message(chat_id=chat.id, role="assistant", content_markdown=text.strip()))


def repair_artifact_with_agent(
    db: Session, project: Project, build_error: str, *, attempt: int = 1
) -> Path:
    """Feed the real Docker build error back to the coding agent and let it patch the
    specific files that are broken, instead of throwing the whole project away."""

    root = _project_dir(project.id)
    summary = run_async(_run_agent_repair(project, root, build_error, attempt=attempt))
    ensure_required_files(project, root)
    _write_manifest(project, root, source="ai-repair")
    try:
        commit_snapshot(root, message=f"AI repair attempt {attempt}: {project.name}")
    except Exception:  # noqa: BLE001 - git snapshotting is best-effort
        pass
    note = (
        f"Исправил ошибку сборки (попытка {attempt}): {summary.strip()}"
        if summary.strip()
        else f"Внёс правки после ошибки сборки (попытка {attempt})."
    )
    _append_chat_note(db, project, note)
    db.add(project)
    return root
