"""Thin, `db.query()`-based persistence for the orchestration domain - deliberately not an ORM
graph (no `relationship()`, matching this codebase's house style, confirmed nowhere else in
db/models/) and deliberately not a generic unit-of-work abstraction. Each repository wraps one
table and exposes the specific read/write operations the orchestration engine needs.

Repositories `add()` + `flush()` but never `commit()` - committing is the caller's
responsibility (engine.py / the API router), so a caller can batch several repository calls
(e.g. "transition run to validating" + "append a RunEvent") into one atomic commit. The one
exception is `WorkspaceLeaseRepository.try_acquire_shared`, which uses a SAVEPOINT
(`Session.begin_nested()`) internally so a lost race there never rolls back unrelated work
already staged on the same session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from src.db.models.agent_task import AgentTask
from src.db.models.mcp_server import McpServer
from src.db.models.orchestration_plan import OrchestrationPlan
from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.run_event import RunEvent
from src.db.models.workspace_lease import WorkspaceLease
from src.services.orchestration.status import (
    RUN_TERMINAL_STATUSES,
    is_task_terminal,
    validate_plan_transition,
    validate_run_transition,
    validate_task_transition,
)

DEFAULT_LEASE_TTL_SECONDS = 180


class OrchestrationRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **fields: object) -> OrchestrationRun:
        run = OrchestrationRun(**fields)
        self.db.add(run)
        self.db.flush()
        return run

    def get(self, run_id: uuid.UUID | str) -> OrchestrationRun | None:
        return self.db.query(OrchestrationRun).filter(OrchestrationRun.id == run_id).one_or_none()

    def get_locked(self, run_id: uuid.UUID | str) -> OrchestrationRun | None:
        """Row-level lock (`SELECT ... FOR UPDATE`) for an atomic read-modify-write. Caller
        must already be inside a transaction and is responsible for committing/rolling back -
        this is the primitive that prevents two workers from both picking up the same run after
        a restart (section 3: "защищёнными от двойного запуска")."""
        return (
            self.db.query(OrchestrationRun)
            .filter(OrchestrationRun.id == run_id)
            .with_for_update()
            .one_or_none()
        )

    def list_active_for_project(self, project_id: uuid.UUID | str) -> list[OrchestrationRun]:
        return (
            self.db.query(OrchestrationRun)
            .filter(OrchestrationRun.project_id == project_id)
            .filter(OrchestrationRun.status.notin_(RUN_TERMINAL_STATUSES))
            .all()
        )

    def list_resumable(self) -> list[OrchestrationRun]:
        """Runs that were mid-flight when the process last stopped - used on backend startup to
        resume execution (restart recovery, see engine.recover_stranded_runs).

        Excludes `waiting_for_user`: those are not stranded, they are deliberately parked on
        something only the user can supply (a secret, a credit top-up), and auto-resuming them
        would just burn straight back into the same wait. `created` is excluded too - nothing has
        started, and whoever created the row is responsible for launching it."""
        return (
            self.db.query(OrchestrationRun)
            .filter(OrchestrationRun.status.notin_(RUN_TERMINAL_STATUSES))
            .filter(OrchestrationRun.status.notin_(("created", "waiting_for_user")))
            .all()
        )

    def transition(self, run: OrchestrationRun, status: str, **fields: object) -> OrchestrationRun:
        validate_run_transition(run.status, status)
        previous_status = run.status
        now = datetime.now(UTC)
        if run.status != status:
            if status == "analyzing" and run.started_at is None:
                fields.setdefault("started_at", now)
            if status in RUN_TERMINAL_STATUSES:
                fields.setdefault("finished_at", now)
        run.status = status
        for key, value in fields.items():
            setattr(run, key, value)
        self.db.add(run)
        self.db.flush()
        if status in RUN_TERMINAL_STATUSES and previous_status not in RUN_TERMINAL_STATUSES:
            AgentTaskRepository(self.db).fail_orphan_tasks_for_run(run.id, run_status=status)
        return run

    def request_cancel(self, run: OrchestrationRun) -> OrchestrationRun:
        run.cancel_requested = True
        self.db.add(run)
        self.db.flush()
        return run


class OrchestrationPlanRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_version(
        self,
        *,
        run_id: uuid.UUID | str,
        version: int,
        graph_json: str,
        goal: str | None = None,
        reason: str | None = None,
        risks_json: str | None = None,
        acceptance_criteria_json: str | None = None,
        replan_reason: str | None = None,
    ) -> OrchestrationPlan:
        plan = OrchestrationPlan(
            run_id=run_id,
            version=version,
            status="draft",
            goal=goal,
            reason=reason,
            graph_json=graph_json,
            risks_json=risks_json,
            acceptance_criteria_json=acceptance_criteria_json,
            replan_reason=replan_reason,
        )
        self.db.add(plan)
        self.db.flush()
        return plan

    def get(self, plan_id: uuid.UUID | str) -> OrchestrationPlan | None:
        return (
            self.db.query(OrchestrationPlan).filter(OrchestrationPlan.id == plan_id).one_or_none()
        )

    def get_active(self, run_id: uuid.UUID | str) -> OrchestrationPlan | None:
        return (
            self.db.query(OrchestrationPlan)
            .filter(OrchestrationPlan.run_id == run_id, OrchestrationPlan.status == "active")
            .order_by(OrchestrationPlan.version.desc())
            .first()
        )

    def list_versions(self, run_id: uuid.UUID | str) -> list[OrchestrationPlan]:
        return (
            self.db.query(OrchestrationPlan)
            .filter(OrchestrationPlan.run_id == run_id)
            .order_by(OrchestrationPlan.version.asc())
            .all()
        )

    def next_version(self, run_id: uuid.UUID | str) -> int:
        current = (
            self.db.query(func.max(OrchestrationPlan.version))
            .filter(OrchestrationPlan.run_id == run_id)
            .scalar()
        )
        return int(current or 0) + 1

    def activate(self, plan: OrchestrationPlan) -> OrchestrationPlan:
        previous = self.get_active(plan.run_id)
        if previous and previous.id != plan.id:
            self.supersede(previous)
        validate_plan_transition(plan.status, "active")
        plan.status = "active"
        self.db.add(plan)
        self.db.flush()
        return plan

    def supersede(self, plan: OrchestrationPlan) -> OrchestrationPlan:
        validate_plan_transition(plan.status, "superseded")
        plan.status = "superseded"
        plan.superseded_at = datetime.now(UTC)
        self.db.add(plan)
        self.db.flush()
        return plan


class AgentTaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **fields: object) -> AgentTask:
        task = AgentTask(**fields)
        self.db.add(task)
        self.db.flush()
        return task

    def get(self, task_id: uuid.UUID | str) -> AgentTask | None:
        return self.db.query(AgentTask).filter(AgentTask.id == task_id).one_or_none()

    def get_locked(self, task_id: uuid.UUID | str) -> AgentTask | None:
        return (
            self.db.query(AgentTask).filter(AgentTask.id == task_id).with_for_update().one_or_none()
        )

    def get_by_local_id(self, plan_id: uuid.UUID | str, local_id: str) -> AgentTask | None:
        return (
            self.db.query(AgentTask)
            .filter(AgentTask.plan_id == plan_id, AgentTask.local_id == local_id)
            .one_or_none()
        )

    def list_by_run(self, run_id: uuid.UUID | str) -> list[AgentTask]:
        return (
            self.db.query(AgentTask)
            .filter(AgentTask.run_id == run_id)
            .order_by(AgentTask.sequence.asc(), AgentTask.created_at.asc())
            .all()
        )

    def list_by_plan(self, plan_id: uuid.UUID | str) -> list[AgentTask]:
        return (
            self.db.query(AgentTask)
            .filter(AgentTask.plan_id == plan_id)
            .order_by(AgentTask.sequence.asc(), AgentTask.created_at.asc())
            .all()
        )

    def transition(self, task: AgentTask, status: str, **fields: object) -> AgentTask:
        validate_task_transition(task.status, status)
        now = datetime.now(UTC)
        if status == "completed":
            # A repaired task can still carry the error from an earlier attempt.
            # Keeping it makes successful runs look failed in admin/debug views.
            fields.setdefault("error_code", None)
            fields.setdefault("error_message", None)
        if task.status != status:
            if status == "running" and task.started_at is None:
                fields.setdefault("started_at", now)
            if is_task_terminal(status):
                fields.setdefault("finished_at", now)
        task.status = status
        for key, value in fields.items():
            setattr(task, key, value)
        self.db.add(task)
        self.db.flush()
        return task

    def fail_orphan_tasks_for_run(
        self, run_id: uuid.UUID | str, *, run_status: str
    ) -> list[AgentTask]:
        """Fail tasks left non-terminal when their run reaches a terminal status."""
        reason = f"Run already {run_status}"
        failed: list[AgentTask] = []
        for task in self.list_by_run(run_id):
            if is_task_terminal(task.status):
                continue
            self.transition(
                task,
                "failed",
                error_code="run_terminal",
                error_message=reason,
            )
            failed.append(task)
        return failed

    def reset_stale_running(
        self, plan_id: uuid.UUID | str, *, stale_seconds: int = 300
    ) -> list[AgentTask]:
        """Tasks left in `running` after a process crash never finish on their own."""
        now = datetime.now(UTC)
        reset: list[AgentTask] = []
        for task in self.list_by_plan(plan_id):
            if task.status != "running":
                continue
            anchor = task.started_at or task.updated_at or task.created_at
            if anchor is None:
                continue
            age = (now - anchor).total_seconds()
            if age < stale_seconds:
                continue
            self.transition(
                task,
                "ready",
                error_message="recovered after stale running state",
            )
            reset.append(task)
        return reset

    def refresh_readiness(self, plan_id: uuid.UUID | str) -> list[AgentTask]:
        """Move `pending`/`blocked` tasks to `ready` once every local_id in their
        depends_on_json is `completed`; move them to `skipped` (terminal) if a dependency has
        `failed` / `cancelled` / `skipped` (can never become ready). Returns tasks that just
        became ready. Pure DAG-scheduling bookkeeping - does not start execution."""
        import json

        tasks = self.list_by_plan(plan_id)
        by_local_id = {t.local_id: t for t in tasks}
        newly_ready: list[AgentTask] = []
        for task in tasks:
            if task.status not in ("pending", "blocked"):
                continue
            deps: list[str] = json.loads(task.depends_on_json) if task.depends_on_json else []
            dep_tasks = [by_local_id[d] for d in deps if d in by_local_id]
            if any(d.status in ("failed", "cancelled", "skipped") for d in dep_tasks):
                if task.status != "skipped":
                    self.transition(task, "skipped")
                continue
            if all(d.status == "completed" for d in dep_tasks):
                self.transition(task, "ready")
                newly_ready.append(task)
            elif task.status != "blocked":
                self.transition(task, "blocked")
        return newly_ready


class WorkspaceLeaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _expire_stale(self, project_id: uuid.UUID | str) -> None:
        now = datetime.now(UTC)
        stale = (
            self.db.query(WorkspaceLease)
            .filter(
                WorkspaceLease.project_id == project_id,
                WorkspaceLease.released_at.is_(None),
                WorkspaceLease.expires_at < now,
            )
            .all()
        )
        for lease in stale:
            lease.released_at = now
        if stale:
            self.db.flush()

    def try_acquire_shared(
        self,
        *,
        project_id: uuid.UUID | str,
        run_id: uuid.UUID | str,
        holder: str,
        task_id: uuid.UUID | str | None = None,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> WorkspaceLease | None:
        """Attempt to become the sole `mode="shared"` writer for this project's workspace.
        Returns None if another active shared lease already exists - callers must not proceed
        to write the workspace in that case. Reaps expired leases first so a crashed holder
        never permanently blocks new work."""
        self._expire_stale(project_id)
        now = datetime.now(UTC)
        lease = WorkspaceLease(
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            holder=holder,
            mode="shared",
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        try:
            with self.db.begin_nested():
                self.db.add(lease)
                self.db.flush()
        except IntegrityError:
            return None
        return lease

    def acquire_isolated(
        self,
        *,
        project_id: uuid.UUID | str,
        run_id: uuid.UUID | str,
        task_id: uuid.UUID | str,
        holder: str,
        worktree_path: str,
        branch_name: str,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> WorkspaceLease:
        """No uniqueness constraint blocks this - many isolated-worktree leases can be active
        concurrently for the same project, each in its own branch/directory."""
        lease = WorkspaceLease(
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            holder=holder,
            mode="isolated",
            worktree_path=worktree_path,
            branch_name=branch_name,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        self.db.add(lease)
        self.db.flush()
        return lease

    def renew(
        self, lease: WorkspaceLease, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    ) -> WorkspaceLease:
        lease.expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        self.db.add(lease)
        self.db.flush()
        return lease

    def release(self, lease: WorkspaceLease) -> WorkspaceLease:
        try:
            if lease.released_at is None:
                lease.released_at = datetime.now(UTC)
                self.db.add(lease)
                self.db.flush()
        except ObjectDeletedError:
            # Project deletion cascades workspace_leases. A concurrent task unwinding after
            # cancellation must treat the already-deleted lease as successfully released.
            return lease
        return lease

    def get_active_shared(self, project_id: uuid.UUID | str) -> WorkspaceLease | None:
        self._expire_stale(project_id)
        return (
            self.db.query(WorkspaceLease)
            .filter(
                WorkspaceLease.project_id == project_id,
                WorkspaceLease.mode == "shared",
                WorkspaceLease.released_at.is_(None),
            )
            .one_or_none()
        )

    def list_active_isolated(self, project_id: uuid.UUID | str) -> list[WorkspaceLease]:
        self._expire_stale(project_id)
        return (
            self.db.query(WorkspaceLease)
            .filter(
                WorkspaceLease.project_id == project_id,
                WorkspaceLease.mode == "isolated",
                WorkspaceLease.released_at.is_(None),
            )
            .all()
        )


class RunEventRepository:
    """Append-only. `append()` assigns the next `seq` from `MAX(seq)+1` scoped to `run_id` -
    safe because only the single active engine loop for a given run appends events (enforced by
    the run's own execution lease upstream in engine.py), so there is no concurrent writer to
    race against within one run."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def append(
        self,
        *,
        run_id: uuid.UUID | str,
        event_type: str,
        payload_json: str,
        task_id: uuid.UUID | str | None = None,
    ) -> RunEvent:
        next_seq = (
            self.db.query(func.coalesce(func.max(RunEvent.seq), 0))
            .filter(RunEvent.run_id == run_id)
            .scalar()
        )
        event = RunEvent(
            run_id=run_id,
            task_id=task_id,
            seq=int(next_seq or 0) + 1,
            event_type=event_type,
            payload_json=payload_json,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def list_since(
        self, run_id: uuid.UUID | str, *, after_seq: int = 0, limit: int = 1000
    ) -> list[RunEvent]:
        return (
            self.db.query(RunEvent)
            .filter(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq.asc())
            .limit(limit)
            .all()
        )

    def list_paginated(
        self, run_id: uuid.UUID | str, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[RunEvent], int]:
        base = self.db.query(RunEvent).filter(RunEvent.run_id == run_id)
        total = base.count()
        rows = base.order_by(RunEvent.seq.asc()).offset(offset).limit(limit).all()
        return rows, total

    def latest_seq(self, run_id: uuid.UUID | str) -> int:
        value = (
            self.db.query(func.coalesce(func.max(RunEvent.seq), 0))
            .filter(RunEvent.run_id == run_id)
            .scalar()
        )
        return int(value or 0)


class McpServerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_enabled(self) -> list[McpServer]:
        return self.db.query(McpServer).filter(McpServer.enabled.is_(True)).all()

    def list_all(self) -> list[McpServer]:
        return self.db.query(McpServer).order_by(McpServer.name.asc()).all()

    def get_by_name(self, name: str) -> McpServer | None:
        return self.db.query(McpServer).filter(McpServer.name == name).one_or_none()

    def get(self, server_id: uuid.UUID | str) -> McpServer | None:
        return self.db.query(McpServer).filter(McpServer.id == server_id).one_or_none()
