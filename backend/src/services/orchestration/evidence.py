"""Independently-collected, factual TaskEvidence - the server's own observation of what a task
actually did, gathered AFTER execution regardless of what the executor's TaskResult claimed.
validation.py computes acceptance from this module's output, never from TaskResult directly
(spec section 8: "Заявления модели не считать доказательством").

Every function here is either pure or touches only git/filesystem state that already exists on
disk - no LLM calls, so this is fully unit-testable without any fake provider.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from src.services import project_git
from src.services.orchestration.context_engine import redact
from src.services.orchestration.schemas import TaskEvidence

_DEPENDENCY_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
}

# Patterns for TaskEvidence.secret_scan_findings - a defense-in-depth SCOPE-validation signal
# (spec section 9: agent "не добавил секрет"), not a substitute for request_secret's own
# never-touches-the-LLM guarantee. Findings record *locations*, never the matched secret text
# itself (which would just leak the same thing the scan exists to catch).
_SECRET_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id shape
    re.compile(r"://[^:/\s@]+:[^@/\s]{6,}@"),  # credentials embedded in a connection URL
)


def scan_for_secret_leaks(diff_text: str) -> list[str]:
    findings: list[str] = []
    for line_no, line in enumerate(diff_text.splitlines(), start=1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pattern in _SECRET_LEAK_PATTERNS:
            if pattern.search(line):
                findings.append(f"added line {line_no} matches a credential-like pattern")
                break
    return findings


def detect_dependency_changes(changed_files: list[str]) -> list[str]:
    return sorted({f for f in changed_files if Path(f).name in _DEPENDENCY_FILES})


def run_static_checks(workspace_root: Path, changed_files: list[str]) -> dict | None:
    """Parse-check the files this task actually touched, server-side (spec section 9's static
    validation layer). Returns None when nothing checkable changed, so validate_static can tell
    "clean" apart from "not applicable".

    Deliberately parse-only - `ast.parse`/`json.loads` never execute the file. Running the
    project's real toolchain (`npm test`, `ruff`, `tsc`) would mean executing agent-authored code
    in the backend process, which is exactly what the platform's "backend never gets a Docker
    socket / never runs project code" invariant forbids; that class of check belongs to the
    worker-side build (validate_build). What this DOES catch, cheaply and deterministically, is
    the most common way an LLM turn breaks a project: a syntactically invalid .py or a malformed
    package.json/tsconfig.json, which otherwise only surfaces later as a confusing Docker build
    failure with no attribution to the task that caused it.
    """
    errors: list[str] = []
    checked = 0
    for rel_path in changed_files:
        suffix = Path(rel_path).suffix.lower()
        if suffix not in (".py", ".json"):
            continue
        absolute = workspace_root / rel_path
        if not absolute.is_file():
            continue  # deleted by this task, or outside the checkout
        try:
            text = absolute.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{rel_path}: unreadable ({type(exc).__name__})")
            continue
        checked += 1
        if suffix == ".py":
            try:
                ast.parse(text, filename=rel_path)
            except SyntaxError as exc:
                errors.append(f"{rel_path}:{exc.lineno or '?'}: {exc.msg}")
        else:
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{rel_path}:{exc.lineno}: {exc.msg}")

    if checked == 0 and not errors:
        return None
    return {"ok": not errors, "checked_files": checked, "errors": errors}


def collect_task_evidence(
    *,
    workspace_root: Path,
    base_commit_sha: str | None,
    head_commit_sha: str | None = None,
    build_result: dict | None = None,
    test_result: dict | None = None,
    lint_result: dict | None = None,
    preview_result: dict | None = None,
    runtime_health_result: dict | None = None,
    service_requests: list[str] | None = None,
    secret_requests: list[str] | None = None,
    duration_seconds: float = 0.0,
    usage: dict | None = None,
    raw_logs: str | None = None,
) -> TaskEvidence:
    """The one entry point git_transaction.py calls after every task execution, win or lose.
    `head_commit_sha=None` (the default) means "diff against the current uncommitted state"
    (pre-commit, scope-validation time) - see project_git._resolve_diff_args for why this
    stages via `git add -A` first rather than doing a plain working-tree diff, and pass an
    explicit `head_commit_sha="HEAD"` (or any other ref) only when diffing two already-committed
    states after the fact."""
    diff = project_git.diff_stat(workspace_root, base_sha=base_commit_sha, head=head_commit_sha)
    by_status = project_git.changed_files_by_status(
        workspace_root, base_sha=base_commit_sha, head=head_commit_sha
    )
    changed = sorted({*by_status["added"], *by_status["modified"], *by_status["deleted"]})

    # Server-collected, never taken from the executor's claim. An executor MAY additionally
    # report its own lint run (e.g. a skill that shells out to a real linter); when it does, its
    # findings are merged in rather than replacing ours - the server's own parse check is the
    # floor that cannot be talked out of by a model asserting "lint passed".
    static_result = run_static_checks(workspace_root, changed)
    if lint_result is not None and static_result is not None:
        merged_errors = list(static_result["errors"]) + list(lint_result.get("errors") or [])
        lint_result = {
            **lint_result,
            **static_result,
            "errors": merged_errors,
            "ok": bool(static_result["ok"]) and bool(lint_result.get("ok", True)),
        }
    elif static_result is not None:
        lint_result = static_result

    return TaskEvidence(
        git_diff_stat=diff,
        changed_files=changed,
        created_files=sorted(by_status["added"]),
        deleted_files=sorted(by_status["deleted"]),
        build_result=build_result,
        test_result=test_result,
        lint_result=lint_result,
        dependency_changes=detect_dependency_changes(changed),
        secret_scan_findings=scan_for_secret_leaks(diff),
        service_requests=service_requests or [],
        secret_requests=secret_requests or [],
        preview_result=preview_result,
        runtime_health_result=runtime_health_result,
        duration_seconds=duration_seconds,
        usage=usage,
        logs_digest=redact(raw_logs)[:4000] if raw_logs else None,
    )
