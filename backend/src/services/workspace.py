"""Shared, safe access to a project's generated-code workspace on disk.

This module centralizes path-safety rules that used to be duplicated between
the one-shot artifact generator and the coding agent's file tools. Anything
that reads or writes files inside a project's workspace should go through
here so the safety rules only need to be reasoned about in one place.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.core.config import settings


class WorkspaceError(RuntimeError):
    pass


BLOCKED_PARTS = {
    "",
    ".",
    "..",
    ".env",
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
}

MAX_FILE_BYTES = 50_000_000
MAX_TOTAL_BYTES = 1_000_000_000
MAX_FILES = 5_000


def projects_root(*, create: bool = True) -> Path:
    root = Path(settings.generated_projects_dir).resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir_path(project_id: object) -> Path:
    """Resolve a project's workspace path without creating directories on disk.

    Use this for read-only checks and cleanup (delete/reconcile). Prefer
    ``project_dir`` when the caller is about to write into the workspace.
    """
    safe_id = str(project_id)
    if not re.fullmatch(r"[a-fA-F0-9-]{32,36}", safe_id):
        raise WorkspaceError("Invalid project id for workspace path")
    return projects_root(create=False) / safe_id


def project_dir(project_id: object) -> Path:
    path = project_dir_path(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_project_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.name in {".git", ".airuntime"}:
            continue
        if child.is_dir():
            import shutil

            shutil.rmtree(child)
        else:
            child.unlink()


def safe_relative_path(raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise WorkspaceError("Path is empty")
    normalized = raw_path.replace("\\", "/").strip().lstrip("/")
    if "\x00" in normalized or normalized.startswith("~"):
        raise WorkspaceError(f"Unsafe path: {raw_path}")
    parts = normalized.split("/")
    if any(part in BLOCKED_PARTS for part in parts):
        raise WorkspaceError(f"Blocked path: {raw_path}")
    if any(part.startswith(".") and part != ".well-known" for part in parts):
        raise WorkspaceError(f"Hidden files are not allowed: {raw_path}")
    if not re.fullmatch(r"[A-Za-z0-9._/\-]+", normalized):
        raise WorkspaceError(f"Path contains unsupported characters: {raw_path}")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise WorkspaceError(f"Unsafe path: {raw_path}")
    return path


def resolve_in_workspace(root: Path, raw_path: str) -> Path:
    rel = safe_relative_path(raw_path)
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if not target.is_relative_to(root_resolved):
        raise WorkspaceError(f"Path escapes project workspace: {raw_path}")
    return target


def workspace_size_bytes(root: Path) -> int:
    total = 0
    for item in root.rglob("*"):
        if item.is_file() and ".git" not in item.parts:
            total += item.stat().st_size
    return total


def list_workspace_files(root: Path, limit: int = 400) -> list[str]:
    files: list[str] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel.startswith(".airuntime/"):
            continue
        files.append(rel)
        if len(files) >= limit:
            break
    return files
