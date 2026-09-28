"""Aggregate orchestration metrics (spec section 20).

The per-event durable log (events_bus/RunEvent) and the per-row state on OrchestrationRun /
AgentTask are the raw material; this module is the aggregation layer on top - "what percentage of
runs succeed", "how often do we replan", "what does a successful run cost" - the questions you
actually ask when deciding whether the engine is healthy, none of which are answerable by reading
one run's events.

Deliberately computed on demand with plain SQL aggregates over a bounded window rather than
maintained as running counters: the source rows are already durable and indexed, an admin opening
a dashboard is not a hot path, and a counter that can drift out of sync with the rows it
summarizes is worse than no counter.

Contains no secret values and no free-text model output - only counts, rates and durations - so
it is safe to expose to an admin/debug view (spec: "Добавь admin/debug view без секретов").
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.workspace_lease import WorkspaceLease

# A run still non-terminal this long after it started has no plausible legitimate reason to be
# running - every task has its own wall-clock budget well under this - so it is counted as stuck.
_STUCK_AFTER = timedelta(hours=2)


def _rate(numerator: int, denominator: int) -> float | None:
    """None (not 0.0) when there is nothing to divide by - "no runs yet" and "0% success" are
    very different signals and a dashboard must not render them identically."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


@dataclass
class OrchestrationMetrics:
    window_hours: int
    runs_total: int = 0
    runs_completed: int = 0
    runs_failed: int = 0
    runs_cancelled: int = 0
    runs_waiting_for_user: int = 0
    runs_stuck: int = 0
    run_success_rate: float | None = None

    tasks_total: int = 0
    tasks_completed: int = 0
    task_acceptance_rate: float | None = None
    first_attempt_success_rate: float | None = None
    repair_success_rate: float | None = None

    replan_rate: float | None = None
    build_success_rate: float | None = None
    deploy_success_rate: float | None = None
    runtime_verification_success_rate: float | None = None
    skill_match_rate: float | None = None
    mcp_error_rate: float | None = None
    repeated_failure_loops: int = 0
    workspace_lease_conflicts: int = 0

    average_credits_per_successful_run: float | None = None
    average_run_seconds: float | None = None
    tasks_by_role: dict[str, int] = field(default_factory=dict)
    tasks_by_execution_kind: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _run_scope(*, since: datetime, project_id: UUID | None):
    clauses = [OrchestrationRun.created_at >= since]
    if project_id is not None:
        clauses.append(OrchestrationRun.project_id == project_id)
    return clauses


def collect_metrics(
    db: Session, *, window_hours: int = 24, project_id: UUID | None = None
) -> OrchestrationMetrics:
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    metrics = OrchestrationMetrics(window_hours=window_hours)
    run_scope = _run_scope(since=since, project_id=project_id)

    run_rows = db.execute(
        select(OrchestrationRun.status, func.count())
        .where(*run_scope)
        .group_by(OrchestrationRun.status)
    ).all()
    by_status = {status: count for status, count in run_rows}
    metrics.runs_total = sum(by_status.values())
    metrics.runs_completed = by_status.get("completed", 0)
    metrics.runs_failed = by_status.get("failed", 0)
    metrics.runs_cancelled = by_status.get("cancelled", 0)
    metrics.runs_waiting_for_user = by_status.get("waiting_for_user", 0)
    # Denominator excludes cancelled and waiting_for_user: a user pressing Stop, or a run parked
    # on a secret the user hasn't supplied, says nothing about whether the engine works.
    decided = metrics.runs_completed + metrics.runs_failed
    metrics.run_success_rate = _rate(metrics.runs_completed, decided)

    metrics.runs_stuck = (
        db.execute(
            select(func.count())
            .select_from(OrchestrationRun)
            .where(
                *run_scope,
                OrchestrationRun.status.notin_(
                    ("completed", "failed", "cancelled", "waiting_for_user", "created")
                ),
                OrchestrationRun.started_at.is_not(None),
                OrchestrationRun.started_at < datetime.now(UTC) - _STUCK_AFTER,
            )
        ).scalar_one()
        or 0
    )

    completed_agg = db.execute(
        select(
            func.avg(OrchestrationRun.credits_used),
            func.avg(
                func.extract("epoch", OrchestrationRun.finished_at - OrchestrationRun.started_at)
            ),
        ).where(
            *run_scope,
            OrchestrationRun.status == "completed",
            OrchestrationRun.started_at.is_not(None),
            OrchestrationRun.finished_at.is_not(None),
        )
    ).one()
    metrics.average_credits_per_successful_run = (
        round(float(completed_agg[0]), 2) if completed_agg[0] is not None else None
    )
    metrics.average_run_seconds = (
        round(float(completed_agg[1]), 2) if completed_agg[1] is not None else None
    )

    # Replan rate is per-RUN, not per-plan-row: plan_version counts versions, so anything above 1
    # means that run needed at least one replan.
    replanned = (
        db.execute(
            select(func.count())
            .select_from(OrchestrationRun)
            .where(*run_scope, OrchestrationRun.plan_version > 1)
        ).scalar_one()
        or 0
    )
    metrics.replan_rate = _rate(replanned, metrics.runs_total)

    task_rows = db.execute(
        select(AgentTask.status, func.count())
        .join(OrchestrationRun, OrchestrationRun.id == AgentTask.run_id)
        .where(*run_scope)
        .group_by(AgentTask.status)
    ).all()
    tasks_by_status = {status: count for status, count in task_rows}
    metrics.tasks_total = sum(tasks_by_status.values())
    metrics.tasks_completed = tasks_by_status.get("completed", 0)
    metrics.task_acceptance_rate = _rate(
        metrics.tasks_completed,
        metrics.tasks_completed + tasks_by_status.get("failed", 0),
    )

    first_try = (
        db.execute(
            select(func.count())
            .select_from(AgentTask)
            .join(OrchestrationRun, OrchestrationRun.id == AgentTask.run_id)
            .where(
                *run_scope,
                AgentTask.status == "completed",
                AgentTask.attempt <= 1,
            )
        ).scalar_one()
        or 0
    )
    metrics.first_attempt_success_rate = _rate(first_try, metrics.tasks_completed)
    # Of the tasks that needed more than one attempt, how many eventually got accepted - i.e.
    # whether repair/retry is actually worth running at all.
    repaired = metrics.tasks_completed - first_try
    repair_attempted = (
        db.execute(
            select(func.count())
            .select_from(AgentTask)
            .join(OrchestrationRun, OrchestrationRun.id == AgentTask.run_id)
            .where(*run_scope, AgentTask.attempt > 1)
        ).scalar_one()
        or 0
    )
    metrics.repair_success_rate = _rate(repaired, repair_attempted)

    for column, target in (
        (AgentTask.role, "tasks_by_role"),
        (AgentTask.execution_kind, "tasks_by_execution_kind"),
    ):
        rows = db.execute(
            select(column, func.count())
            .join(OrchestrationRun, OrchestrationRun.id == AgentTask.run_id)
            .where(*run_scope)
            .group_by(column)
        ).all()
        setattr(metrics, target, {str(value): count for value, count in rows})

    # Skill match rate: of the tasks the router had to place, how many landed on a deterministic
    # skill rather than falling through to a specialist agent.
    skill_tasks = metrics.tasks_by_execution_kind.get("skill", 0)
    metrics.skill_match_rate = _rate(skill_tasks, metrics.tasks_total)

    # These event names only mark immediate run-status transitions. Real build,
    # deployment and runtime outcomes are checked elsewhere, so a run_completed
    # count cannot serve as the success numerator for any of these rates.
    # task_failed includes failures unrelated to MCP calls, so it cannot be used
    # as an MCP error numerator either. Leave the field unset until call outcomes
    # are recorded separately.

    # A run that failed with a loop-detector verdict - the "we are going in circles" signal that
    # matters most when tuning failure policy.
    metrics.repeated_failure_loops = (
        db.execute(
            select(func.count())
            .select_from(OrchestrationRun)
            .where(
                *run_scope,
                OrchestrationRun.error_code.in_(("loop_detected", "no_progress")),
            )
        ).scalar_one()
        or 0
    )

    lease_scope = [WorkspaceLease.acquired_at >= since, WorkspaceLease.released_at.is_(None)]
    if project_id is not None:
        lease_scope.append(WorkspaceLease.project_id == project_id)
    metrics.workspace_lease_conflicts = (
        db.execute(
            select(func.count()).select_from(WorkspaceLease).where(*lease_scope)
        ).scalar_one()
        or 0
    )

    return metrics
