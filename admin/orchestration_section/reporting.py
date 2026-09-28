"""Read-only orchestration aggregates for admin dashboards.

Pure functions (no `request`), mirroring backend/services/orchestration/metrics.py but scoped
to a single project where noted. No secret values or free-text model output in aggregates.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.models import Project
from orchestration_section.models import AgentTask, OrchestrationRun, RunEvent

RUN_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_STUCK_AFTER = timedelta(hours=2)
_NON_TERMINAL_FOR_STUCK = frozenset(
    {"completed", "failed", "cancelled", "waiting_for_user", "created"}
)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _window(days: int) -> tuple:
    now = timezone.now()
    return now - timedelta(days=days), now


def _window_hours(window_hours: int) -> tuple:
    now = timezone.now()
    return now - timedelta(hours=window_hours), now


def _runs_for_project(project_id: UUID | str, start, end):
    return OrchestrationRun.objects.filter(
        project_id=project_id, created_at__gte=start, created_at__lt=end
    )


def _stuck_qs(qs):
    cutoff = timezone.now() - _STUCK_AFTER
    return qs.filter(
        started_at__isnull=False,
        started_at__lt=cutoff,
    ).exclude(status__in=_NON_TERMINAL_FOR_STUCK)


def project_orchestration_summary(project_id: UUID | str, days: int = 30) -> dict:
    """KPIs and recent runs for one project."""
    start, now = _window(days)
    runs = _runs_for_project(project_id, start, now)

    by_status = dict(runs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    total = sum(by_status.values())
    completed = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    cancelled = by_status.get("cancelled", 0)
    decided = completed + failed
    stuck = _stuck_qs(runs).count()

    credits = runs.aggregate(total=Sum("credits_used"))["total"] or 0
    avg_credits = runs.filter(status="completed").aggregate(v=Avg("credits_used"))["v"]

    recent = list(
        runs.order_by("-created_at")[:25].values(
            "id",
            "status",
            "goal",
            "credits_used",
            "plan_version",
            "provider",
            "model",
            "created_at",
            "started_at",
            "finished_at",
            "error_code",
        )
    )

    return {
        "days": days,
        "runs_total": total,
        "runs_completed": completed,
        "runs_failed": failed,
        "runs_cancelled": cancelled,
        "runs_stuck": stuck,
        "success_rate": _rate(completed, decided),
        "credits_total": credits,
        "credits_avg_successful": round(float(avg_credits), 2) if avg_credits is not None else None,
        "by_status": by_status,
        "recent_runs": recent,
    }


def run_timeline(run_id: UUID | str) -> dict:
    """Run metadata, tasks, and ordered event timeline with parsed payloads."""
    run = OrchestrationRun.objects.filter(id=run_id).first()
    if run is None:
        return {"run": None, "tasks": [], "events": []}

    tasks = list(
        AgentTask.objects.filter(run_id=run_id).values(
            "id",
            "local_id",
            "sequence",
            "title",
            "role",
            "execution_kind",
            "status",
            "attempt",
            "max_attempts",
            "skill_id",
            "capability_id",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
        )
    )

    events = []
    for event in RunEvent.objects.filter(run_id=run_id).order_by("seq"):
        payload = event.payload_json
        try:
            payload_pretty = json.dumps(json.loads(payload), ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            payload_pretty = payload
        events.append(
            {
                "id": str(event.id),
                "seq": event.seq,
                "event_type": event.event_type,
                "task_id": str(event.task_id) if event.task_id else None,
                "created_at": event.created_at,
                "payload_pretty": payload_pretty,
            }
        )

    run_data = {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "chat_id": str(run.chat_id),
        "message_id": str(run.message_id) if run.message_id else None,
        "user_id": str(run.user_id),
        "status": run.status,
        "goal": run.goal,
        "original_request": run.original_request,
        "complexity": run.complexity,
        "plan_version": run.plan_version,
        "current_task_id": str(run.current_task_id) if run.current_task_id else None,
        "context_summary": run.context_summary,
        "base_commit_sha": run.base_commit_sha,
        "final_commit_sha": run.final_commit_sha,
        "provider": run.provider,
        "model": run.model,
        "credit_budget": run.credit_budget,
        "credits_used": run.credits_used,
        "cancel_requested": run.cancel_requested,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "metadata_json": run.metadata_json,
    }

    return {"run": run_data, "tasks": tasks, "events": events}


def project_metrics(project_id: UUID | str, window_hours: int = 24) -> dict:
    """Project-scoped orchestration health metrics (mirrors backend collect_metrics)."""
    start, now = _window_hours(window_hours)
    runs = _runs_for_project(project_id, start, now)

    by_status = dict(runs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    runs_total = sum(by_status.values())
    runs_completed = by_status.get("completed", 0)
    runs_failed = by_status.get("failed", 0)
    runs_cancelled = by_status.get("cancelled", 0)
    runs_waiting = by_status.get("waiting_for_user", 0)
    decided = runs_completed + runs_failed
    runs_stuck = _stuck_qs(runs).count()

    completed_runs = runs.filter(
        status="completed",
        started_at__isnull=False,
        finished_at__isnull=False,
    )
    completed_agg = completed_runs.aggregate(avg_credits=Avg("credits_used"))
    durations = [
        (finished - started).total_seconds()
        for started, finished in completed_runs.values_list("started_at", "finished_at")
        if started and finished
    ]
    avg_seconds = round(sum(durations) / len(durations), 2) if durations else None

    replanned = runs.filter(plan_version__gt=1).count()

    task_qs = AgentTask.objects.filter(run__project_id=project_id, run__created_at__gte=start)
    tasks_by_status = dict(
        task_qs.values("status").annotate(c=Count("id")).values_list("status", "c")
    )
    tasks_total = sum(tasks_by_status.values())
    tasks_completed = tasks_by_status.get("completed", 0)
    tasks_failed = tasks_by_status.get("failed", 0)

    first_try = task_qs.filter(status="completed", attempt__lte=1).count()
    repair_attempted = task_qs.filter(attempt__gt=1).count()
    repaired = tasks_completed - first_try

    tasks_by_role = dict(task_qs.values("role").annotate(c=Count("id")).values_list("role", "c"))
    tasks_by_kind = dict(
        task_qs.values("execution_kind").annotate(c=Count("id")).values_list("execution_kind", "c")
    )

    event_qs = RunEvent.objects.filter(run__project_id=project_id, run__created_at__gte=start)
    events = dict(
        event_qs.values("event_type").annotate(c=Count("id")).values_list("event_type", "c")
    )

    loop_failures = runs.filter(error_code__in=("loop_detected", "no_progress")).count()

    return {
        "window_hours": window_hours,
        "runs_total": runs_total,
        "runs_completed": runs_completed,
        "runs_failed": runs_failed,
        "runs_cancelled": runs_cancelled,
        "runs_waiting_for_user": runs_waiting,
        "runs_stuck": runs_stuck,
        "run_success_rate": _rate(runs_completed, decided),
        "tasks_total": tasks_total,
        "tasks_completed": tasks_completed,
        "task_acceptance_rate": _rate(tasks_completed, tasks_completed + tasks_failed),
        "first_attempt_success_rate": _rate(first_try, tasks_completed),
        "repair_success_rate": _rate(repaired, repair_attempted),
        "replan_rate": _rate(replanned, runs_total),
        # Phase-start events do not record the outcome of the real build/deploy checks.
        "build_success_rate": None,
        "deploy_success_rate": None,
        "runtime_verification_success_rate": None,
        "skill_match_rate": _rate(tasks_by_kind.get("skill", 0), tasks_total),
        "mcp_error_rate": None,
        "repeated_failure_loops": loop_failures,
        "average_credits_per_successful_run": (
            round(float(completed_agg["avg_credits"]), 2)
            if completed_agg["avg_credits"] is not None
            else None
        ),
        "average_run_seconds": avg_seconds,
        "tasks_by_role": tasks_by_role,
        "tasks_by_execution_kind": tasks_by_kind,
        "events_by_type": events,
    }


def fleet_summary(days: int = 30) -> dict:
    """Fleet-wide orchestration KPIs for the admin analytics page."""
    start, now = _window(days)
    runs = OrchestrationRun.objects.filter(created_at__gte=start, created_at__lt=now)
    by_status = dict(runs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    total = sum(by_status.values())
    completed = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    decided = completed + failed
    stuck = _stuck_qs(runs).count()
    active = runs.exclude(status__in=RUN_TERMINAL | frozenset({"waiting_for_user"})).count()

    credits = runs.aggregate(total=Sum("credits_used"))["total"] or 0
    avg_credits = runs.filter(status="completed").aggregate(v=Avg("credits_used"))["v"]

    completed_runs = runs.filter(
        status="completed", started_at__isnull=False, finished_at__isnull=False
    )
    durations = [
        (finished - started).total_seconds()
        for started, finished in completed_runs.values_list("started_at", "finished_at")
        if started and finished
    ]

    return {
        "days": days,
        "runs_total": total,
        "runs_completed": completed,
        "runs_failed": failed,
        "runs_active": active,
        "runs_stuck": stuck,
        "success_rate": _rate(completed, decided),
        "credits_total": credits,
        "credits_avg_successful": round(float(avg_credits), 2) if avg_credits is not None else None,
        "average_run_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "by_status": by_status,
    }


def daily_runs_series(days: int = 30) -> list[dict]:
    start, now = _window(days)
    rows = (
        OrchestrationRun.objects.filter(created_at__gte=start, created_at__lt=now)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            failed=Count("id", filter=Q(status="failed")),
            credits=Sum("credits_used"),
        )
        .order_by("day")
    )
    return [
        {
            "day": row["day"].isoformat() if row["day"] else "",
            "total": row["total"],
            "completed": row["completed"],
            "failed": row["failed"],
            "credits": row["credits"] or 0,
        }
        for row in rows
    ]


def runs_by_status(days: int = 30) -> list[dict]:
    start, now = _window(days)
    rows = (
        OrchestrationRun.objects.filter(created_at__gte=start, created_at__lt=now)
        .values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return [{"status": row["status"], "count": row["count"]} for row in rows]


def tasks_by_role_fleet(days: int = 30) -> list[dict]:
    start, now = _window(days)
    rows = (
        AgentTask.objects.filter(run__created_at__gte=start, run__created_at__lt=now)
        .values("role")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return [{"role": row["role"] or "unknown", "count": row["count"]} for row in rows]


def fleet_health_metrics(window_hours: int = 24) -> dict:
    """Aggregate health metrics across all projects."""
    start, now = _window_hours(window_hours)
    runs = OrchestrationRun.objects.filter(created_at__gte=start, created_at__lt=now)
    by_status = dict(runs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    runs_total = sum(by_status.values())
    runs_completed = by_status.get("completed", 0)
    runs_failed = by_status.get("failed", 0)
    decided = runs_completed + runs_failed
    replanned = runs.filter(plan_version__gt=1).count()

    task_qs = AgentTask.objects.filter(run__created_at__gte=start)
    tasks_total = task_qs.count()
    tasks_completed = task_qs.filter(status="completed").count()
    tasks_failed = task_qs.filter(status="failed").count()
    first_try = task_qs.filter(status="completed", attempt__lte=1).count()

    event_qs = RunEvent.objects.filter(run__created_at__gte=start)
    events = dict(
        event_qs.values("event_type").annotate(c=Count("id")).values_list("event_type", "c")
    )

    return {
        "window_hours": window_hours,
        "tasks_total": tasks_total,
        "run_success_rate": _rate(runs_completed, decided),
        "task_acceptance_rate": _rate(tasks_completed, tasks_completed + tasks_failed),
        "first_attempt_success_rate": _rate(first_try, tasks_completed),
        "replan_rate": _rate(replanned, runs_total),
        "build_success_rate": None,
        "deploy_success_rate": None,
        "events_by_type": events,
        "tasks_by_execution_kind": dict(
            task_qs.values("execution_kind")
            .annotate(c=Count("id"))
            .values_list("execution_kind", "c")
        ),
    }


def active_runs(limit: int = 50) -> list[dict]:
    """Non-terminal runs with current task context for live admin monitoring."""
    terminal = RUN_TERMINAL | frozenset({"waiting_for_user", "created"})
    rows = (
        OrchestrationRun.objects.exclude(status__in=terminal)
        .select_related("project")
        .order_by("-updated_at")[:limit]
    )
    task_ids = [run.current_task_id for run in rows if run.current_task_id]
    tasks_by_id = {str(task.id): task for task in AgentTask.objects.filter(id__in=task_ids)}
    active: list[dict] = []
    for run in rows:
        task = tasks_by_id.get(str(run.current_task_id)) if run.current_task_id else None
        if task is None:
            task = (
                AgentTask.objects.filter(run_id=run.id, status="running")
                .order_by("-started_at")
                .first()
            )
        project_name = getattr(run.project, "name", None)
        if project_name is None:
            project_name = (
                Project.objects.filter(id=run.project_id).values_list("name", flat=True).first()
            )
        active.append(
            {
                "run_id": str(run.id),
                "project_id": str(run.project_id),
                "project_name": project_name or "—",
                "status": run.status,
                "goal": (run.goal or run.original_request or "")[:160],
                "provider": run.provider,
                "model": run.model,
                "credits_used": run.credits_used,
                "credit_budget": run.credit_budget,
                "updated_at": run.updated_at,
                "started_at": run.started_at,
                "task_title": task.title if task else None,
                "task_status": task.status if task else None,
                "task_local_id": task.local_id if task else None,
            }
        )
    return active


def decimal_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Not JSON serializable: {type(value)}")
