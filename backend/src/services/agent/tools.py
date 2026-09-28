"""Workspace tools exposed to the coding agent.

Deliberately small and explicit (per the platform's security architecture:
"LLM tool execution allowlist - explicitly registered tools only"). There is
no generic shell/exec tool here: the model can inspect and edit files, and
separately the build/test step (src/services/artifacts.py) actually builds
the project in Docker and reports back real errors. Keeping "edit" and
"execute" as separate stages keeps the blast radius of a single tool call
small and auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.services.workspace import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    WorkspaceError,
    list_workspace_files,
    resolve_in_workspace,
    workspace_size_bytes,
)

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": (
            "List files that currently exist in the project workspace. Always call this "
            "before writing files so you know what already exists and avoid clobbering it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative subdirectory to list, default '.' (whole project).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": "Read the full current contents of one file in the project workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path."}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file, or completely replace the contents of an existing file. "
            "Use this for new files or full rewrites; use edit_file for small, targeted changes "
            "to an existing file so you don't have to resend the whole file. For non-trivial "
            "Telegram bots prefer many small module files (handlers/, services/, db/, …) plus a "
            "thin app.py entrypoint - do not dump an entire product into a single app.py."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path."},
                "content": {"type": "string", "description": "Full UTF-8 file content."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a precise, incremental edit to an existing file by replacing one exact, "
            "unique occurrence of old_text with new_text. Fails if old_text is not found or "
            "is not unique in the file - in that case include more surrounding context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path."},
                "old_text": {"type": "string", "description": "Exact existing snippet to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the project workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path."}},
            "required": ["path"],
        },
    },
    {
        "name": "generate_image",
        "description": (
            "Generate a PNG image with RouterAI (DALL-E / GPT-Image) and save it into the "
            "project workspace. Use for logos, hero photos, product mockups, bot avatars, "
            "Open Graph images, and other visual assets a website or bot needs. After saving, "
            "reference the relative path from HTML/CSS/Telegram assets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative output path, e.g. static/images/hero.png",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed art direction for the image.",
                },
                "size": {
                    "type": "string",
                    "description": "1024x1024 (default), 1536x1024, or 1024x1536",
                },
            },
            "required": ["path", "prompt"],
        },
    },
    {
        "name": "generate_pdf",
        "description": (
            "Generate a simple PDF document from plain text/markdown-ish content and save it "
            "into the project workspace. Use for downloadable brochures, price lists, bot "
            "attachments, invoices, or static site assets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative output path, e.g. static/docs/price-list.pdf",
                },
                "title": {
                    "type": "string",
                    "description": "Document title shown on the first page.",
                },
                "content": {
                    "type": "string",
                    "description": "Body text. Use blank lines between sections.",
                },
            },
            "required": ["path", "title", "content"],
        },
    },
    {
        "name": "build_project",
        "description": (
            "Build a real Docker image from the project's current files right now, so you can "
            "see actual build errors (missing dependency, syntax error, wrong path, bad base "
            "image, etc.) and fix them yourself before ending your turn - instead of only "
            "finding out after the automatic build attempt that happens once you're done. Can "
            "take up to a few minutes (base image pulls). Call it once your files are ready to "
            "try, read the returned log, and keep fixing + rebuilding until it succeeds or "
            "you're confident the remaining issue needs the user's input (e.g. a missing "
            "secret)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "preview_project",
        "description": (
            "Open the already-built project in an isolated headless browser, scroll the "
            "page, and check it for real problems before you finish: console/network "
            "errors, broken images, horizontal overflow, and what the headings/buttons/"
            "visible text actually say. Requires a successful build_project first. Only "
            "works for website/mixed projects - there is nothing to render for a bot-only "
            "project. Call this before ending your turn on any non-trivial website change - "
            "it catches things a build check cannot, like leftover placeholder text, a "
            "broken layout, or a picture that doesn't match the brief. Screenshots are "
            "stored as internal artifacts, not shown to you directly - read the returned "
            "findings (visible_text_sample, broken_images, overflow_elements, etc.) instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Same-origin relative paths to check, e.g. ['/', '/services.html']. "
                        "Must start with '/' - no external URLs, no host/scheme. Defaults to "
                        "['/'] if omitted. Max 5."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "request_secret",
        "description": (
            "Ask the platform to reserve a secret slot (API key, token, credential) that this "
            "project's code will read from an environment variable. This does NOT ask the user "
            "for the value in chat - it creates an empty, named slot that the user fills in "
            "themselves in the project's Settings page, where the platform can validate it. "
            "Call this as soon as you know the project needs a credential you don't have, "
            "instead of asking for it as chat text and instead of inventing/hardcoding a value. "
            "Safe to call again for the same key - it won't overwrite an existing value.\n"
            "IMPORTANT - never use this for ANY credential belonging to a service YOU are "
            "provisioning via request_service (a database, cache, queue, etc. you're creating "
            "for this project) - that includes not just the full connection string "
            "(DATABASE_URL, REDIS_URL, ...) but every piece of it: its password, username, "
            "host, port, database name - all of it (e.g. POSTGRES_PASSWORD, MYSQL_ROOT_PASSWORD, "
            "REDIS_PASSWORD are all wrong here). request_service already generates and wires up "
            "100% of that on its own; there is nothing left for the user to provide, ever, for a "
            "service you created yourself. Only use request_secret for something you genuinely "
            "cannot create yourself: a third-party API key, a payment gateway secret, or a "
            "database/service the user explicitly says already exists elsewhere and that they "
            "will connect this project to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "UPPER_SNAKE_CASE environment variable name the code reads, e.g. "
                        "TELEGRAM_BOT_TOKEN or STRIPE_SECRET_KEY."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "One short sentence shown to the user explaining what it's for.",
                },
            },
            "required": ["key", "reason"],
        },
    },
    {
        "name": "request_service",
        "description": (
            "Ask the platform to provision a real backing service (database, cache, queue, "
            "search index - anything that runs as its own container) for this project. For "
            "feature-rich sites (auth, cabinet, multi-user, booking, history, admin) prefer "
            "postgres via this tool as the default path - do not default to SQLite-in-container. "
            "SQLite is fine only for tiny/local prototypes. Call this instead of writing a "
            "docker-compose.yml or assuming an external service already exists - the platform "
            "starts the container for you on a private network reachable from your app. For "
            "'postgres'/'redis'/'mysql'/'mongo'/'rabbitmq', just pass kind - the platform picks "
            "a sensible image, GENERATES THE PASSWORD/USERNAME ITSELF, and injects a ready "
            "connection-string env var (DATABASE_URL/REDIS_URL/MONGO_URL/RABBITMQ_URL) your code "
            "should read, not invent. Nothing about this service is left for the user to fill "
            "in - do not also call request_secret for its password or any other piece of it. "
            "For anything else (Elasticsearch, ClickHouse, a Celery worker built from "
            "this same project's own Dockerfile, or any other image), pass kind as a short "
            "label plus `image` explicitly - the tool result tells you the exact hostname to "
            "connect to; use that hostname with whatever port/credentials you configure via "
            "`env`, since there's no auto-generated connection string for a custom image. Safe "
            "to call again for the same kind - it won't create a duplicate or change an "
            "existing service's config. This is the only correct way to give the project a "
            "database/cache/queue it doesn't already have - never ask the user (via "
            "request_secret or chat text) to supply a connection string for something you're "
            "provisioning yourself; most users aren't technical and won't know what that means."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "Short label identifying the service, e.g. postgres, redis, rabbitmq, "
                        "search, worker. Used to build its hostname/container name."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "One short sentence: why this project needs it.",
                },
                "image": {
                    "type": "string",
                    "description": (
                        "Docker image to run, e.g. 'elasticsearch:8.15.0' or "
                        "'rabbitmq:3-management'. Required unless kind is one of the built-in "
                        "presets (postgres/redis/mysql/mongo/rabbitmq)."
                    ),
                },
                "env": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Extra environment variables to set on the service's own container "
                        "(its config, not your app's) - e.g. credentials or settings that "
                        "image's docs call for. Not needed for the built-in presets unless you "
                        "want to override their defaults."
                    ),
                },
                "data_path": {
                    "type": "string",
                    "description": (
                        "Absolute path inside the service's container where it stores data, "
                        "e.g. '/var/lib/rabbitmq' - the platform bind-mounts a persistent host "
                        "directory under AIRUNTIME_VOLUMES_DIR so data survives rebuilds. "
                        "Omit for services that don't need persistence, or for a preset kind "
                        "(already knows its own path)."
                    ),
                },
            },
            "required": ["kind", "reason"],
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOL_DEFS}


@dataclass
class ToolExecutionResult:
    ok: bool
    summary: str
    content: str = ""


@dataclass
class ServiceRequest:
    kind: str
    reason: str
    image: str | None = None
    env: dict[str, str] | None = None
    data_path: str | None = None


class WorkspaceTools:
    """Executes agent tool calls against one project's workspace directory."""

    def __init__(
        self, root: Path, *, project_id: str | None = None, api_key: str | None = None
    ) -> None:
        self.root = root
        self.project_id = project_id
        self.api_key = api_key
        self.touched_files: set[str] = set()
        self.requested_secrets: list[tuple[str, str]] = []
        self.requested_services: list[ServiceRequest] = []
        # None = build_project was never observed this turn (e.g. the Codex shell path,
        # which builds via `docker build` directly rather than this tool). True/False once a
        # build_project call has returned - product_pipeline.py uses this to skip preview/
        # review after an explicit failed build instead of re-deriving build state itself.
        # Orchestration's coding executors also lift this (and last_build_result) into
        # AgentExecutionResult.build_result so validation.py's required "build" step has
        # real evidence when the agent already ran build_project mid-turn.
        self.build_succeeded: bool | None = None
        self.last_build_result: dict | None = None

    def call(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        try:
            if name == "list_files":
                return self._list_files(arguments.get("path", "."))
            if name == "read_file":
                return self._read_file(str(arguments.get("path", "")))
            if name == "write_file":
                return self._write_file(
                    str(arguments.get("path", "")), arguments.get("content", "")
                )
            if name == "edit_file":
                return self._edit_file(
                    str(arguments.get("path", "")),
                    arguments.get("old_text", ""),
                    arguments.get("new_text", ""),
                )
            if name == "delete_file":
                return self._delete_file(str(arguments.get("path", "")))
            if name == "generate_image":
                return self._generate_image(
                    str(arguments.get("path", "")),
                    str(arguments.get("prompt", "")),
                    str(arguments.get("size", "") or "1024x1024"),
                )
            if name == "generate_pdf":
                return self._generate_pdf(
                    str(arguments.get("path", "")),
                    str(arguments.get("title", "")),
                    str(arguments.get("content", "")),
                )
            if name == "build_project":
                return self._build_project()
            if name == "preview_project":
                paths = arguments.get("paths") if isinstance(arguments, dict) else None
                return self._preview_project(paths if isinstance(paths, list) else None)
            if name == "request_secret":
                return self._request_secret(
                    str(arguments.get("key", "")), str(arguments.get("reason", ""))
                )
            if name == "request_service":
                return self._request_service(
                    str(arguments.get("kind", "")),
                    str(arguments.get("reason", "")),
                    image=arguments.get("image"),
                    env=arguments.get("env"),
                    data_path=arguments.get("data_path"),
                )
            return ToolExecutionResult(ok=False, summary=f"Unknown tool: {name}")
        except WorkspaceError as exc:
            return ToolExecutionResult(ok=False, summary=str(exc))
        except Exception as exc:  # defensive: never let a tool crash the agent loop
            return ToolExecutionResult(ok=False, summary=f"Tool error: {exc}")

    def _list_files(self, path: str) -> ToolExecutionResult:
        if path and path != ".":
            base = resolve_in_workspace(self.root, path)
            if not base.exists():
                return ToolExecutionResult(
                    ok=True, summary="Directory does not exist yet.", content="[]"
                )
            files = [
                p.relative_to(self.root).as_posix()
                for p in sorted(base.rglob("*"))
                if p.is_file() and ".git" not in p.parts and ".airuntime" not in p.parts
            ]
        else:
            files = list_workspace_files(self.root)
        content = "\n".join(files) if files else "(empty workspace)"
        return ToolExecutionResult(ok=True, summary=f"{len(files)} file(s)", content=content)

    def _read_file(self, path: str) -> ToolExecutionResult:
        target = resolve_in_workspace(self.root, path)
        if not target.exists() or not target.is_file():
            return ToolExecutionResult(ok=False, summary=f"File not found: {path}")
        data = target.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            return ToolExecutionResult(
                ok=False, summary=f"File is too large to read ({len(data)} bytes)"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolExecutionResult(ok=False, summary="File is not valid UTF-8 text")
        return ToolExecutionResult(
            ok=True, summary=f"Read {path} ({len(data)} bytes)", content=text
        )

    def _write_file(self, path: str, content: Any) -> ToolExecutionResult:
        if not isinstance(content, str):
            return ToolExecutionResult(ok=False, summary="content must be a string")
        target = resolve_in_workspace(self.root, path)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            return ToolExecutionResult(ok=False, summary=f"File too large ({len(encoded)} bytes)")
        current_total = workspace_size_bytes(self.root)
        existing = target.stat().st_size if target.exists() else 0
        if current_total - existing + len(encoded) > MAX_TOTAL_BYTES:
            return ToolExecutionResult(ok=False, summary="Project workspace size limit exceeded")
        existing_files = set(list_workspace_files(self.root))
        rel = target.relative_to(self.root).as_posix()
        if rel not in existing_files and len(existing_files) >= MAX_FILES:
            return ToolExecutionResult(
                ok=False, summary=f"Too many files in project (max {MAX_FILES})"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.touched_files.add(rel)
        return ToolExecutionResult(ok=True, summary=f"Wrote {rel} ({len(encoded)} bytes)")

    def _edit_file(self, path: str, old_text: Any, new_text: Any) -> ToolExecutionResult:
        if not isinstance(old_text, str) or not old_text:
            return ToolExecutionResult(ok=False, summary="old_text must be a non-empty string")
        if not isinstance(new_text, str):
            return ToolExecutionResult(ok=False, summary="new_text must be a string")
        target = resolve_in_workspace(self.root, path)
        if not target.exists():
            return ToolExecutionResult(
                ok=False, summary=f"File not found: {path}. Use write_file to create it first."
            )
        current = target.read_text(encoding="utf-8")
        count = current.count(old_text)
        if count == 0:
            return ToolExecutionResult(
                ok=False,
                summary="old_text not found in file - re-read the file, it may have changed",
            )
        if count > 1:
            return ToolExecutionResult(
                ok=False,
                summary=f"old_text is not unique ({count} occurrences) - include more context",
            )
        updated = current.replace(old_text, new_text, 1)
        encoded = updated.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            return ToolExecutionResult(ok=False, summary="Resulting file would be too large")
        target.write_text(updated, encoding="utf-8")
        rel = target.relative_to(self.root).as_posix()
        self.touched_files.add(rel)
        return ToolExecutionResult(ok=True, summary=f"Edited {rel}")

    def _build_project(self) -> ToolExecutionResult:
        if not self.project_id:
            return ToolExecutionResult(
                ok=False, summary="Build tool unavailable outside a project context"
            )
        from src.services.docker_control_queue import submit_control_job

        result = submit_control_job(
            action="build_check", project_id=self.project_id, timeout_seconds=180
        )
        if result is None:
            return ToolExecutionResult(
                ok=False, summary="Build service did not respond - try again"
            )
        log = result.get("log", "")
        arch_hint = self._thin_bot_architecture_hint()
        self.last_build_result = {
            "ok": bool(result.get("ok")),
            "log": log,
            "log_tail": (log or "")[-4000:],
        }
        if result.get("ok"):
            self.build_succeeded = True
            content = log + arch_hint if arch_hint else log
            summary = "Build succeeded"
            if arch_hint:
                summary = (
                    "Build succeeded, but architecture looks too thin - split into modules "
                    "before finishing"
                )
            return ToolExecutionResult(ok=True, summary=summary, content=content)
        self.build_succeeded = False
        content = log + arch_hint if arch_hint else log
        return ToolExecutionResult(ok=False, summary="Build failed", content=content)

    @staticmethod
    def sanitize_preview_paths(paths: list[Any] | None) -> list[str]:
        """Only same-origin relative paths ever reach the browser - never a scheme, host, or
        parent traversal. One bad entry is dropped rather than failing the whole call. Reused
        as-is by preview_runner.py (the worker-side enforcement point) so both layers agree."""
        cleaned: list[str] = []
        for raw in paths or ["/"]:
            if not isinstance(raw, str):
                continue
            candidate = raw.strip()
            if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
                continue
            if ".." in candidate or "\\" in candidate or "://" in candidate:
                continue
            cleaned.append(candidate)
            if len(cleaned) >= 5:
                break
        return cleaned or ["/"]

    def _preview_project(self, paths: list[Any] | None) -> ToolExecutionResult:
        if not self.project_id:
            return ToolExecutionResult(
                ok=False, summary="Preview tool unavailable outside a project context"
            )
        from src.core.config import settings
        from src.services.docker_control_queue import submit_control_job

        safe_paths = self.sanitize_preview_paths(paths)
        timeout = max(30, int(settings.preview_timeout_seconds) + 30)
        result = submit_control_job(
            action="preview",
            project_id=self.project_id,
            timeout_seconds=timeout,
            extra={"paths": safe_paths},
        )
        if result is None:
            return ToolExecutionResult(
                ok=False, summary="Preview service did not respond - try again"
            )
        if "status" not in result:
            # docker_control_actions.py's generic failure envelope ({"ok": False, "error":
            # ...}), e.g. a Docker error before preview_runner could even start.
            return ToolExecutionResult(
                ok=False,
                summary=str(result.get("error") or "Preview failed"),
                content=json.dumps(result, ensure_ascii=False),
            )
        status = str(result.get("status") or "failed")
        content = json.dumps(result, ensure_ascii=False)
        if status == "failed":
            fatals = [str(item) for item in (result.get("fatal_errors") or [])]
            detail = "; ".join(fatals[:3]) if fatals else "Preview failed"
            return ToolExecutionResult(ok=False, summary=detail[:200], content=content)
        return ToolExecutionResult(ok=True, summary=f"Preview {status}", content=content)

    def _thin_bot_architecture_hint(self) -> str:
        """Nudge mid-loop when the agent is about to ship a monolith app.py."""
        py_files = [
            p
            for p in self.root.rglob("*.py")
            if ".airuntime" not in p.parts
            and "__pycache__" not in p.parts
            and ".git" not in p.parts
        ]
        if len(py_files) > 1:
            return ""
        app_path = self.root / "app.py"
        if not app_path.exists():
            return ""
        try:
            lines = len(app_path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            return ""
        if lines < 80:
            return ""
        return (
            "\n\nARCHITECTURE HINT: app.py is large (~"
            f"{lines} lines) and is the only Python file. For non-trivial bots this is wrong - "
            "split handlers/services/db into separate modules, keep app.py as a thin entrypoint, "
            "ensure Dockerfile uses `COPY . .`, then call build_project again. Do not finish "
            "with only app.py + requirements.txt + Dockerfile."
        )

    def _request_secret(self, key: str, reason: str) -> ToolExecutionResult:
        key = key.strip()
        if not key:
            return ToolExecutionResult(ok=False, summary="key must not be empty")
        from src.services.project_services import is_platform_managed_secret_key

        if is_platform_managed_secret_key(key):
            return ToolExecutionResult(
                ok=False,
                summary=(
                    f"{key} is created by request_service, not filled in by the user. "
                    "Call request_service for postgres/redis/mysql/mongo/rabbitmq and read "
                    "the injected DATABASE_URL/REDIS_URL from the environment."
                ),
            )
        self.requested_secrets.append((key, reason.strip()))
        return ToolExecutionResult(
            ok=True,
            summary=f"Requested secret {key} - the user will fill in the value in Settings",
        )

    def _request_service(
        self,
        kind: str,
        reason: str,
        *,
        image: Any = None,
        env: Any = None,
        data_path: Any = None,
    ) -> ToolExecutionResult:
        from src.services.project_services import (
            container_name_for,
            is_known_preset,
            normalize_service_kind,
        )

        normalized_kind = normalize_service_kind(kind)
        resolved_image = image.strip() if isinstance(image, str) and image.strip() else None
        if not resolved_image and not is_known_preset(normalized_kind):
            return ToolExecutionResult(
                ok=False,
                summary=(
                    f"Unknown service kind '{normalized_kind}' - call again with an explicit "
                    "image, e.g. image='elasticsearch:8.15.0'"
                ),
            )
        resolved_env = env if isinstance(env, dict) else None
        resolved_data_path = (
            data_path.strip() if isinstance(data_path, str) and data_path.strip() else None
        )

        self.requested_services.append(
            ServiceRequest(
                kind=normalized_kind,
                reason=reason.strip(),
                image=resolved_image,
                env=resolved_env,
                data_path=resolved_data_path,
            )
        )
        hostname = (
            container_name_for(self.project_id, normalized_kind)
            if self.project_id
            else normalized_kind
        )
        return ToolExecutionResult(
            ok=True,
            summary=(
                f"Requested service '{normalized_kind}' - will be reachable at host "
                f"'{hostname}' once deployed"
            ),
        )

    def _generate_image(self, path: str, prompt: str, size: str) -> ToolExecutionResult:
        from src.services.media_generation import generate_image_bytes

        if not prompt.strip():
            return ToolExecutionResult(ok=False, summary="prompt is required")
        target = resolve_in_workspace(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            data, content_type = generate_image_bytes(
                prompt=prompt,
                api_key=self.api_key,
                size=size or "1024x1024",
            )
        except Exception as exc:
            return ToolExecutionResult(ok=False, summary=f"Image generation failed: {exc}")
        if len(data) > MAX_FILE_BYTES:
            return ToolExecutionResult(
                ok=False, summary="Generated image exceeds workspace file limit"
            )
        target.write_bytes(data)
        rel = target.relative_to(self.root).as_posix()
        self.touched_files.add(rel)
        return ToolExecutionResult(
            ok=True,
            summary=f"Generated image saved to {rel} ({content_type}, {len(data)} bytes)",
            content=rel,
        )

    def _generate_pdf(self, path: str, title: str, content: str) -> ToolExecutionResult:
        from src.services.media_generation import generate_pdf_bytes

        target = resolve_in_workspace(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = generate_pdf_bytes(title=title, content=content)
        except Exception as exc:
            return ToolExecutionResult(ok=False, summary=f"PDF generation failed: {exc}")
        if len(data) > MAX_FILE_BYTES:
            return ToolExecutionResult(
                ok=False, summary="Generated PDF exceeds workspace file limit"
            )
        target.write_bytes(data)
        rel = target.relative_to(self.root).as_posix()
        self.touched_files.add(rel)
        return ToolExecutionResult(
            ok=True,
            summary=f"Generated PDF saved to {rel} ({len(data)} bytes)",
            content=rel,
        )

    def _delete_file(self, path: str) -> ToolExecutionResult:
        target = resolve_in_workspace(self.root, path)
        if not target.exists():
            return ToolExecutionResult(ok=False, summary=f"File not found: {path}")
        target.unlink()
        rel = target.relative_to(self.root).as_posix()
        self.touched_files.add(rel)
        return ToolExecutionResult(ok=True, summary=f"Deleted {rel}")
