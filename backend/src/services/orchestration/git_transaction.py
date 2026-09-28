"""GitTransactionManager - the exact 10-step sequence spec section 15 specifies, as two calls
(`begin` / `complete`) rather than one, because step 3 ("Выполнить задачу") is the caller's
job: an executor (executors.py) runs between `begin()` capturing the base SHA + lease, and
`complete()` collecting evidence/validating/committing-or-rolling-back/releasing the lease.

    1. base SHA          -> begin(): project_git.current_head_sha()
    2. workspace lease    -> begin(): WorkspaceIsolationManager.acquire_for_task()
    3. execute task       -> caller, between begin() and complete()
    4. collect evidence   -> complete(): evidence.collect_task_evidence()
    5. check scope        -> complete(): validation.run_validation() (includes scope)
    6. run validation     -> complete(): validation.run_validation()
    7. commit on success  -> complete(): project_git.commit_snapshot(), airuntime(...) message
       8. repair or rollback -> complete(): discard_uncommitted_changes() only for unsafe
          failures (scope/static/build). Preview/product quality failures keep the working
          tree so the next repair attempt can iterate instead of rewriting from scratch.
    9. save accepted SHA  -> complete(): TransactionOutcome.accepted_commit_sha
    10. release lease     -> complete(): WorkspaceIsolationManager.release()

A failed/unvalidated result is never committed - `complete()` only calls commit_snapshot when
`validation_result.accepted` is True. This is the one code path that creates a commit for
orchestrated work; nothing else in the orchestration package calls commit_snapshot directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.services.orchestration.evidence import collect_task_evidence
from src.services.orchestration.schemas import (
    RiskLevel,
    TaskContract,
    TaskEvidence,
    TaskResult,
    ValidationFinding,
    ValidationResult,
)
from src.services.orchestration.validation import run_validation, should_discard_uncommitted_work
from src.services.orchestration.workspace_isolation import (
    AcquiredWorkspace,
    WorkspaceIsolationManager,
)
from src.services.project_git import (
    ProjectGitError,
    commit_snapshot,
    current_head_sha,
    discard_uncommitted_changes,
)


def format_commit_message(*, run_id: object, task_id: object, role: str, title: str) -> str:
    # project_git._short_message() truncates every commit message to 80 chars (existing house
    # convention, keeps `git log --oneline` readable) - full UUIDs alone would consume ~90
    # chars before the title even starts, so this uses the same 8-char-prefix convention the
    # codebase already applies elsewhere (e.g. rollback_to's `commit_hash[:8]`). The full ids
    # remain authoritatively queryable via AgentTask.accepted_commit_sha in the DB; this label
    # is a human-readable hint, not the source of truth.
    return f"airuntime(run:{str(run_id)[:8]} task:{str(task_id)[:8]} role:{role}): {title}"


@dataclass
class TransactionHandle:
    task: AgentTask
    contract: TaskContract
    acquired: AcquiredWorkspace
    base_commit_sha: str | None
    _started_at: float


@dataclass
class TransactionOutcome:
    committed: bool
    accepted_commit_sha: str | None
    validation_result: ValidationResult
    evidence: TaskEvidence


class GitTransactionManager:
    def __init__(self, db: Session, isolation: WorkspaceIsolationManager) -> None:
        self.db = db
        self.isolation = isolation

    def begin(
        self,
        *,
        task: AgentTask,
        contract: TaskContract,
        project_id: object,
        run_id: object,
        project_root: Path,
        holder: str,
        ttl_seconds: int,
    ) -> TransactionHandle | None:
        """Returns None on shared-mode lease contention - the caller must not proceed with
        execution in that case (no workspace was acquired to execute against)."""
        acquired = self.isolation.acquire_for_task(
            task=task,
            project_id=project_id,
            run_id=run_id,
            project_root=project_root,
            holder=holder,
            ttl_seconds=ttl_seconds,
        )
        if acquired is None:
            return None
        base_sha = current_head_sha(acquired.workspace_root)
        return TransactionHandle(
            task=task,
            contract=contract,
            acquired=acquired,
            base_commit_sha=base_sha,
            _started_at=time.monotonic(),
        )

    def complete(
        self,
        handle: TransactionHandle,
        *,
        result: TaskResult | None,
        build_result: dict | None = None,
        test_result: dict | None = None,
        lint_result: dict | None = None,
        preview_result: dict | None = None,
        runtime_health_result: dict | None = None,
        service_requests: list[str] | None = None,
        secret_requests: list[str] | None = None,
        usage: dict | None = None,
        raw_logs: str | None = None,
        release_lease: bool = True,
    ) -> TransactionOutcome:
        duration_seconds = time.monotonic() - handle._started_at
        evidence = collect_task_evidence(
            workspace_root=handle.acquired.workspace_root,
            base_commit_sha=handle.base_commit_sha,
            build_result=build_result,
            test_result=test_result,
            lint_result=lint_result,
            preview_result=preview_result,
            runtime_health_result=runtime_health_result,
            service_requests=service_requests,
            secret_requests=secret_requests,
            duration_seconds=duration_seconds,
            usage=usage,
            raw_logs=raw_logs,
        )
        validation_result = run_validation(
            contract=handle.contract, result=result, evidence=evidence
        )

        accepted_sha: str | None = None
        committed = False
        if validation_result.accepted:
            message = format_commit_message(
                run_id=handle.task.run_id,
                task_id=handle.task.id,
                role=handle.task.role,
                title=handle.task.title,
            )
            try:
                new_sha = commit_snapshot(handle.acquired.workspace_root, message=message)
                accepted_sha = new_sha or handle.base_commit_sha
                committed = True
            except ProjectGitError as exc:
                validation_result = validation_result.model_copy(
                    update={
                        "accepted": False,
                        "findings": [
                            *validation_result.findings,
                            ValidationFinding(
                                step="build",
                                passed=False,
                                severity=RiskLevel.HIGH,
                                message=f"commit failed after validation passed: {exc}",
                            ),
                        ],
                    }
                )

        if not committed and should_discard_uncommitted_work(validation_result):
            if handle.acquired.mode in ("shared", "isolated"):
                discard_uncommitted_changes(handle.acquired.workspace_root)

        if release_lease:
            self.isolation.release(handle.acquired)

        return TransactionOutcome(
            committed=committed,
            accepted_commit_sha=accepted_sha,
            validation_result=validation_result,
            evidence=evidence,
        )

    def abort(self, handle: TransactionHandle) -> None:
        """For a task that never got to `complete()` at all (e.g. cancellation mid-execution) -
        discard any partial changes and release the lease without attempting evidence
        collection or a commit."""
        discard_uncommitted_changes(handle.acquired.workspace_root)
        self.isolation.release(handle.acquired)
