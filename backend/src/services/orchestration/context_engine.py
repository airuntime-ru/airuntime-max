"""Assembles the three tiers of context the spec draws a hard line between:

  - GLOBAL context (orchestrator-only): full request, plan, all task results, git/build/deploy
    history, secret/service inventory. Never handed to an executor - only used by planner.py /
    replanner.py and summarized into OrchestrationRun.context_summary for observability.
  - TASK context (specialist agent): a few sentences of project goal, the task's own goal,
    dependency results, a *small* set of relevant files, structured errors, acceptance
    criteria. This is what contract_builder.py folds into TaskContract.relevant_context /
    .relevant_files / .dependency_results / .current_state.
  - TOOL context (skill/MCP capability): project-scoped identifiers only, built ad hoc by
    capability_router.py/executors.py - deliberately not a heavy object here.

Everything in this module is pure/deterministic (string/file/DB assembly, no LLM calls) so it
needs no fake-provider plumbing to unit test, and its output is safe to log wholesale (secret
VALUES never pass through here - `secrets_inventory`/`services_inventory` return key
names/kinds only, and `redact()` is applied to every piece of raw log/error text before it
becomes a ContextItem, closing the gap found in deployment_check.py where runtime logs
containing live secret env values were fed verbatim into repair prompts).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.db.models.project import Project
from src.db.models.project_service import ProjectService
from src.db.models.secret import Secret
from src.services.orchestration.schemas import (
    ContextItem,
    DependencyResult,
    FileReference,
    ProjectStateSummary,
)
from src.services.prompt_guard import redact_secrets

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def clip_text(text: str, *, max_chars: int, keep: str = "tail") -> str:
    """Deterministic clipping with a visible marker - mirrors prompt_guard's
    prepare_agent_user_message but generic (that one is chat-message-specific)."""
    if len(text) <= max_chars:
        return text
    marker = f"[...clipped {len(text) - max_chars} chars...]"
    if keep == "head":
        return text[:max_chars] + marker
    return marker + text[-max_chars:]


def redact(text: str) -> str:
    """Defense-in-depth secret-value scrub for any raw log/error text before it becomes
    context. `secrets_inventory`/`services_inventory` below never carry values in the first
    place - this exists for text whose *origin* is outside our control, e.g. a crashed
    container's stdout that echoed `os.environ`."""
    return redact_secrets(text or "")


def content_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass(frozen=True)
class StructuredError:
    error_type: str
    summary: str
    file_hints: list[str]
    raw_excerpt: str


_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):")
_PY_EXC_RE = re.compile(r"^(\S*(?:Error|Exception)): (.*)$", re.MULTILINE)
_FILE_HINT_RE = re.compile(
    r'File "([^"]+)"|(?:^|\s)([\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|yml|yaml)):\d+', re.MULTILINE
)
_NPM_ERR_RE = re.compile(r"^npm ERR! (.*)$", re.MULTILINE)
_DOCKER_ERR_RE = re.compile(r"(?i)error response from daemon: (.*)")


def extract_structured_error(
    log_text: str, *, max_excerpt_chars: int = 4000
) -> StructuredError | None:
    """Deterministic, dependency-free structured-error extraction, in the same spirit as
    deployment_check.py's `detect_runtime_errors` regexes but returning a typed, file-hinted
    summary instead of a bare bool, for build_error/deploy_error/runtime_error ContextItems."""
    if not log_text or not log_text.strip():
        return None
    text = redact(log_text)

    error_type = "unknown"
    summary = ""
    if _TRACEBACK_RE.search(text):
        error_type = "python_traceback"
        match = list(_PY_EXC_RE.finditer(text))
        summary = match[-1].group(0) if match else "Traceback (see excerpt)"
    elif (npm_match := _NPM_ERR_RE.search(text)) is not None:
        error_type = "npm_error"
        summary = npm_match.group(1)
    elif (docker_match := _DOCKER_ERR_RE.search(text)) is not None:
        error_type = "docker_error"
        summary = docker_match.group(1)
    elif "CRITICAL" in text or re.search(r"\bERROR\b", text):
        error_type = "generic_error"
        line = next(
            (line for line in text.splitlines() if "ERROR" in line or "CRITICAL" in line), ""
        )
        summary = line.strip()
    else:
        return None

    file_hints: list[str] = []
    for match in _FILE_HINT_RE.finditer(text):
        hint = match.group(1) or match.group(2)
        if hint and hint not in file_hints:
            file_hints.append(hint)
        if len(file_hints) >= 8:
            break

    return StructuredError(
        error_type=error_type,
        summary=summary[:500] or "(see excerpt)",
        file_hints=file_hints,
        raw_excerpt=clip_text(text, max_chars=max_excerpt_chars),
    )


_WORD_RE = re.compile(r"[a-zA-Z0-9]+")  # no underscore: splits snake_case paths into real words


def select_relevant_files(
    available_files: list[str],
    *,
    goal_text: str,
    relevant_path_hints: list[str] | None = None,
    limit: int = 12,
) -> list[str]:
    """Deterministic relevance ranking - no LLM call. Exact/prefix hint matches first, then
    keyword overlap between the task goal and path segments, stable-sorted so results are
    reproducible run-to-run (important for cache correctness and for tests)."""
    hints = relevant_path_hints or []
    goal_words = {w.lower() for w in _WORD_RE.findall(goal_text or "") if len(w) > 2}

    def score(path: str) -> tuple[int, int, str]:
        hint_score = 0
        for hint in hints:
            hint = hint.strip("/")
            if not hint:
                continue
            if path == hint or path.startswith(hint + "/") or hint in path:
                hint_score += 10
        path_words = {w.lower() for w in _WORD_RE.findall(path)}
        overlap = len(goal_words & path_words)
        # Negative sort keys so higher relevance sorts first while ties break alphabetically.
        return (-hint_score, -overlap, path)

    ranked = sorted(set(available_files), key=score)
    return ranked[:limit]


class ContextCache:
    """Tiny in-process LRU keyed by (project_id, git_sha, key). Including the git SHA in the
    key is the invalidation strategy the spec asks for - a new commit is automatically a cache
    miss, nothing needs to explicitly bust entries for a project after a write."""

    def __init__(self, max_entries: int = 512) -> None:
        self._max_entries = max_entries
        self._store: OrderedDict[tuple[str, str, str], object] = OrderedDict()

    def get(self, project_id: str, git_sha: str | None, key: str) -> object | None:
        cache_key = (str(project_id), git_sha or "", key)
        if cache_key not in self._store:
            return None
        self._store.move_to_end(cache_key)
        return self._store[cache_key]

    def set(self, project_id: str, git_sha: str | None, key: str, value: object) -> None:
        cache_key = (str(project_id), git_sha or "", key)
        self._store[cache_key] = value
        self._store.move_to_end(cache_key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()


_GLOBAL_FILE_CACHE = ContextCache()


class ContextEngine:
    def __init__(self, db: Session, *, cache: ContextCache | None = None) -> None:
        self.db = db
        self._cache = cache if cache is not None else _GLOBAL_FILE_CACHE

    # -- inventories: key names / kinds only, values never touch this layer -------------------

    def secrets_inventory(self, project_id: object) -> list[str]:
        rows = (
            self.db.query(Secret.key, Secret.encrypted_value)
            .filter(Secret.project_id == project_id)
            .all()
        )
        return sorted({key for key, _value in rows})

    def unfilled_secret_keys(self, project_id: object) -> list[str]:
        rows = (
            self.db.query(Secret.key)
            .filter(Secret.project_id == project_id, Secret.encrypted_value.is_(None))
            .all()
        )
        return sorted({key for (key,) in rows})

    def services_inventory(self, project_id: object) -> list[str]:
        rows = (
            self.db.query(ProjectService.kind).filter(ProjectService.project_id == project_id).all()
        )
        return sorted({kind for (kind,) in rows})

    # -- git / workspace -----------------------------------------------------------------------

    def git_history_summary(self, project_dir: Path, *, limit: int = 10) -> str:
        from src.services.project_git import list_versions

        versions = list_versions(project_dir, limit=limit)
        if not versions:
            return "(no commits yet)"
        return "\n".join(f"- {v.commit_hash[:8]} {v.message}" for v in versions)

    def list_workspace_files_cached(
        self, project_id: object, workspace_root: Path, git_sha: str | None
    ) -> list[str]:
        cached = self._cache.get(str(project_id), git_sha, "file_list")
        if cached is not None:
            return list(cached)  # type: ignore[arg-type]
        from src.services.workspace import list_workspace_files

        files = list_workspace_files(workspace_root)
        self._cache.set(str(project_id), git_sha, "file_list", files)
        return files

    # -- task-scoped assembly (feeds contract_builder.py) ---------------------------------------

    def build_project_state_summary(
        self,
        project: Project,
        workspace_root: Path,
        *,
        git_sha: str | None,
        last_build_status: str | None = None,
        last_deploy_status: str | None = None,
        recent_changes_summary: str = "",
    ) -> ProjectStateSummary:
        files = self.list_workspace_files_cached(project.id, workspace_root, git_sha)
        return ProjectStateSummary(
            project_type=project.type,  # type: ignore[arg-type]
            project_name=project.name,
            current_files=files[:200],
            recent_changes_summary=recent_changes_summary,
            git_head_sha=git_sha,
            secrets_inventory=self.secrets_inventory(project.id),
            services_inventory=self.services_inventory(project.id),
            last_build_status=last_build_status,
            last_deploy_status=last_deploy_status,
        )

    def build_relevant_files(
        self,
        workspace_root: Path,
        *,
        available_files: list[str],
        goal_text: str,
        relevant_path_hints: list[str] | None = None,
        limit: int = 8,
        max_chars_per_file: int = 4000,
    ) -> list[FileReference]:
        chosen = select_relevant_files(
            available_files,
            goal_text=goal_text,
            relevant_path_hints=relevant_path_hints,
            limit=limit,
        )
        refs: list[FileReference] = []
        for rel_path in chosen:
            full_path = workspace_root / rel_path
            try:
                raw = full_path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                refs.append(
                    FileReference(
                        path=rel_path, reason="matched task scope (binary/unreadable)", content=None
                    )
                )
                continue
            truncated = len(raw) > max_chars_per_file
            content = (
                clip_text(raw, max_chars=max_chars_per_file, keep="head") if truncated else raw
            )
            refs.append(
                FileReference(
                    path=rel_path, reason="matched task scope", content=content, truncated=truncated
                )
            )
        return refs

    def build_dependency_results(self, dependency_tasks: list[AgentTask]) -> list[DependencyResult]:
        import json

        results: list[DependencyResult] = []
        for task in dependency_tasks:
            key_outputs: list[str] = []
            summary = ""
            if task.result_json:
                try:
                    claimed = json.loads(task.result_json)
                    summary = str(claimed.get("summary", ""))[:800]
                    key_outputs = [str(f) for f in claimed.get("claimed_changed_files", [])][:20]
                    unresolved = [
                        str(item).strip()
                        for item in claimed.get("unresolved", [])
                        if str(item).strip()
                    ][:8]
                    if unresolved:
                        summary = (summary + " TODO судьи: " + " | ".join(unresolved))[:800]
                except (ValueError, TypeError):
                    pass
            results.append(
                DependencyResult(
                    local_id=task.local_id,
                    title=task.title,
                    status=task.status,
                    summary=summary,
                    key_outputs=key_outputs,
                )
            )
        return results

    def build_error_context_item(
        self, *, kind: str, log_text: str, title: str, provenance: str
    ) -> ContextItem | None:
        structured = extract_structured_error(log_text)
        if structured is None:
            return None
        content = f"{structured.summary}\n\nFiles: {', '.join(structured.file_hints) or '(none detected)'}\n\n{structured.raw_excerpt}"
        return ContextItem(
            kind=kind,  # type: ignore[arg-type]
            title=title,
            content=content,
            provenance=provenance,
            token_estimate=estimate_tokens(content),
        )

    # -- global (orchestrator-only) assembly -----------------------------------------------------

    def build_global_context_summary(
        self,
        *,
        original_request: str,
        project: Project,
        workspace_root: Path,
        git_sha: str | None,
        plan_goal: str | None,
        task_summaries: list[str],
        max_chars: int = 6000,
    ) -> str:
        """Everything the planner/replanner sees; never handed directly to a task executor.
        Stored (clipped) on OrchestrationRun.context_summary for observability/debugging."""
        parts = [
            f"Original request: {clip_text(original_request, max_chars=2000)}",
            f"Project: {project.name} ({project.type})",
            f"Plan goal: {plan_goal or '(not planned yet)'}",
            f"Secrets on file (keys only): {', '.join(self.secrets_inventory(project.id)) or '(none)'}",
            f"Services provisioned: {', '.join(self.services_inventory(project.id)) or '(none)'}",
            "Git history:\n" + self.git_history_summary(workspace_root),
        ]
        if task_summaries:
            parts.append("Task results so far:\n" + "\n".join(f"- {s}" for s in task_summaries))
        return clip_text(redact("\n\n".join(parts)), max_chars=max_chars)
