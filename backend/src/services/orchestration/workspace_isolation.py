"""Three workspace modes (spec section 14), built on WorkspaceLeaseRepository (DB-authoritative,
see that module's docstring for why it's not just another Redis SETNX) + project_git.py's
worktree functions:

  - parallel_read_only: no lease at all - nothing to serialize against since nothing writes.
  - shared_sequential: the DB partial-unique-index lease (one active holder per project).
  - isolated_worktree: an isolated lease (many can coexist) + a real `git worktree` + branch,
    so the task's executor operates on a directory that literally cannot collide with the
    shared checkout or with another isolated task's own worktree.

Interop with the legacy (pre-orchestration) write path: `project_git.commit_snapshot()` still
internally takes its own best-effort Redis lock (`with_project_git_lock`) on every call,
including calls this module's callers make - so a legacy chat turn and an orchestrated run's
commit are still mutually exclusive at the actual git-plumbing level, even though only the
orchestrated side additionally holds this module's DB lease. What the DB lease adds on top is
what the Redis lock never covered: exclusivity across the *live* tool-calling/Codex-container
work that happens *before* a commit (see the Explore audit finding "No lock around the agent's
actual file writes" in project_git.py's own history).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.db.models.workspace_lease import WorkspaceLease
from src.services import project_git
from src.services.orchestration.repository import (
    DEFAULT_LEASE_TTL_SECONDS,
    WorkspaceLeaseRepository,
)


def make_holder_id() -> str:
    return f"{socket.gethostname()}:{uuid4().hex[:12]}"


def worktree_path_for_task(project_root: Path, task_id: object) -> Path:
    # Sibling directory, never nested inside project_root - `git worktree add` refuses (and
    # `git add -A` in the main tree would otherwise sweep it up) if it were nested inside the
    # tracked working directory.
    return project_root.parent / f"{project_root.name}__worktrees" / str(task_id)


def branch_name_for_task(task_id: object) -> str:
    return f"airuntime-task-{str(task_id)[:8]}"


@dataclass
class AcquiredWorkspace:
    mode: str  # "shared" | "isolated" | "read_only"
    workspace_root: Path
    project_root: Path
    lease: WorkspaceLease | None = None
    branch_name: str | None = None


class WorkspaceIsolationManager:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.leases = WorkspaceLeaseRepository(db)

    def acquire_for_task(
        self,
        *,
        task: AgentTask,
        project_id: object,
        run_id: object,
        project_root: Path,
        holder: str,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> AcquiredWorkspace | None:
        """Returns None only for shared_sequential contention (another active shared lease
        exists) - the caller must wait/retry, never proceed without a lease. Isolated and
        read-only modes never return None (isolated leases don't contend; read-only needs no
        lease at all)."""
        mode = task.workspace_mode

        if mode == "parallel_read_only":
            return AcquiredWorkspace(
                mode="read_only", workspace_root=project_root, project_root=project_root
            )

        if mode == "isolated_worktree":
            worktree_path = worktree_path_for_task(project_root, task.id)
            branch_name = branch_name_for_task(task.id)
            lease = self.leases.acquire_isolated(
                project_id=project_id,
                run_id=run_id,
                task_id=task.id,
                holder=holder,
                worktree_path=str(worktree_path),
                branch_name=branch_name,
                ttl_seconds=ttl_seconds,
            )
            project_git.create_worktree(
                project_root,
                worktree_path=worktree_path,
                branch_name=branch_name,
                base_sha=task.base_commit_sha,
            )
            return AcquiredWorkspace(
                mode="isolated",
                workspace_root=worktree_path,
                project_root=project_root,
                lease=lease,
                branch_name=branch_name,
            )

        # default: shared_sequential
        lease = self.leases.try_acquire_shared(
            project_id=project_id,
            run_id=run_id,
            holder=holder,
            task_id=task.id,
            ttl_seconds=ttl_seconds,
        )
        if lease is None:
            return None
        return AcquiredWorkspace(
            mode="shared", workspace_root=project_root, project_root=project_root, lease=lease
        )

    def renew(
        self, acquired: AcquiredWorkspace, *, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    ) -> None:
        if acquired.lease is not None:
            self.leases.renew(acquired.lease, ttl_seconds=ttl_seconds)

    def release(self, acquired: AcquiredWorkspace, *, remove_worktree: bool = False) -> None:
        """`remove_worktree=False` by default: after an isolated task finishes, its worktree
        must still exist for IntegrationAgent to merge - only remove it (via
        `release_and_cleanup_worktree`) once that merge has actually happened."""
        if acquired.lease is not None:
            self.leases.release(acquired.lease)
        if acquired.mode == "isolated" and remove_worktree and acquired.branch_name:
            project_git.remove_worktree(
                acquired.project_root,
                worktree_path=acquired.workspace_root,
                branch_name=acquired.branch_name,
            )

    def release_and_cleanup_worktree(self, acquired: AcquiredWorkspace) -> None:
        self.release(acquired, remove_worktree=True)
