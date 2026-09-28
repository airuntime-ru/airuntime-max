from __future__ import annotations

import shutil
import subprocess
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from src.core.config import settings

try:
    from redis import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ProjectVersion:
    commit_hash: str
    created_at: datetime
    message: str


class ProjectGitError(RuntimeError):
    pass


_PLATFORM_GIT_EXCLUDES = (
    # Runtime evidence is intentionally kept beside the checkout so the next independent QA
    # task can read it, but it is platform-owned output rather than user-authored project code.
    ".airuntime/preview/",
)


def _ensure_platform_git_excludes(project_dir: Path) -> None:
    # In a linked worktree `.git` is a text file, not a directory. Ask Git for the canonical
    # repository metadata path so this works for both the main checkout and isolated worktrees.
    resolved = _run_git(
        cwd=project_dir, args=["rev-parse", "--git-path", "info/exclude"], check=True
    ).stdout.strip()
    exclude_path = Path(resolved)
    if not exclude_path.is_absolute():
        exclude_path = project_dir / exclude_path
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        exclude_path.read_text(encoding="utf-8", errors="replace") if exclude_path.exists() else ""
    )
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [pattern for pattern in _PLATFORM_GIT_EXCLUDES if pattern not in existing_lines]
    if not missing:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    with exclude_path.open("a", encoding="utf-8") as handle:
        handle.write(separator + "\n".join(missing) + "\n")


def _require_git() -> str:
    git_bin = shutil.which("git")
    if not git_bin:
        raise ProjectGitError("git is not available in container")
    return git_bin


def project_repo_dir(project_id: UUID | str) -> Path:
    return Path(settings.generated_projects_dir).resolve() / str(project_id)


def _normalize_repo_rel_path(value: str) -> str:
    """
    Normalize path inside git repo (no leading slash), prevent traversal.
    Returns empty string for root.
    """
    raw = (value or "").strip()
    raw = raw.replace("\\", "/")
    raw = raw.lstrip("/")
    if raw in {"", "."}:
        return ""
    parts = [p for p in raw.split("/") if p]
    if any(p == ".." for p in parts):
        raise ProjectGitError("Invalid path")
    return "/".join(parts)


@dataclass(frozen=True)
class ProjectVersionTreeEntry:
    name: str
    entry_type: str  # 'tree' | 'blob'
    size_bytes: int | None


def _run_git(*, cwd: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    _ = _require_git()
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=check,
    )


def _run_git_bytes(
    *, cwd: Path, args: list[str], check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    _ = _require_git()
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        capture_output=True,
    )


def init_repo_if_needed(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    if (project_dir / ".git").exists():
        _ensure_platform_git_excludes(project_dir)
        return

    _ = _require_git()
    # Initialize repository.
    _run_git(cwd=project_dir, args=["init", "-q"])
    # Configure committer identity (we don't rely on global gitconfig inside container).
    _run_git(
        cwd=project_dir,
        args=["config", "user.email", "airuntime@local.invalid"],
        check=True,
    )
    _run_git(
        cwd=project_dir,
        args=["config", "user.name", "AIRuntime"],
        check=True,
    )
    _ensure_platform_git_excludes(project_dir)


def _redis() -> Redis | None:
    if Redis is None:
        return None
    try:
        return Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
    except Exception:
        return None


def _lock_key(project_id: str) -> str:
    return f"airuntime:git-lock:{project_id}"


class _NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def with_project_git_lock(
    project_id: UUID | str, *, ttl_seconds: int = 180
) -> AbstractContextManager[None]:
    """
    Best-effort lock to protect git checkout/commit and docker builds.

    If Redis is unavailable, it degrades to a no-op lock.
    """
    lock = _redis()
    if not lock:
        return _NullLock()

    token = str(uuid4())
    key = _lock_key(str(project_id))
    acquired = False

    try:
        # Try acquire for a short time to reduce conflicts.
        for _ in range(30):
            acquired = bool(lock.set(key, token, nx=True, ex=ttl_seconds))
            if acquired:
                break
            time.sleep(0.2)
        if not acquired:
            return _NullLock()

        class _Releaser(_NullLock):
            def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
                try:
                    if lock.get(key) == token:
                        lock.delete(key)
                except Exception:
                    pass
                return None

        return _Releaser()
    except Exception:
        return _NullLock()


def _short_message(msg: str, *, max_len: int = 80) -> str:
    cleaned = " ".join((msg or "").replace("\n", " ").split())
    return cleaned[:max_len] if cleaned else "AIRuntime update"


def commit_snapshot(project_dir: Path, *, message: str) -> str | None:
    """
    Commit all current files inside project_dir into git.

    Returns new commit hash, or None when nothing changed.
    """
    with with_project_git_lock(project_dir.name):
        init_repo_if_needed(project_dir)

        # Add everything (excluding .git itself).
        _run_git(cwd=project_dir, args=["add", "-A"])

        # If index == HEAD, git commit will be a no-op (we also avoid creating noise).
        changed = _run_git(
            cwd=project_dir, args=["status", "--porcelain"], check=False
        ).stdout.strip()
        if not changed:
            return None

        completed = _run_git(
            cwd=project_dir,
            args=["commit", "-q", "-m", _short_message(message)],
            check=False,
        )
        if completed.returncode != 0:
            # If there's some race / unusual state, bubble up.
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            raise ProjectGitError(f"git commit failed: {stderr or stdout}")

        # Get HEAD hash.
        head = _run_git(cwd=project_dir, args=["rev-parse", "HEAD"]).stdout.strip()
        return head


def discard_uncommitted_changes(project_dir: Path) -> None:
    """Rollback for an unaccepted (failed validation) task transaction to the last accepted
    checkpoint (HEAD), scoped strictly to this project's own git repository - never a broader
    filesystem operation against `project_dir`'s parent or anything outside this git root (spec
    section 15: "Rollback должен быть ограничен project artifact path и последним checkpoint" /
    "Не применять широкие destructive-команды к workspace root").

    `git reset --hard HEAD` (not just `checkout -- .`) because evidence collection
    (evidence.py, via project_git._resolve_diff_args) already ran `git add -A` to see new files
    in the pre-commit diff - by the time a rollback is needed, a brand-new file the task wrote
    is STAGED, not merely untracked, and `git clean` alone never touches staged content (only
    genuinely untracked working-tree entries). `reset --hard` un-stages and removes it in one
    step by making the index and working tree exactly match HEAD again. `git clean -fd`
    afterward is a belt-and-suspenders pass for anything that ended up untracked regardless.
    """
    if not (project_dir / ".git").exists():
        return
    if current_head_sha(project_dir) is None:
        # No accepted checkpoint exists yet (repo has zero commits) - "reset --hard HEAD"
        # has no HEAD to target on an unborn branch, so the discard is simply "wipe everything
        # except .git", the same skip-list workspace.clean_project_dir uses.
        for child in project_dir.iterdir():
            if child.name in {".git", ".airuntime"}:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        return
    _run_git(cwd=project_dir, args=["reset", "--hard", "HEAD"], check=False)
    _run_git(cwd=project_dir, args=["clean", "-fd"], check=False)


def list_versions(project_dir: Path, *, limit: int = 30) -> list[ProjectVersion]:
    if not (project_dir / ".git").exists():
        return []

    fmt = "%H|%ct|%s"
    try:
        proc = _run_git(cwd=project_dir, args=["log", f"-n{int(limit)}", f"--pretty=format:{fmt}"])
    except subprocess.CalledProcessError:
        # A freshly `init_repo_if_needed()`-ed repo with no commits yet is a real, reachable
        # state (e.g. the orchestration engine builds a context summary - including this history
        # - before the very first task has ever committed anything) - `git log` exits 128
        # ("does not have any commits yet"), which is "no history", not an error condition here.
        return []
    out = proc.stdout.strip()
    if not out:
        return []

    versions: list[ProjectVersion] = []
    for line in out.splitlines():
        commit_hash, unix_ct, msg = line.split("|", 2)
        created_at = datetime.fromtimestamp(int(unix_ct), tz=UTC)
        versions.append(ProjectVersion(commit_hash=commit_hash, created_at=created_at, message=msg))
    return versions


def list_version_tree(
    project_dir: Path, *, commit_hash: str, rel_path: str = ""
) -> list[ProjectVersionTreeEntry]:
    if not (project_dir / ".git").exists():
        raise ProjectGitError("Repo not initialized")

    normalized = _normalize_repo_rel_path(rel_path)

    # `git ls-tree <commit>:<path>` returns direct children with names relative to
    # that path. Using `-- <path>` returns prefixed names like `public/index.html`,
    # which makes the UI accidentally build paths such as `public/public/...`.
    if normalized:
        args = ["ls-tree", "-z", "-l", f"{commit_hash}:{normalized}"]
    else:
        args = ["ls-tree", "-z", "-l", commit_hash]

    proc = _run_git_bytes(cwd=project_dir, args=args, check=False)
    if proc.returncode != 0:
        raise ProjectGitError(
            proc.stderr.decode("utf-8", errors="replace").strip() or "git ls-tree failed"
        )

    out = proc.stdout
    entries: list[ProjectVersionTreeEntry] = []
    for raw_item in out.split(b"\0"):
        if not raw_item:
            continue
        # Format: "<mode> <type> <object> <size>\t<name>"
        try:
            meta, name = raw_item.split(b"\t", 1)
        except ValueError:
            continue
        meta_parts = meta.split()
        if len(meta_parts) < 4:
            continue
        entry_type = meta_parts[1].decode("utf-8", errors="replace")
        size_bytes_raw = meta_parts[3].decode("utf-8", errors="replace")
        size_bytes = None if size_bytes_raw == "-" else int(size_bytes_raw)
        entries.append(
            ProjectVersionTreeEntry(
                name=name.decode("utf-8", errors="replace"),
                entry_type=entry_type,
                size_bytes=size_bytes,
            )
        )

    # Sort: directories first, then name.
    entries.sort(key=lambda e: (0 if e.entry_type == "tree" else 1, e.name.lower()))
    return entries


def read_version_file(
    project_dir: Path,
    *,
    commit_hash: str,
    rel_path: str,
    max_bytes: int = 256 * 1024,
) -> dict:
    """
    Return file content for viewing.
    For binary/unsupported files -> is_binary=true, content="".
    For too large files -> truncated=true.
    """
    if not (project_dir / ".git").exists():
        raise ProjectGitError("Repo not initialized")

    normalized = _normalize_repo_rel_path(rel_path)
    if not normalized:
        raise ProjectGitError("Path points to directory")

    obj = f"{commit_hash}:{normalized}"
    proc = _run_git_bytes(cwd=project_dir, args=["show", obj], check=False)
    if proc.returncode != 0:
        raise ProjectGitError(
            proc.stderr.decode("utf-8", errors="replace").strip() or "git show failed"
        )

    data = proc.stdout
    size_bytes = len(data)
    truncated = False
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True

    # Quick binary detection.
    if b"\0" in data[: min(4096, len(data))]:
        return {
            "content": "",
            "is_binary": True,
            "truncated": truncated,
            "size_bytes": size_bytes,
        }

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        # Still allow "replace" to show something instead of crashing.
        try:
            content = data.decode("utf-8", errors="replace")
        except Exception:
            return {
                "content": "",
                "is_binary": True,
                "truncated": truncated,
                "size_bytes": size_bytes,
            }

    return {
        "content": content,
        "is_binary": False,
        "truncated": truncated,
        "size_bytes": size_bytes,
    }


def archive_version(project_dir: Path, *, commit_hash: str, out_path: Path) -> None:
    if not (project_dir / ".git").exists():
        raise ProjectGitError("Repo not initialized")

    try:
        proc = _run_git_bytes(cwd=project_dir, args=["archive", "--format=zip", commit_hash])
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes | bytearray)
            else str(exc.stderr)
        )
        raise ProjectGitError(stderr.strip() or "git archive failed") from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(proc.stdout)


def archive_version_stream(project_dir: Path, *, commit_hash: str) -> bytes:
    # Helper for smaller archives.
    try:
        proc = _run_git_bytes(cwd=project_dir, args=["archive", "--format=zip", commit_hash])
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes | bytearray)
            else str(exc.stderr)
        )
        raise ProjectGitError(stderr.strip() or "git archive failed") from exc
    return proc.stdout


_EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty-tree
# object hash - a constant, not computed, so this works identically on Windows dev machines
# (no /dev/null) and Linux containers alike.


def current_head_sha(project_dir: Path) -> str | None:
    """`None` for a repo with no commits yet (or no `.git` at all) - the base_sha
    git_transaction.py captures at the start of every task transaction, so a freshly-created
    project's very first task correctly diffs against the empty tree rather than raising."""
    if not (project_dir / ".git").exists():
        return None
    proc = _run_git(cwd=project_dir, args=["rev-parse", "HEAD"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _resolve_diff_args(project_dir: Path, *, base_sha: str | None, head: str | None) -> list[str]:
    """Shared arg-builder for diff_stat/changed_files_between/changed_files_by_status.

    `base_sha=None` -> diff against git's empty-tree object (a brand-new project with no prior
    commit yet still gets a correct "everything is new" diff instead of an empty one).

    `head=None` -> diff against the STAGED INDEX after staging everything (`git add -A` first),
    not a literal ref. This is what evidence collection needs when it runs BEFORE a commit
    decision has been made (git_transaction.py always collects evidence pre-commit): a plain
    `git diff <base>` (or `git diff <base> HEAD`) only shows changes to files git already
    tracks - a brand-new file the agent just wrote is invisible to it until staged, so without
    this, evidence.collect_task_evidence would silently miss every newly-created file and scope
    validation could never catch a forbidden new file being added. Staging first has no side
    effect beyond updating the index (no commit), and commit_snapshot() re-stages via its own
    `git add -A` immediately after anyway, so this is not wasted or duplicated work in the
    success path - it's what makes the evidence collected actually match what gets committed.

    `head="HEAD"` (or any other literal ref) -> ordinary two-commit diff, unchanged.
    """
    base = base_sha or _EMPTY_TREE_SHA1
    if head is None:
        _run_git(cwd=project_dir, args=["add", "-A"], check=False)
        return ["--cached", base]
    return [base, head]


def diff_stat(project_dir: Path, *, base_sha: str | None, head: str | None = "HEAD") -> str:
    """`git diff --stat` between base_sha and head (see `_resolve_diff_args` for what `None`
    means for each) - the cheap, textual "what changed" summary
    services/orchestration/evidence.py uses as factual proof of a task's real footprint,
    independent of whatever the agent's own TaskResult claims."""
    if not (project_dir / ".git").exists():
        return ""
    args = _resolve_diff_args(project_dir, base_sha=base_sha, head=head)
    proc = _run_git(cwd=project_dir, args=["diff", "--stat", *args], check=False)
    if proc.returncode != 0:
        # Most common cause: base_sha not reachable in this repo (e.g. a fresh worktree) -
        # fall back to a full status listing rather than raising, since this is diagnostic
        # evidence, not a control-flow-critical operation.
        return _run_git(cwd=project_dir, args=["status", "--porcelain"], check=False).stdout
    return proc.stdout


def changed_files_between(
    project_dir: Path, *, base_sha: str | None, head: str | None = "HEAD"
) -> list[str]:
    if not (project_dir / ".git").exists():
        return []
    args = _resolve_diff_args(project_dir, base_sha=base_sha, head=head)
    proc = _run_git(cwd=project_dir, args=["diff", "--name-only", *args], check=False)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def changed_files_by_status(
    project_dir: Path, *, base_sha: str | None, head: str | None = "HEAD"
) -> dict[str, list[str]]:
    """`{"added": [...], "modified": [...], "deleted": [...]}` via `git diff --name-status` -
    what services/orchestration/evidence.py uses to split TaskEvidence.changed_files into
    created_files/deleted_files, since `changed_files_between` alone can't distinguish them."""
    result: dict[str, list[str]] = {"added": [], "modified": [], "deleted": []}
    if not (project_dir / ".git").exists():
        return result
    args = _resolve_diff_args(project_dir, base_sha=base_sha, head=head)
    proc = _run_git(cwd=project_dir, args=["diff", "--name-status", *args], check=False)
    if proc.returncode != 0:
        return result
    status_map = {"A": "added", "M": "modified", "D": "deleted"}
    for line in proc.stdout.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        code, path = line.split("\t", 1)
        # Renames/copies ("R100", "C100") - treat as modified on the new path, close enough
        # for evidence purposes (the exact rename pairing isn't load-bearing here).
        bucket = status_map.get(code[0], "modified")
        result[bucket].append(path)
    return result


def create_worktree(
    project_dir: Path, *, worktree_path: Path, branch_name: str, base_sha: str | None = None
) -> None:
    """Isolated per-task worktree for workspace_mode="isolated_worktree" (spec section 14) -
    a real separate working directory + branch sharing the same object store, so a parallel
    write task can never touch the shared checkout other concurrent/sequential tasks use."""
    init_repo_if_needed(project_dir)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if (
        base_sha is None
        and _run_git(
            cwd=project_dir, args=["rev-parse", "--verify", "-q", "HEAD"], check=False
        ).returncode
        != 0
    ):
        # Brand-new project, zero commits yet (HEAD is unborn) - `worktree add ... HEAD` has
        # nothing to branch from and fails outright. commit_snapshot() can't help here either
        # (an empty tree has no diff to no-op against), so give the shared checkout a real root
        # commit first; the worktree then branches from that like any other.
        _run_git(
            cwd=project_dir,
            args=["commit", "--allow-empty", "-q", "-m", "airuntime: initial commit"],
        )
    start_point = base_sha or "HEAD"
    _run_git(
        cwd=project_dir,
        args=["worktree", "add", "-b", branch_name, str(worktree_path), start_point],
    )


def remove_worktree(
    project_dir: Path, *, worktree_path: Path, branch_name: str, force: bool = True
) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    _run_git(cwd=project_dir, args=args, check=False)
    _run_git(cwd=project_dir, args=["branch", "-D", branch_name], check=False)
    _run_git(cwd=project_dir, args=["worktree", "prune"], check=False)


def merge_worktree_branch(
    project_dir: Path, *, branch_name: str, message: str, no_ff: bool = True
) -> str | None:
    """Merge an isolated worktree's branch back into the current HEAD of the shared workspace.
    Never auto-resolves conflicts (spec: "при конфликте не выполнять слепой auto-resolution") -
    a conflicted merge is aborted and ProjectGitError raised so IntegrationAgent's caller can
    surface it, rather than silently picking a side."""
    with with_project_git_lock(project_dir.name):
        proc = _run_git(
            cwd=project_dir,
            args=[
                "merge",
                "--no-ff" if no_ff else "--ff",
                "-m",
                _short_message(message),
                branch_name,
            ],
            check=False,
        )
        if proc.returncode != 0:
            _run_git(cwd=project_dir, args=["merge", "--abort"], check=False)
            stderr = proc.stderr.strip() or proc.stdout.strip()
            raise ProjectGitError(f"merge conflict, aborted: {stderr}")
        changed = _run_git(
            cwd=project_dir, args=["status", "--porcelain"], check=False
        ).stdout.strip()
        if changed:
            # Should not normally happen right after a clean merge commit, but stay honest
            # about the working tree state rather than assuming.
            _run_git(cwd=project_dir, args=["add", "-A"])
        head = _run_git(cwd=project_dir, args=["rev-parse", "HEAD"]).stdout.strip()
        return head


def rollback_to(project_dir: Path, *, commit_hash: str, message: str) -> str:
    """
    Checkout target commit and create a new commit representing rollback.
    Returns new HEAD hash.
    """
    if not (project_dir / ".git").exists():
        raise ProjectGitError("Repo not initialized")

    with with_project_git_lock(project_dir.name):
        try:
            _run_git(cwd=project_dir, args=["checkout", "-q", "--force", commit_hash])
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else str(exc.stderr)
            raise ProjectGitError(stderr or "git checkout failed") from exc

        # Create explicit rollback commit even if checkout resulted in same tree.
        _run_git(
            cwd=project_dir,
            args=["commit", "-q", "--allow-empty", "-m", _short_message(message)],
            check=False,
        )

        try:
            return _run_git(cwd=project_dir, args=["rev-parse", "HEAD"]).stdout.strip()
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else str(exc.stderr)
            raise ProjectGitError(stderr or "git rev-parse failed") from exc
