"""OrchestrationEngine (spec sections 3/4/23): the top-level driver that turns one persisted
OrchestrationRun into planned, executed, validated, committed AgentTasks, until the run reaches
a terminal status (completed/failed/cancelled) or pauses at waiting_for_user for the user to
supply a secret/service/decision.

`run_orchestration()` is the single entry point. It is safe to call again for a run that is
already partway through (a fresh process picking up after a restart, or resuming after
waiting_for_user) - every phase re-derives its starting point from the DB rather than assuming
in-memory continuity, which is what makes a run survive a backend/worker restart (Definition of
Done: "продолжает выполнение после падения/рестарта backend"). What does NOT survive a restart:
LoopDetector/BudgetTracker/ReplanGate state is per-call, in-memory (mirrors failure_policy.py's
own documented scope) - a resumed run's loop/budget tracking restarts empty except for
`credits_used`, which is reseeded from the persisted OrchestrationRun.credits_used column so a
resumed run can't blow through its credit budget just by being restarted enough times.

Session lifecycle: a fresh Session (via `db_factory`) is opened per phase (initial setup/
planning, each outer-loop iteration, finalization) and closed before returning to the caller or
looping - never held open across the whole run. Exception: `_run_one_task` holds ONE session for
an entire task's attempt-retry sequence (bounded by max_attempts, default 3), since GitTransaction
Manager's `begin()`/`complete()` pairing carries live ORM objects (the lease, the task) that must
not cross a session boundary - this mirrors the existing precedent of chat.py's own turn handler,
which already holds one session open across a whole (potentially multi-minute) agent turn.

Scheduling: each outer-loop iteration dispatches a "wave" of ready tasks concurrently via
asyncio.gather (see `_select_wave`), not just one - every ready parallel_read_only task (no
lease at all, workspace_isolation.py's own words: "nothing to serialize against since nothing
writes") and every ready isolated_worktree task (its own lease + real `git worktree`, can't
collide with anything else) go out together, plus at most ONE ready shared_sequential task -
that mode's lease has a single active holder per project, so sending more than one at once
would just have the rest sit in the lease-contention wait loop for no benefit, and risks a
timeout there that sequential-only dispatch never had. Each wave member runs in its OWN Session
(`_run_task_wave_member`) - a SQLAlchemy Session is never safe to share across concurrently
running coroutines. What is still NOT built: IntegrationAgent-driven multi-branch merge
orchestration wiring an isolated_worktree task's finished branch back into the shared workspace
(project_git.merge_worktree_branch exists and is tested, but nothing calls it yet - seeing two
isolated tasks actually complete concurrently in the same run is the natural trigger for that
follow-up, not built here).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.agent_task import AgentTask
from src.db.models.orchestration_plan import OrchestrationPlan
from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.project import Project
from src.db.models.user import User
from src.db.session import SessionLocal
from src.services import project_git
from src.services.byok import has_valid_key
from src.services.file_context import (
    ImageAttachment,
    extract_image_attachments,
    load_run_attachment_ids,
)
from src.services.model_access import reasoning_effort_for_user
from src.services.model_pricing import estimate_model_usage_cost
from src.services.orchestration import events_bus
from src.services.orchestration.budget import (
    BudgetCheckResult,
    BudgetLimits,
    BudgetStatus,
    BudgetTracker,
    charge_credits_for_run,
    estimate_task_cost,
)
from src.services.orchestration.cancellation import CancellationToken
from src.services.orchestration.capability_provider import (
    PLATFORM_CAPABILITY_BUILD_CHECK,
    PLATFORM_CAPABILITY_PREVIEW_CHECK,
    McpCapabilityProvider,
    PlatformToolCapabilityProvider,
    SkillCapabilityProvider,
)
from src.services.orchestration.capability_router import CapabilityRouter
from src.services.orchestration.context_engine import ContextEngine
from src.services.orchestration.contract_builder import build_task_contract, score_task_contract
from src.services.orchestration.executors import (
    AgentExecutor,
    CodexContainerExecutor,
    DeterministicExecutor,
    HttpProviderExecutor,
    McpExecutor,
    SkillExecutor,
    TaskContext,
)
from src.services.orchestration.failure_policy import (
    FailureDecision,
    FailureEvaluation,
    LoopDetector,
    evaluate_failure,
)
from src.services.orchestration.git_transaction import GitTransactionManager
from src.services.orchestration.mcp import registry as mcp_registry
from src.services.orchestration.planner import ExecutionPlan, generate_plan
from src.services.orchestration.project_finalize import finalize_project_after_completed_run
from src.services.orchestration.replanner import (
    ReplanGate,
    generate_replan,
    summarize_replan_reason,
)
from src.services.orchestration.repository import (
    AgentTaskRepository,
    McpServerRepository,
    OrchestrationPlanRepository,
    OrchestrationRunRepository,
)
from src.services.orchestration.review_handoff import (
    build_judge_fix_planned_tasks,
    can_enqueue_judge_fix,
    collect_review_todos,
    collect_todos_from_task_result,
    judge_fix_round_count,
    parse_task_result_json,
    verdict_from_task_result,
)
from src.services.orchestration.role_policy import get_role_policy
from src.services.orchestration.schemas import (
    CapabilityContext,
    ContextItem,
    FailureClass,
    PlannedTask,
    SpecialistRole,
    TaskContract,
    TaskEvidence,
    WriteScope,
)
from src.services.orchestration.schemas import ExecutionKind as _ExecutionKind
from src.services.orchestration.skills.base import registry as skill_registry
from src.services.orchestration.status import is_run_terminal, is_task_terminal
from src.services.orchestration.workspace_isolation import WorkspaceIsolationManager, make_holder_id
from src.services.project_services import (
    ProjectServiceError,
    ensure_service_request,
    user_facing_secret_keys,
)
from src.services.system_settings import resolve_platform_api_key
from src.services.workspace import project_dir as project_workspace_dir

logger = logging.getLogger(__name__)

_LEASE_CONTENTION_MAX_WAIT_SECONDS = 30
_LEASE_CONTENTION_POLL_SECONDS = 2.0

# workspace_isolation.py: these two modes never contend for the single shared_sequential lease
# (parallel_read_only takes no lease at all; isolated_worktree's own lease never contends with
# another task's) - anything else (today just shared_sequential, but any future/unrecognized
# mode defaults here too) is treated as needing exclusive access and gets the cautious,
# one-at-a-time treatment.
_CONCURRENT_WORKSPACE_MODES = frozenset({"parallel_read_only", "isolated_worktree"})

_RETRYABLE_DECISIONS = frozenset(
    {
        FailureDecision.RETRY,
        FailureDecision.REPAIR,
        FailureDecision.REPLACE_EXECUTOR,
        FailureDecision.REPLACE_SKILL,
    }
)


async def _ensure_build_evidence(
    *,
    contract: TaskContract,
    project_id: object,
    existing: dict | None,
) -> dict | None:
    """Server-side build evidence for contracts that declared a build validation step.

    Coding executors (Codex/HTTP) only populate build_result when the agent happened to call
    build_project mid-turn. Without a fallback here, every Implementer/UI/BuildFixer task that
    skipped that tool fails validation with "no build_result evidence is present" - the exact
    loop that burns replan budget on otherwise-valid work. Mirrors evidence.run_static_checks:
    declared validation levels must be collected by the server, not left as an agent courtesy.
    """
    if existing is not None:
        return existing
    if not any(step.kind == "build" for step in contract.validation_steps):
        return None
    result = await PlatformToolCapabilityProvider().invoke(
        PLATFORM_CAPABILITY_BUILD_CHECK,
        {},
        CapabilityContext(
            project_id=uuid.UUID(str(project_id)),
            run_id=contract.run_id,
            task_id=contract.task_id,
            role=contract.role,
        ),
    )
    if isinstance(result.output, dict) and result.output:
        return result.output
    return {
        "ok": False,
        "log": result.error or "build_check produced no output",
        "log_tail": (result.error or "build_check produced no output")[-4000:],
    }


async def _ensure_preview_and_runtime_evidence(
    *,
    contract: TaskContract,
    project_id: object,
    build_result: dict | None,
    preview_existing: dict | None,
    runtime_existing: dict | None,
) -> tuple[dict | None, dict | None]:
    """Collect declared browser/runtime evidence on the server after a successful build.

    Codex runs with shell access, not AIRuntime's internal preview/runtime tools. Requiring the
    model to manufacture these evidence payloads made valid changes fail validation and retry
    until the credit budget was exhausted. A preview is the deterministic pre-deploy runtime
    check: it boots the built image in an isolated network and drives it with Chromium.
    """
    step_kinds = {step.kind for step in contract.validation_steps}
    needs_preview = "preview" in step_kinds and preview_existing is None
    needs_runtime = "runtime" in step_kinds and runtime_existing is None
    if not needs_preview and not needs_runtime:
        return preview_existing, runtime_existing
    if not build_result or not build_result.get("ok"):
        return preview_existing, runtime_existing

    collected_preview = preview_existing
    if collected_preview is None:
        result = await PlatformToolCapabilityProvider().invoke(
            PLATFORM_CAPABILITY_PREVIEW_CHECK,
            {"paths": ["/"]},
            CapabilityContext(
                project_id=uuid.UUID(str(project_id)),
                run_id=contract.run_id,
                task_id=contract.task_id,
                role=contract.role,
            ),
        )
        if isinstance(result.output, dict) and result.output:
            collected_preview = result.output
        else:
            error = result.error or "preview_check produced no output"
            collected_preview = {
                "status": "failed",
                "pages": [],
                "fatal_errors": [error],
                "warnings": [],
            }

    collected_runtime = runtime_existing
    if needs_runtime:
        preview_status = str(collected_preview.get("status") or "failed")
        pages = collected_preview.get("pages")
        collected_runtime = {
            "ok": preview_status in {"passed", "issues_found"}
            and isinstance(pages, list)
            and bool(pages),
            "source": "isolated_preview",
            "preview_status": preview_status,
            "pages_checked": len(pages) if isinstance(pages, list) else 0,
            "fatal_errors": collected_preview.get("fatal_errors") or [],
        }

    return (
        collected_preview if "preview" in step_kinds else preview_existing,
        collected_runtime,
    )


def _inherit_evidence_from_dependencies(
    dependency_tasks: list[AgentTask],
) -> tuple[dict | None, dict | None, dict | None]:
    """Reuse build/preview/runtime evidence collected by completed dependency tasks.

    QA/visual review tasks often declare preview validation but run as read-only skills that
    do not re-run preview themselves — without inheritance they fail with "no preview_result".
    """
    build_result: dict | None = None
    preview_result: dict | None = None
    runtime_result: dict | None = None
    for dep in dependency_tasks:
        if dep.status != "completed" or not dep.evidence_json:
            continue
        try:
            evidence = TaskEvidence.model_validate_json(dep.evidence_json)
        except Exception:  # noqa: BLE001 - malformed evidence must not abort the run
            continue
        if build_result is None and evidence.build_result:
            build_result = evidence.build_result
        if preview_result is None and evidence.preview_result:
            preview_result = evidence.preview_result
        if runtime_result is None and evidence.runtime_health_result:
            runtime_result = evidence.runtime_health_result
    return build_result, preview_result, runtime_result


_DELIVERABLE_TASK_ROLES = frozenset({"implementer", "ui_ux_specialist", "integration_agent"})
_REVIEW_TASK_ROLES = frozenset({"qa_reviewer", "security_reviewer"})
_QA_JUDGE_ROLES = frozenset({"qa_reviewer"})


def _try_recover_qa_deadlock(
    db: Session,
    *,
    run: OrchestrationRun,
    plan_id: uuid.UUID | str,
    plan_tasks: list[AgentTask],
    non_terminal: list[AgentTask],
    task_repo: AgentTaskRepository,
) -> bool:
    """Skip blocked QA when the main deliverable is already accepted.

    Prevents ``plan deadlocked`` failures when optional QA chains stall after implementer
    completed (common when budget pauses or replan leaves redundant QA nodes).
    """
    if not any(t.status == "completed" and t.role in _DELIVERABLE_TASK_ROLES for t in plan_tasks):
        return False
    stuck = [t for t in non_terminal if t.status in ("blocked", "pending", "waiting_for_user")]
    if not stuck or not all(t.role in _REVIEW_TASK_ROLES for t in stuck):
        return False
    for task in stuck:
        task_repo.transition(
            task,
            "skipped",
            error_message="optional QA skipped after main deliverable was accepted",
        )
    task_repo.refresh_readiness(plan_id)
    events_bus.emit(
        db,
        run_id=run.id,
        event_type="trace",
        payload={
            "category": "scheduling",
            "action": "qa_deadlock_recovery",
            "summary": f"skipped {len(stuck)} blocked QA task(s)",
        },
    )
    return True


def _user_has_run_budget(db: Session, *, user_id: uuid.UUID, provider_name: str) -> bool:
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        return False
    if user.credits_balance > 0:
        return True
    return has_valid_key(db, user, provider_name)


def _reasoning_effort_for_project(db, project) -> str | None:
    """Codex reasoning effort for the project owner's plan.

    Restricted (free) plans run a cheaper effort so a one-time grant is not spent in two turns.
    """
    from src.db.models.user import User

    owner = db.get(User, project.user_id)
    if owner is None:
        return None
    return reasoning_effort_for_user(db, owner)


def _select_workspace_mode(
    role: SpecialistRole, planned_task: PlannedTask, *, concurrent_write_task_count: int
) -> str:
    policy = get_role_policy(role)
    if policy.write_scope_ceiling == WriteScope.NONE:
        return "parallel_read_only"
    # Always shared for writers until IntegrationAgent merges isolated worktrees
    # (merge_worktree_branch exists but is never called). Isolated mode left accepted
    # commits on side branches while Deploy built the shared disk tree — Versions empty,
    # chat deploy flaky. concurrent_write_task_count kept for the future merge wiring.
    _ = (planned_task, concurrent_write_task_count)
    return "shared_sequential"


def _select_wave(ready: list[AgentTask]) -> list[AgentTask]:
    """Which ready tasks run THIS iteration, concurrently. Every ready task in a mode that can't
    contend with anything (_CONCURRENT_WORKSPACE_MODES) goes out together; at most one
    shared_sequential task joins them (`ready`'s own ordering - sequence, then created_at - keeps
    that pick deterministic). `ready` is never empty when this is called, and every task falls
    into exactly one of the two buckets, so the result is never empty either."""
    concurrent = [t for t in ready if t.workspace_mode in _CONCURRENT_WORKSPACE_MODES]
    sequential = [t for t in ready if t.workspace_mode not in _CONCURRENT_WORKSPACE_MODES]
    return concurrent + sequential[:1]


# Which "this capability is now running" event each execution kind announces itself with. The
# specialist/codex kinds are already covered by task_started, so they get no second event.
_CAPABILITY_START_EVENTS: dict[str, str] = {
    _ExecutionKind.SKILL.value: "skill_started",
    _ExecutionKind.MCP.value: "capability_started",
    _ExecutionKind.DETERMINISTIC_VALIDATION.value: "capability_started",
}


def _build_executor(
    kind: str, *, db: Session, mcp_repo: McpServerRepository
) -> AgentExecutor | None:
    """None means "no executor call at all" - only ever true for user_input (never actually
    produced by capability_router.route() today, but a task row could carry it defensively) or
    an unrecognized execution_kind (should not happen; treated the same, safely, rather than
    raising)."""
    if kind == _ExecutionKind.CODEX_TASK.value:
        return CodexContainerExecutor()
    if kind == _ExecutionKind.SPECIALIST_AGENT.value:
        return HttpProviderExecutor()
    if kind == _ExecutionKind.INTEGRATION.value:
        # See module docstring: no concurrent isolated branches are actually scheduled in this
        # cut, so there is nothing to "merge" yet - IntegrationAgent still gets a real contract-
        # driven turn against the current shared workspace (its system prompt already tells it
        # to confirm the project builds before claiming success).
        return CodexContainerExecutor()
    if kind == _ExecutionKind.DETERMINISTIC_VALIDATION.value:
        return DeterministicExecutor(PlatformToolCapabilityProvider())
    if kind == _ExecutionKind.SKILL.value:
        return SkillExecutor(SkillCapabilityProvider(skill_registry, db=db))
    if kind == _ExecutionKind.MCP.value:
        return McpExecutor(McpCapabilityProvider(mcp_repo))
    return None


def _reconstruct_planned_task(plan: OrchestrationPlan, task: AgentTask) -> PlannedTask:
    execution_plan = ExecutionPlan.model_validate_json(plan.graph_json)
    for planned in execution_plan.tasks:
        if planned.local_id == task.local_id:
            return planned
    raise ValueError(f"local_id {task.local_id!r} not found in plan {plan.id} graph_json")


async def _materialize_plan_tasks(
    db: Session,
    *,
    run: OrchestrationRun,
    plan: OrchestrationPlan,
    execution_plan: ExecutionPlan,
    project_type: str,
    router: CapabilityRouter,
) -> list[AgentTask]:
    task_repo = AgentTaskRepository(db)
    mcp_repo = McpServerRepository(db)
    mcp_ids_by_role: dict[SpecialistRole, frozenset[str]] = {}
    for role in {t.role for t in execution_plan.tasks}:
        pairs = await mcp_registry.list_capabilities_for_role(mcp_repo, role=role)
        mcp_ids_by_role[role] = frozenset(cap.id for _server, cap in pairs)

    # Precomputed once against the *planned* roles (routing never actually changes a task's
    # write-vs-read-only nature - can_execute_role's collapse-to-Implementer branch is dead now
    # that specialist agents always run) so _select_workspace_mode can tell a genuinely
    # concurrent write task from a lone one before any task row exists yet.
    concurrent_write_task_count = sum(
        1
        for t in execution_plan.tasks
        if not t.dependencies and get_role_policy(t.role).write_scope_ceiling != WriteScope.NONE
    )

    created: list[AgentTask] = []
    for sequence, planned in enumerate(execution_plan.tasks):
        decision = await router.route(
            planned_task=planned,
            project_type=project_type,
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
            mcp_capability_ids=mcp_ids_by_role.get(planned.role, frozenset()),
        )
        task = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id=planned.local_id,
            sequence=sequence,
            title=planned.title,
            role=decision.effective_role.value,
            execution_kind=decision.execution_kind.value,
            status="pending",
            max_attempts=get_role_policy(decision.effective_role).default_max_attempts,
            depends_on_json=json.dumps(planned.dependencies),
            skill_id=decision.skill_id,
            capability_id=decision.capability_id,
            workspace_mode=_select_workspace_mode(
                decision.effective_role,
                planned,
                concurrent_write_task_count=concurrent_write_task_count,
            ),
        )
        created.append(task)
    task_repo.refresh_readiness(plan.id)
    return created


async def _append_planned_tasks(
    db: Session,
    *,
    run: OrchestrationRun,
    plan: OrchestrationPlan,
    project: Project,
    planned_tasks: list[PlannedTask],
    router: CapabilityRouter,
) -> list[AgentTask]:
    """Add follow-up nodes to the *current* plan without a full LLM replan."""
    if not planned_tasks:
        return []
    execution_plan = ExecutionPlan.model_validate_json(plan.graph_json)
    existing_ids = {task.local_id for task in execution_plan.tasks}
    to_add = [task for task in planned_tasks if task.local_id not in existing_ids]
    if not to_add:
        return []
    execution_plan.tasks.extend(to_add)
    plan.graph_json = execution_plan.model_dump_json()
    db.add(plan)
    db.flush()

    task_repo = AgentTaskRepository(db)
    existing_rows = task_repo.list_by_plan(plan.id)
    sequence = (max((task.sequence for task in existing_rows), default=-1)) + 1
    mcp_repo = McpServerRepository(db)
    created: list[AgentTask] = []
    for planned in to_add:
        pairs = await mcp_registry.list_capabilities_for_role(mcp_repo, role=planned.role)
        decision = await router.route(
            planned_task=planned,
            project_type=project.type,
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
            mcp_capability_ids=frozenset(cap.id for _server, cap in pairs),
        )
        created.append(
            task_repo.create(
                run_id=run.id,
                plan_id=plan.id,
                local_id=planned.local_id,
                sequence=sequence,
                title=planned.title,
                role=decision.effective_role.value,
                execution_kind=decision.execution_kind.value,
                status="pending",
                max_attempts=get_role_policy(decision.effective_role).default_max_attempts,
                depends_on_json=json.dumps(planned.dependencies),
                skill_id=decision.skill_id,
                capability_id=decision.capability_id,
                workspace_mode=_select_workspace_mode(
                    decision.effective_role,
                    planned,
                    concurrent_write_task_count=1,
                ),
            )
        )
        sequence += 1
    newly_ready = task_repo.refresh_readiness(plan.id)
    for task in newly_ready:
        events_bus.emit(
            db,
            run_id=run.id,
            task_id=task.id,
            event_type="task_ready",
            payload={"local_id": task.local_id, "title": task.title},
        )
    return created


async def _maybe_enqueue_judge_fix_from_completed_reviews(
    db: Session,
    *,
    run: OrchestrationRun,
    plan: OrchestrationPlan,
    project: Project,
    completed_task_ids: set[uuid.UUID | str],
) -> list[AgentTask]:
    """After a judge wave: one implementer punch-list, optional second judge, then ship."""
    task_repo = AgentTaskRepository(db)
    plan_tasks = task_repo.list_by_plan(plan.id)
    if not can_enqueue_judge_fix(plan_tasks, max_rounds=settings.orchestration_max_review_rounds):
        return []

    execution_plan = ExecutionPlan.model_validate_json(plan.graph_json)
    planned_by_id = {task.local_id: task for task in execution_plan.tasks}
    completed_ids = {str(task_id) for task_id in completed_task_ids}
    revising: list[AgentTask] = []
    todos: list[str] = []
    for task in plan_tasks:
        if task.role not in _QA_JUDGE_ROLES or task.status != "completed":
            continue
        if str(task.id) not in completed_ids:
            continue
        claimed = parse_task_result_json(task.result_json)
        item_todos = collect_todos_from_task_result(claimed)
        verdict = verdict_from_task_result(claimed)
        if verdict not in {"revise", "blocked"} and not item_todos:
            continue
        revising.append(task)
        todos.extend(item_todos)

    todos = collect_review_todos({"verdict": "revise", "todos": todos})
    if not revising or not todos:
        return []

    room = settings.orchestration_max_plan_tasks - len(plan_tasks)
    if room < 1:
        return []

    relevant_paths: list[str] = []
    suggested_skills: list[str] = []
    for qa_task in revising:
        planned = planned_by_id.get(qa_task.local_id)
        if planned is None:
            continue
        for dep_id in planned.dependencies:
            dep = planned_by_id.get(dep_id)
            if dep is not None:
                relevant_paths.extend(dep.relevant_paths)
        suggested_skills.extend(planned.suggested_skills)
        if qa_task.skill_id:
            suggested_skills.append(qa_task.skill_id)
    if not relevant_paths:
        relevant_paths = ["public/index.html", "Dockerfile"]
    if not suggested_skills:
        suggested_skills = ["visual_preview_review"]
    # Preserve order while dropping duplicates.
    relevant_paths = list(dict.fromkeys(relevant_paths))
    suggested_skills = list(dict.fromkeys(suggested_skills))

    planned_tasks = build_judge_fix_planned_tasks(
        todos=todos,
        qa_local_ids=[task.local_id for task in revising],
        relevant_paths=relevant_paths,
        suggested_skills=suggested_skills,
        round_number=judge_fix_round_count(plan_tasks) + 1,
        include_followup_qa=room >= 2,
    )
    created = await _append_planned_tasks(
        db,
        run=run,
        plan=plan,
        project=project,
        planned_tasks=planned_tasks,
        router=CapabilityRouter(skill_registry=skill_registry),
    )
    if created:
        events_bus.emit(
            db,
            run_id=run.id,
            event_type="trace",
            payload={
                "category": "scheduling",
                "action": "judge_handoff",
                "todos": todos,
                "local_ids": [task.local_id for task in created],
            },
        )
    return created


async def _persist_new_plan(
    db: Session,
    *,
    run: OrchestrationRun,
    project: Project,
    execution_plan: ExecutionPlan,
    context_summary: str,
    replan_reason: str | None,
    router: CapabilityRouter,
) -> OrchestrationPlan:
    plan_repo = OrchestrationPlanRepository(db)
    version = plan_repo.next_version(run.id)
    plan = plan_repo.create_version(
        run_id=run.id,
        version=version,
        graph_json=execution_plan.model_dump_json(),
        goal=execution_plan.goal,
        risks_json=json.dumps([r.model_dump() for r in execution_plan.risks]),
        acceptance_criteria_json=json.dumps(
            [c.model_dump() for c in execution_plan.final_acceptance_criteria]
        ),
        replan_reason=replan_reason,
    )
    await _materialize_plan_tasks(
        db,
        run=run,
        plan=plan,
        execution_plan=execution_plan,
        project_type=project.type,
        router=router,
    )
    plan_repo.activate(plan)
    run_repo = OrchestrationRunRepository(db)
    run_repo.transition(
        run,
        "running",
        goal=execution_plan.goal,
        complexity=execution_plan.complexity,
        plan_version=version,
        context_summary=context_summary,
    )
    events_bus.emit(
        db,
        run_id=run.id,
        event_type="plan_created" if version == 1 else "plan_revised",
        payload={
            "version": version,
            "goal": execution_plan.goal,
            "task_count": len(execution_plan.tasks),
            "reason": replan_reason,
        },
    )
    # After plan_created, never before: the plan's dependency-free tasks are already `ready` by
    # the time _materialize_plan_tasks returns, so without announcing them here the very first
    # wave would silently skip task_ready and only dependency-unblocked tasks would emit it.
    for task in AgentTaskRepository(db).list_by_plan(plan.id):
        if task.status == "ready":
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_ready",
                payload={"local_id": task.local_id, "title": task.title},
            )
    return plan


def _walk_run_to_completed(db: Session, run: OrchestrationRun) -> None:
    """status.py's RUN_TRANSITIONS only reaches "completed" via
    running -> validating -> integrating -> building -> deploying -> verifying_runtime, by
    design: a run is not really "done" until the project has actually been built, deployed, and
    runtime-checked (mirroring chat.py's own existing verify/deploy/runtime-check phases for the
    non-orchestrated turn path). All planned tasks being individually accepted (each already
    build/preview/runtime-validated per its own contract's validation_steps) means there is
    nothing left for a SEPARATE run-level build/deploy pass to add in THIS cut, so these
    intermediate states are walked through immediately - real deployment triggering (subdomain
    allocation, `create_deployment_for_project`, the deploy-wait polling loop) stays the CALLER's
    responsibility (chat.py wiring), exactly as it already is for the non-orchestrated path,
    rather than engine.py reaching into that project-wide subsystem itself."""
    run_repo = OrchestrationRunRepository(db)
    # Completion pipeline is legal from `running`. A run that finished its last task while
    # still in planning/replanning/waiting_for_user would otherwise raise IllegalStatusTransition
    # here and become an `internal_error`.
    if run.status in ("planning", "replanning", "waiting_for_user"):
        run_repo.transition(run, "running")
    # Each phase gets its own event so the run's durable log (and the UI replaying it) shows the
    # same phases the run status walks through, instead of jumping straight from the last task
    # to run_completed with no trace of why.
    phase_events = {
        "integrating": "integration_started",
        "building": "build_started",
        "deploying": "deploy_started",
        "verifying_runtime": "runtime_verification_started",
    }
    for status in ("validating", "integrating", "building", "deploying", "verifying_runtime"):
        if run.status == status:
            continue
        run_repo.transition(run, status)
        event_type = phase_events.get(status)
        if event_type is not None:
            events_bus.emit(db, run_id=run.id, event_type=event_type, payload={})
    workspace_root = project_workspace_dir(run.project_id)
    final_sha = project_git.current_head_sha(workspace_root)
    run_repo.transition(run, "completed", final_commit_sha=final_sha)
    events_bus.emit(db, run_id=run.id, event_type="run_completed", payload={})


async def _ensure_planned(
    db: Session,
    *,
    run: OrchestrationRun,
    project: Project,
    context_engine: ContextEngine,
    router: CapabilityRouter,
    provider_name: str,
    model: str,
    api_key: str,
) -> OrchestrationPlan:
    existing = OrchestrationPlanRepository(db).get_active(run.id)
    if existing is not None:
        return existing

    workspace_root = project_workspace_dir(project.id)
    await asyncio.to_thread(project_git.init_repo_if_needed, workspace_root)
    git_sha = await asyncio.to_thread(project_git.current_head_sha, workspace_root)
    context_summary = await asyncio.to_thread(
        context_engine.build_global_context_summary,
        original_request=run.original_request or "",
        project=project,
        workspace_root=workspace_root,
        git_sha=git_sha,
        plan_goal=None,
        task_summaries=[],
    )
    planning_usage: list[dict] = []
    generation = await generate_plan(
        user_message=run.original_request or "",
        project_context_summary=context_summary,
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        max_tasks=settings.orchestration_max_plan_tasks,
        usage_sink=planning_usage.append,
        db=db,
        run_id=run.id,
    )
    user = db.query(User).filter(User.id == run.user_id).one_or_none()
    for usage in planning_usage:
        _record_run_usage_charge(
            db,
            run=run,
            project=project,
            user=user,
            provider_name=provider_name,
            model=model,
            usage=usage,
            summary_text="",
            category="planning",
        )
    return await _persist_new_plan(
        db,
        run=run,
        project=project,
        execution_plan=generation.plan,
        context_summary=context_summary,
        replan_reason=None,
        router=router,
    )


@dataclass
class _TaskAttemptOutcome:
    signal: Literal["completed", "failed", "waiting_for_user", "replan", "run_should_stop"]
    evaluation: FailureEvaluation | None = None
    # Human-readable "why" for a run_should_stop, e.g. the exceeded budget dimension - surfaced
    # to the user on OrchestrationRun.error_message rather than left only in the logs.
    detail: str | None = None


def _format_engine_error(exc: BaseException, *, limit: int = 400) -> str:
    """Compact exception text for OrchestrationRun/AgentTask.error_message (ops alerts)."""
    text = f"{type(exc).__name__}: {exc}".strip()
    text = " ".join(text.split())
    return (text[:limit] or type(exc).__name__).strip()


def _fail_task_after_unhandled_error(
    db_factory: Callable[[], Session],
    *,
    task_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    exc: BaseException,
    replanning_enabled: bool,
) -> _TaskAttemptOutcome:
    """Turn a bug inside one task into a task-level failure instead of failing the whole run.

    Production `internal_error` alerts were almost always a single task throwing
    (DetachedInstanceError, IllegalStatusTransition, executor crash). Swallowing that at the
    run boundary hid the real exception and killed unrelated in-flight work.
    """
    message = _format_engine_error(exc)
    db = db_factory()
    try:
        task = AgentTaskRepository(db).get(task_id)
        if task is None:
            return _TaskAttemptOutcome("failed")
        if task.status == "completed":
            return _TaskAttemptOutcome("completed")
        if task.status == "waiting_for_user":
            return _TaskAttemptOutcome("waiting_for_user")
        if not is_task_terminal(task.status):
            AgentTaskRepository(db).transition(
                task,
                "failed",
                error_code="internal_error",
                error_message=message,
            )
            events_bus.emit(
                db,
                run_id=run_id,
                task_id=task.id,
                event_type="task_failed",
                payload={"reason": "internal_error", "error": message},
            )
            db.commit()
    except Exception:  # noqa: BLE001 - failure-marking itself must never raise into the gather
        logger.exception("run_orchestration: failed to mark task %s failed after crash", task_id)
    finally:
        db.close()

    evaluation = FailureEvaluation(
        failure_class=FailureClass.AGENT_ERROR,
        decision=FailureDecision.REPLAN if replanning_enabled else FailureDecision.FAIL,
        loop_detected=False,
        reason=message,
    )
    return _TaskAttemptOutcome(
        "replan" if replanning_enabled else "failed",
        evaluation=evaluation,
    )


def _record_run_usage_charge(
    db: Session,
    *,
    run: OrchestrationRun,
    project: Project,
    user: User | None,
    provider_name: str,
    model: str,
    usage: dict | None,
    summary_text: str,
    category: str,
    task_id: uuid.UUID | None = None,
    attempt: int | None = None,
) -> int:
    usage_cost = estimate_model_usage_cost(provider=provider_name, model=model, usage=usage)
    cost = (
        usage_cost.credits
        if usage_cost is not None
        else estimate_task_cost(usage=usage, summary_text=summary_text)
    )
    if user is not None:
        charge_credits_for_run(
            db,
            user,
            project_id=project.id,
            amount=cost,
            project_name=project.name,
            provider_name=provider_name,
            model=model,
            usage_cost=usage_cost,
        )
    run.credits_used = (run.credits_used or 0) + cost
    usage_snapshot = json.loads(run.token_usage_json or "{}")
    charges = usage_snapshot.setdefault("charges", [])
    charges.append(
        {
            "category": category,
            "task_id": str(task_id) if task_id else None,
            "attempt": attempt,
            "provider": provider_name,
            "model": model,
            "credits": cost,
            "usage": usage or {},
            "provider_cost_usd_micros": (
                usage_cost.provider_cost_usd_micros if usage_cost else None
            ),
        }
    )
    run.token_usage_json = json.dumps(usage_snapshot)
    db.add(run)
    return cost


async def _run_one_task(
    db: Session,
    *,
    run: OrchestrationRun,
    project: Project,
    task: AgentTask,
    plan: OrchestrationPlan,
    context_engine: ContextEngine,
    isolation: WorkspaceIsolationManager,
    provider_name: str,
    model: str,
    api_key: str,
    run_images: list[ImageAttachment],
    cancellation: CancellationToken,
    loop_detector: LoopDetector,
    budget: BudgetTracker,
    replanning_enabled: bool,
) -> _TaskAttemptOutcome:
    task_repo = AgentTaskRepository(db)
    workspace_root = project_workspace_dir(project.id)
    git_txn = GitTransactionManager(db, isolation)
    planned_task = _reconstruct_planned_task(plan, task)
    dep_local_ids: list[str] = json.loads(task.depends_on_json or "[]")
    dependency_tasks = [
        t for t in (task_repo.get_by_local_id(plan.id, d) for d in dep_local_ids) if t is not None
    ]
    user = db.query(User).filter(User.id == run.user_id).one_or_none()

    error_context: list[ContextItem] = []
    while True:
        if cancellation.is_cancelled:
            task_repo.transition(task, "cancelled")
            return _TaskAttemptOutcome("run_should_stop")

        budget_check = budget.check()
        if budget_check.status != BudgetStatus.EXCEEDED and user is not None:
            # Run credit_budget alone is not enough: ledger can hit zero while the run budget
            # still has headroom (charges clamp to 0 and only an email fires). Stop visibly.
            db.refresh(user)
            if user.credits_balance <= 0 and not has_valid_key(db, user, provider_name):
                budget_check = BudgetCheckResult(
                    status=BudgetStatus.EXCEEDED,
                    dimension="account_balance",
                    message=(
                        "Закончились кредиты — работа агента приостановлена. "
                        "Пополните баланс в профиле или подключите свой API-ключ."
                    ),
                )
        if budget_check.status == BudgetStatus.EXCEEDED:
            # "waiting_for_user", not "skipped"/"failed": this task never got a chance to run,
            # and the thing it is waiting on (the user topping up credits) is recoverable. Both
            # of the terminal options would silently drop this work forever; parking it here is
            # what lets resume_run() re-ready it after a top-up (spec section 18).
            task_repo.transition(
                task,
                "waiting_for_user",
                error_code="budget_exceeded",
                error_message=budget_check.message,
            )
            return _TaskAttemptOutcome("run_should_stop", detail=budget_check.message)

        task_repo.transition(task, "running")
        events_bus.emit(
            db,
            run_id=run.id,
            task_id=task.id,
            event_type="task_started",
            payload={"local_id": task.local_id, "title": task.title, "attempt": task.attempt + 1},
        )
        db.commit()

        git_sha = await asyncio.to_thread(project_git.current_head_sha, workspace_root)
        available_files = await asyncio.to_thread(
            context_engine.list_workspace_files_cached,
            project.id,
            workspace_root,
            git_sha,
        )
        contract = await asyncio.to_thread(
            build_task_contract,
            task=task,
            planned_task=planned_task,
            run_goal=run.goal or run.original_request or "",
            context_engine=context_engine,
            project=project,
            workspace_root=workspace_root,
            git_sha=git_sha,
            dependency_tasks=dependency_tasks,
            available_files=available_files,
            registered_skill_ids=skill_registry.all_ids(),
            error_context_items=error_context or None,
            default_timeout_seconds=settings.orchestration_task_default_timeout_seconds,
        )

        completeness = score_task_contract(contract)
        if not completeness.passed:
            task_repo.transition(
                task,
                "failed",
                error_code="contract_incomplete",
                error_message=f"contract incomplete: missing {completeness.missing_required}",
            )
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_failed",
                payload={"reason": "contract_incomplete", "missing": completeness.missing_required},
            )
            db.commit()
            return _TaskAttemptOutcome(
                "replan" if replanning_enabled else "failed",
                evaluation=FailureEvaluation(
                    failure_class=FailureClass.CONTRACT_INCOMPLETE,
                    decision=FailureDecision.REPLAN if replanning_enabled else FailureDecision.FAIL,
                    loop_detected=False,
                    reason="task contract failed completeness scoring",
                ),
            )

        holder = make_holder_id()
        handle = None
        waited = 0.0
        while handle is None:
            handle = await asyncio.to_thread(
                git_txn.begin,
                task=task,
                contract=contract,
                project_id=project.id,
                run_id=run.id,
                project_root=workspace_root,
                holder=holder,
                ttl_seconds=settings.orchestration_run_lease_ttl_seconds,
            )
            if handle is not None:
                break
            if waited >= _LEASE_CONTENTION_MAX_WAIT_SECONDS or cancellation.is_cancelled:
                task_repo.transition(
                    task,
                    "failed",
                    error_code="timeout",
                    error_message="workspace lease contention timed out",
                )
                db.commit()
                return _TaskAttemptOutcome("failed")
            await asyncio.sleep(_LEASE_CONTENTION_POLL_SECONDS)
            waited += _LEASE_CONTENTION_POLL_SECONDS

        executor = _build_executor(task.execution_kind, db=db, mcp_repo=McpServerRepository(db))
        if executor is None:
            await asyncio.to_thread(git_txn.abort, handle)
            task_repo.transition(task, "waiting_for_user")
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="waiting_for_user",
                payload={"reason": f"execution_kind {task.execution_kind!r} needs user input"},
            )
            db.commit()
            return _TaskAttemptOutcome("waiting_for_user")

        task_context = TaskContext(
            workspace_root=handle.acquired.workspace_root,
            project_id=str(project.id),
            provider_name=provider_name,
            model=model,
            api_key=api_key,
            db=db,
            reasoning_effort=_reasoning_effort_for_project(db, project),
            images=run_images,
        )
        # Granular per-capability audit trail (spec section 11's "каждый вызов записывается в
        # audit log"): task_started alone doesn't say WHICH skill/MCP capability ran, and after
        # the fact the task row only carries the router's decision, not that it was reached.
        started_event = _CAPABILITY_START_EVENTS.get(task.execution_kind)
        if started_event is not None:
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type=started_event,
                payload={
                    "local_id": task.local_id,
                    "skill_id": task.skill_id,
                    "capability_id": task.capability_id,
                    "execution_kind": task.execution_kind,
                },
            )
            db.commit()

        try:
            agent_result = await executor.execute(contract, task_context, cancellation)
        except Exception:
            try:
                await asyncio.to_thread(git_txn.abort, handle)
            except Exception:  # noqa: BLE001 - lease TTL is the fallback if abort also fails
                logger.exception("run_orchestration: failed to abort git txn for task %s", task.id)
            raise

        # A persisted cancellation may have arrived while the executor was blocked in a model
        # turn. Observe it before any build/preview fallback can create fresh resources during
        # project deletion.
        db.refresh(run)
        if run.cancel_requested and not cancellation.is_cancelled:
            cancellation.cancel("cancellation requested while task was running")

        if task.execution_kind == _ExecutionKind.SKILL.value:
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="skill_completed",
                payload={
                    "local_id": task.local_id,
                    "skill_id": task.skill_id,
                    "status": agent_result.task_result.status
                    if agent_result.task_result
                    else "failed",
                },
            )

        budget.record_task()
        summary_text = agent_result.task_result.summary if agent_result.task_result else ""
        cost = _record_run_usage_charge(
            db,
            run=run,
            project=project,
            user=user,
            provider_name=provider_name,
            model=model,
            usage=agent_result.usage,
            summary_text=summary_text,
            category="task",
            task_id=task.id,
            attempt=task.attempt,
        )
        budget.record_credits(cost)

        # Tell the user what this run has actually cost so far, and warn once it is close to the
        # ceiling - BudgetStatus.APPROACHING exists precisely so spending is visible BEFORE the
        # run parks itself on an exhausted budget rather than only after.
        post_charge = budget.check()
        events_bus.emit(
            db,
            run_id=run.id,
            task_id=task.id,
            event_type="budget_updated",
            payload={
                "credits_used": run.credits_used,
                "credit_budget": run.credit_budget,
                "cost_rub": run.credits_used / settings.billing_credits_per_rub,
                "provider": provider_name,
                "model": model,
                "status": str(post_charge.status),
                "detail": post_charge.message,
            },
        )

        if cancellation.is_cancelled:
            await asyncio.to_thread(git_txn.abort, handle)
            task_repo.transition(task, "cancelled")
            db.commit()
            return _TaskAttemptOutcome("run_should_stop", detail=cancellation.reason)

        task_repo.transition(task, "collecting_evidence")
        db.commit()

        secret_requests = (
            agent_result.task_result.requested_secrets if agent_result.task_result else []
        )
        service_requests = (
            agent_result.task_result.requested_services if agent_result.task_result else []
        )
        # Services are auto-provisioned unconditionally (DB row only, no Docker call - real
        # provisioning happens at deploy time) regardless of whether this attempt otherwise
        # validated - unlike a missing secret, nothing here needs the user, so this must never
        # pause the run. Mirrors chat.py's own non-orchestrated turn handler, which does the same
        # unconditionally right after a turn rather than gating it on turn success.
        for kind in service_requests:
            try:
                ensure_service_request(db, project, kind, f"Запрошено задачей «{task.title}»")
            except ProjectServiceError:
                logger.warning(
                    "orchestration: ignoring unrecognized service kind %r requested by task %s",
                    kind,
                    task.id,
                )
        if service_requests:
            db.commit()
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_progress",
                payload={"note": f"Подключаю сервисы: {', '.join(service_requests)}"},
            )

        try:
            dep_build, dep_preview, dep_runtime = _inherit_evidence_from_dependencies(
                dependency_tasks
            )
            build_result = await _ensure_build_evidence(
                contract=contract,
                project_id=project.id,
                existing=agent_result.build_result or dep_build,
            )
            preview_result, runtime_health_result = await _ensure_preview_and_runtime_evidence(
                contract=contract,
                project_id=project.id,
                build_result=build_result,
                preview_existing=agent_result.preview_result or dep_preview,
                runtime_existing=agent_result.runtime_health_result or dep_runtime,
            )
            outcome = await asyncio.to_thread(
                git_txn.complete,
                handle,
                result=agent_result.task_result,
                build_result=build_result,
                lint_result=agent_result.lint_result,
                test_result=agent_result.test_result,
                preview_result=preview_result,
                runtime_health_result=runtime_health_result,
                service_requests=service_requests,
                secret_requests=secret_requests,
                usage=agent_result.usage,
                raw_logs=agent_result.raw_logs or None,
            )
        except Exception:
            try:
                await asyncio.to_thread(git_txn.abort, handle)
            except Exception:  # noqa: BLE001 - lease TTL is the fallback if abort also fails
                logger.exception("run_orchestration: failed to abort git txn for task %s", task.id)
            raise

        task_repo.transition(
            task,
            "validating",
            result_json=agent_result.task_result.model_dump_json()
            if agent_result.task_result
            else None,
            evidence_json=outcome.evidence.model_dump_json(),
            validation_result_json=outcome.validation_result.model_dump_json(),
        )
        events_bus.emit(
            db,
            run_id=run.id,
            task_id=task.id,
            event_type="task_validating",
            payload={"local_id": task.local_id, "accepted": outcome.validation_result.accepted},
        )
        db.commit()

        if outcome.committed and outcome.validation_result.accepted:
            task_repo.transition(
                task,
                "completed",
                accepted_commit_sha=outcome.accepted_commit_sha,
                attempt=task.attempt + 1,
            )
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_completed",
                payload={"local_id": task.local_id, "commit_sha": outcome.accepted_commit_sha},
            )
            db.commit()
            return _TaskAttemptOutcome("completed")

        user_secrets = user_facing_secret_keys(secret_requests)
        if user_secrets:
            task_repo.transition(task, "waiting_for_user", attempt=task.attempt + 1)
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="waiting_for_secret",
                payload={"requested_secrets": user_secrets},
            )
            db.commit()
            return _TaskAttemptOutcome("waiting_for_user")

        # A judge verdict is a punch list, not a run failure. Completing the QA node lets the
        # scheduler hand TODOs to one implementer pass instead of Ralph-looping through replan.
        if task.role in _QA_JUDGE_ROLES:
            task_repo.transition(
                task,
                "completed",
                attempt=task.attempt + 1,
                accepted_commit_sha=outcome.accepted_commit_sha,
            )
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_completed",
                payload={
                    "local_id": task.local_id,
                    "commit_sha": outcome.accepted_commit_sha,
                    "judge_handoff": True,
                },
            )
            db.commit()
            return _TaskAttemptOutcome("completed")

        error_message = "; ".join(
            f.message for f in outcome.validation_result.findings if not f.passed
        ) or (agent_result.error or "validation did not accept this attempt")
        evaluation = evaluate_failure(
            attempt=task.attempt + 1,
            max_attempts=task.max_attempts,
            replanning_enabled=replanning_enabled,
            loop_detector=loop_detector,
            error_message=error_message,
            validation_result=outcome.validation_result,
            diff_stat=outcome.evidence.git_diff_stat,
        )

        if evaluation.decision in _RETRYABLE_DECISIONS:
            task_repo.transition(
                task,
                "repairing",
                attempt=task.attempt + 1,
                error_code=evaluation.failure_class.value,
                error_message=error_message,
            )
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_repairing",
                payload={"reason": evaluation.reason},
            )
            db.commit()
            error_item = context_engine.build_error_context_item(
                kind="build_error",
                log_text=error_message,
                title=f"Предыдущая попытка ({evaluation.failure_class.value})",
                provenance="previous_attempt",
            )
            error_context = [error_item] if error_item else []
            continue

        if evaluation.decision == FailureDecision.WAIT_FOR_USER:
            task_error_code = (
                "loop_detected" if evaluation.loop_detected else evaluation.failure_class.value
            )
            task_repo.transition(
                task,
                "waiting_for_user",
                attempt=task.attempt + 1,
                error_code=task_error_code,
                error_message=error_message,
            )
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="waiting_for_user",
                payload={"reason": evaluation.reason},
            )
            db.commit()
            return _TaskAttemptOutcome("waiting_for_user", evaluation=evaluation)

        if evaluation.decision == FailureDecision.REPLAN:
            task_repo.transition(
                task,
                "failed",
                attempt=task.attempt + 1,
                error_code=evaluation.failure_class.value,
                error_message=f"{error_message} (superseded by replan)",
            )
            events_bus.emit(
                db,
                run_id=run.id,
                task_id=task.id,
                event_type="task_failed",
                payload={"reason": evaluation.reason},
            )
            db.commit()
            return _TaskAttemptOutcome("replan", evaluation=evaluation)

        task_repo.transition(
            task,
            "failed",
            attempt=task.attempt + 1,
            error_code=evaluation.failure_class.value,
            error_message=error_message,
        )
        events_bus.emit(
            db,
            run_id=run.id,
            task_id=task.id,
            event_type="task_failed",
            payload={"reason": evaluation.reason},
        )
        db.commit()
        return _TaskAttemptOutcome("failed", evaluation=evaluation)


async def _run_task_wave_member(
    *,
    db_factory: Callable[[], Session],
    run_id: uuid.UUID | str,
    project_id: uuid.UUID | str,
    plan_id: uuid.UUID | str,
    task_id: uuid.UUID | str,
    provider_name: str,
    model: str,
    api_key: str,
    run_images: list[ImageAttachment],
    cancellation: CancellationToken,
    loop_detector: LoopDetector,
    budget: BudgetTracker,
    replanning_enabled: bool,
) -> tuple[uuid.UUID | str, _TaskAttemptOutcome, str | None]:
    """One member of a concurrently-dispatched wave (see this module's docstring and
    `_select_wave`). Opens its OWN Session - never safe to share one across coroutines running
    concurrently under asyncio.gather. budget/loop_detector/cancellation ARE shared across wave
    members by design: every mutating method on them is plain synchronous code with no `await`
    in the middle of a read-modify-write, and the event loop only ever switches coroutines at an
    actual `await` point, so concurrent calls from different wave members can't tear a write.

    Returns (task_id, outcome, evidence_json) rather than the ORM AgentTask itself, which is
    unusable the moment this function's own session closes below - a "replan" outcome needs the
    failed task's evidence, so that one field is captured while the session is still open."""
    db = db_factory()
    try:
        run = OrchestrationRunRepository(db).get(run_id)
        project = db.query(Project).filter(Project.id == project_id).one_or_none()
        plan = OrchestrationPlanRepository(db).get(plan_id)
        task = AgentTaskRepository(db).get(task_id)
        if run is None or project is None or plan is None or task is None:
            # Can't happen in practice - all four were just read, moments ago, from the same DB
            # rows the caller's own session saw. Fail safe rather than crash the whole wave.
            return task_id, _TaskAttemptOutcome("failed"), None
        try:
            outcome = await _run_one_task(
                db,
                run=run,
                project=project,
                task=task,
                plan=plan,
                context_engine=ContextEngine(db),
                isolation=WorkspaceIsolationManager(db),
                provider_name=provider_name,
                model=model,
                api_key=api_key,
                run_images=run_images,
                cancellation=cancellation,
                loop_detector=loop_detector,
                budget=budget,
                replanning_enabled=replanning_enabled,
            )
            return task_id, outcome, task.evidence_json
        except Exception as exc:  # noqa: BLE001 - one task crash must not fail the whole run
            logger.exception(
                "run_orchestration: unhandled error in task %s of run %s", task_id, run_id
            )
            return (
                task_id,
                _fail_task_after_unhandled_error(
                    db_factory,
                    task_id=task_id,
                    run_id=run_id,
                    exc=exc,
                    replanning_enabled=replanning_enabled,
                ),
                None,
            )
    finally:
        db.close()


def extend_budget_after_topup(db: Session, run: OrchestrationRun) -> bool:
    """Grant a budget-exhausted run a fresh allowance so resuming it can actually make progress.

    Without this, resuming is a no-op loop: run_orchestration reseeds BudgetTracker from the
    persisted `credits_used` and compares it against the same `credit_budget` it already blew, so
    the very first budget check re-parks the run at waiting_for_user having done nothing. Extends
    the ceiling by one more default allowance ON TOP of what's already been spent, and clears the
    budget error so the run doesn't keep presenting as failed-on-budget.

    Returns True when it actually extended anything (i.e. this run was parked on budget).
    """
    if run.error_code != "budget_exceeded":
        return False
    allowance = settings.orchestration_default_credit_budget
    if allowance is None:
        # No configured per-run ceiling at all - the only limit was an explicit per-run
        # credit_budget, so lifting it entirely is what "continue after top-up" means here.
        run.credit_budget = None
    else:
        run.credit_budget = (run.credits_used or 0) + allowance
    run.error_code = None
    run.error_message = None
    db.add(run)
    return True


def prepare_run_for_resume(db: Session, run: OrchestrationRun) -> None:
    """Clear persisted blockers before relaunching a run parked at waiting_for_user."""
    if run.error_code == "budget_exceeded":
        extend_budget_after_topup(db, run)
        return
    if run.error_code in ("replan_limit_reached", "loop_detected"):
        run.error_code = None
        run.error_message = None
        db.add(run)


def resume_task_after_user_input(db: Session, task_id: uuid.UUID | str) -> AgentTask:
    """Called by whatever surface collects the missing secret/service/decision (chat.py wiring
    or the orchestration API router) once it has actually been supplied - moves the task back to
    `ready` so the next `run_orchestration()` call picks it up again. Does not itself verify the
    input was provided; that is the caller's responsibility."""
    task_repo = AgentTaskRepository(db)
    task = task_repo.get(task_id)
    if task is None:
        raise ValueError(f"AgentTask {task_id} not found")
    return task_repo.transition(task, "ready")


async def run_orchestration(
    run_id: uuid.UUID | str,
    *,
    db_factory: Callable[[], Session],
    provider_name: str,
    model: str,
    api_key: str,
    cancellation: CancellationToken | None = None,
) -> None:
    cancellation = cancellation or CancellationToken()

    try:
        await _run_orchestration_inner(
            run_id,
            db_factory=db_factory,
            provider_name=provider_name,
            model=model,
            api_key=api_key,
            cancellation=cancellation,
        )
    except Exception as exc:  # noqa: BLE001 - a run must never get stuck non-terminal because of an
        # unexpected bug here; best-effort mark it failed in a FRESH session (the one that raised
        # may be unusable) rather than leaving it silently stalled forever.
        logger.exception("run_orchestration: unhandled error for run %s", run_id)
        error_message = _format_engine_error(exc)
        try:
            db = db_factory()
            try:
                run = OrchestrationRunRepository(db).get(run_id)
                if run is not None and not is_run_terminal(run.status):
                    OrchestrationRunRepository(db).transition(
                        run,
                        "failed",
                        error_code="internal_error",
                        error_message=error_message,
                    )
                    events_bus.emit(
                        db,
                        run_id=run.id,
                        event_type="run_failed",
                        payload={"reason": "internal_error", "error": error_message},
                    )
                    db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - the failure-marking fallback itself must never raise
            logger.exception("run_orchestration: also failed to mark run %s failed", run_id)


async def _run_orchestration_inner(
    run_id: uuid.UUID | str,
    *,
    db_factory: Callable[[], Session],
    provider_name: str,
    model: str,
    api_key: str,
    cancellation: CancellationToken,
) -> None:
    router = CapabilityRouter(skill_registry=skill_registry)

    credit_budget: int | None = None
    credits_used_seed = 0
    run_images: list[ImageAttachment] = []

    db = db_factory()
    try:
        run = OrchestrationRunRepository(db).get(run_id)
        if run is None:
            logger.error("run_orchestration: OrchestrationRun %s not found", run_id)
            return
        run_images = extract_image_attachments(db, load_run_attachment_ids(run.metadata_json))
        project = db.query(Project).filter(Project.id == run.project_id).one_or_none()
        if project is None:
            OrchestrationRunRepository(db).transition(
                run, "failed", error_message="project not found"
            )
            db.commit()
            return

        if run.cancel_requested:
            cancellation.cancel("cancellation requested for this run")
        if cancellation.is_cancelled and not is_run_terminal(run.status):
            # Checked before planning even starts (not just in the main loop below) - a run
            # cancelled before its first task ever ran must not still pay for a full planning
            # call first.
            OrchestrationRunRepository(db).transition(run, "cancelled")
            events_bus.emit(
                db,
                run_id=run.id,
                event_type="run_cancelled",
                payload={"reason": cancellation.reason},
            )
            db.commit()
            return

        if run.status == "created":
            OrchestrationRunRepository(db).transition(run, "analyzing")
            events_bus.emit(
                db,
                run_id=run.id,
                event_type="run_created",
                payload={"original_request": (run.original_request or "")[:500]},
            )
            db.commit()
        if run.status == "waiting_for_user":
            OrchestrationRunRepository(db).transition(run, "running")
            db.commit()

        plan_repo = OrchestrationPlanRepository(db)
        if plan_repo.get_active(run.id) is None and run.status in ("analyzing", "planning"):
            if run.status == "analyzing":
                OrchestrationRunRepository(db).transition(run, "planning")
                events_bus.emit(db, run_id=run.id, event_type="planning_started", payload={})
                db.commit()
            context_engine = ContextEngine(db)
            await _ensure_planned(
                db,
                run=run,
                project=project,
                context_engine=context_engine,
                router=router,
                provider_name=provider_name,
                model=model,
                api_key=api_key,
            )
            db.commit()
        # Captured as plain values (not read off `run` below) - `run` is bound to this session,
        # which is about to close; SQLAlchemy's default expire-on-commit means any attribute not
        # already touched above would otherwise trigger a lazy-load against a closed Session.
        credit_budget = run.credit_budget
        credits_used_seed = run.credits_used or 0
    finally:
        db.close()

    loop_detector = LoopDetector()
    replan_gate = ReplanGate(max_replans=settings.orchestration_max_replans)
    budget = BudgetTracker(
        BudgetLimits(
            max_credits=credit_budget
            if credit_budget is not None
            else settings.orchestration_default_credit_budget,
            max_task_attempts=settings.orchestration_max_task_attempts,
        )
    )
    budget.usage.credits_used = credits_used_seed

    while True:
        db = db_factory()
        try:
            run = OrchestrationRunRepository(db).get(run_id)
            if run is None or is_run_terminal(run.status):
                return

            if run.cancel_requested and not cancellation.is_cancelled:
                cancellation.cancel("cancellation requested for this run")
            if cancellation.is_cancelled:
                OrchestrationRunRepository(db).transition(run, "cancelled")
                events_bus.emit(
                    db,
                    run_id=run.id,
                    event_type="run_cancelled",
                    payload={"reason": cancellation.reason},
                )
                db.commit()
                return

            plan = OrchestrationPlanRepository(db).get_active(run.id)
            if plan is None:
                project_row = db.query(Project).filter(Project.id == run.project_id).one_or_none()
                if project_row is None:
                    OrchestrationRunRepository(db).transition(
                        run, "failed", error_message="project not found"
                    )
                    events_bus.emit(
                        db, run_id=run.id, event_type="run_failed", payload={"reason": "no_project"}
                    )
                    db.commit()
                    return
                if run.original_request and not is_run_terminal(run.status):
                    if run.status != "planning":
                        OrchestrationRunRepository(db).transition(run, "planning")
                        events_bus.emit(
                            db, run_id=run.id, event_type="planning_started", payload={}
                        )
                        db.commit()
                    context_engine = ContextEngine(db)
                    router = CapabilityRouter(skill_registry=skill_registry)
                    await _ensure_planned(
                        db,
                        run=run,
                        project=project_row,
                        context_engine=context_engine,
                        router=router,
                        provider_name=provider_name,
                        model=model,
                        api_key=api_key,
                    )
                    db.commit()
                    continue
                OrchestrationRunRepository(db).transition(
                    run, "failed", error_message="no active plan"
                )
                events_bus.emit(
                    db, run_id=run.id, event_type="run_failed", payload={"reason": "no_active_plan"}
                )
                db.commit()
                return

            task_repo = AgentTaskRepository(db)
            task_repo.reset_stale_running(plan.id)
            before_ready = {t.id for t in task_repo.list_by_plan(plan.id) if t.status == "ready"}
            task_repo.refresh_readiness(plan.id)
            plan_tasks = task_repo.list_by_plan(plan.id)
            ready = [t for t in plan_tasks if t.status == "ready"]
            # Only tasks that BECAME ready this iteration - re-announcing an already-ready task
            # every loop would flood the durable event log.
            for task in ready:
                if task.id in before_ready:
                    continue
                events_bus.emit(
                    db,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="task_ready",
                    payload={"local_id": task.local_id, "title": task.title},
                )
            if len(ready) > len(before_ready):
                db.commit()

            if not ready:
                non_terminal = [t for t in plan_tasks if not is_task_terminal(t.status)]
                waiting = [t for t in non_terminal if t.status == "waiting_for_user"]
                if waiting:
                    if _try_recover_qa_deadlock(
                        db,
                        run=run,
                        plan_id=plan.id,
                        plan_tasks=plan_tasks,
                        non_terminal=non_terminal,
                        task_repo=task_repo,
                    ):
                        db.commit()
                        continue
                    loop_task = next((t for t in waiting if t.error_code == "loop_detected"), None)
                    if run.status != "waiting_for_user":
                        park_fields: dict[str, object] = {}
                        if loop_task is not None:
                            park_fields = {
                                "error_code": "loop_detected",
                                "error_message": (
                                    "Агент несколько раз повторял одну и ту же ошибку. "
                                    "Уточните запрос в чате и нажмите «Продолжить»."
                                ),
                            }
                        OrchestrationRunRepository(db).transition(
                            run, "waiting_for_user", **park_fields
                        )
                        events_bus.emit(
                            db,
                            run_id=run.id,
                            event_type="waiting_for_user",
                            payload={"tasks": [t.local_id for t in waiting]},
                        )
                        db.commit()
                    return
                running = [t for t in non_terminal if t.status == "running"]
                if running:
                    db.commit()
                    await asyncio.sleep(2)
                    continue
                if _try_recover_qa_deadlock(
                    db,
                    run=run,
                    plan_id=plan.id,
                    plan_tasks=plan_tasks,
                    non_terminal=non_terminal,
                    task_repo=task_repo,
                ):
                    db.commit()
                    continue
                if non_terminal:
                    OrchestrationRunRepository(db).transition(
                        run,
                        "failed",
                        error_message="plan deadlocked: blocked tasks with no path to ready",
                    )
                    events_bus.emit(
                        db, run_id=run.id, event_type="run_failed", payload={"reason": "deadlock"}
                    )
                    db.commit()
                    return

                failed = [t for t in plan_tasks if t.status == "failed"]
                if failed:
                    OrchestrationRunRepository(db).transition(
                        run, "failed", error_message=f"{len(failed)} task(s) failed"
                    )
                    events_bus.emit(
                        db,
                        run_id=run.id,
                        event_type="run_failed",
                        payload={"failed_local_ids": [t.local_id for t in failed]},
                    )
                else:
                    _walk_run_to_completed(db, run)
                    project_row = (
                        db.query(Project).filter(Project.id == run.project_id).one_or_none()
                    )
                    if project_row is not None:
                        finalize_project_after_completed_run(db, run=run, project=project_row)
                db.commit()
                return

            wave = _select_wave(ready)
            # Captured as plain values before commit/close below - expire_on_commit means every
            # attribute on `run`/`plan`/each task in `wave` would otherwise need a lazy-load
            # against a Session that's about to be closed (DetachedInstanceError).
            wave_task_ids = [t.id for t in wave]
            plan_id = plan.id
            plan_version = plan.version
            run.current_task_id = wave_task_ids[0]
            db.add(run)
            db.commit()
            project_id = run.project_id
            db.close()

            # Concurrency unit: each wave member opens its own Session (see
            # _run_task_wave_member) - none of them share `db` above, which stays closed for the
            # whole gather so this process doesn't hold an idle connection while the wave runs.
            results = await asyncio.gather(
                *(
                    _run_task_wave_member(
                        db_factory=db_factory,
                        run_id=run_id,
                        project_id=project_id,
                        plan_id=plan_id,
                        task_id=wave_task_id,
                        provider_name=provider_name,
                        model=model,
                        api_key=api_key,
                        run_images=run_images,
                        cancellation=cancellation,
                        loop_detector=loop_detector,
                        budget=budget,
                        replanning_enabled=True,
                    )
                    for wave_task_id in wave_task_ids
                )
            )

            db = db_factory()
            run = OrchestrationRunRepository(db).get(run_id)
            project = db.query(Project).filter(Project.id == project_id).one_or_none()
            plan = OrchestrationPlanRepository(db).get(plan_id)

            stopped = next((r for r in results if r[1].signal == "run_should_stop"), None)
            if stopped is not None:
                run_repo = OrchestrationRunRepository(db)
                if cancellation.is_cancelled:
                    run_repo.transition(run, "cancelled")
                    events_bus.emit(
                        db,
                        run_id=run.id,
                        event_type="run_cancelled",
                        payload={"reason": cancellation.reason},
                    )
                else:
                    # waiting_for_user, NOT failed: the run stopped because the user ran out of
                    # credits/budget, which they can fix by topping up - spec section 18's
                    # "позволяет продолжить после пополнения". `failed` is terminal with no
                    # outgoing transition, so parking here is what makes resume_run() able to
                    # pick the run back up (it re-readies the budget-skipped tasks).
                    run_repo.transition(
                        run,
                        "waiting_for_user",
                        error_code="budget_exceeded",
                        error_message=stopped[1].detail
                        or (
                            "Закончились кредиты — работа агента приостановлена. "
                            "Пополните баланс в профиле или подключите свой API-ключ."
                        ),
                    )
                    events_bus.emit(
                        db,
                        run_id=run.id,
                        event_type="waiting_for_user",
                        payload={
                            "reason": "budget_exceeded",
                            "detail": stopped[1].detail,
                            "credits_used": run.credits_used,
                        },
                    )
                db.commit()
                return

            if run is not None and plan is not None and project is not None:
                await _maybe_enqueue_judge_fix_from_completed_reviews(
                    db,
                    run=run,
                    plan=plan,
                    project=project,
                    completed_task_ids={
                        tid for tid, outcome, _ in results if outcome.signal == "completed"
                    },
                )

            # If several wave members request a replan at once, the first (wave order) is used -
            # replanning replaces the rest of the plan wholesale regardless of how many tasks
            # failed around the same time, so one is enough context for generate_replan.
            replan_hit = next((r for r in results if r[1].signal == "replan"), None)
            if replan_hit is not None:
                failed_task_id, outcome, evidence_json = replan_hit
                if not replan_gate.can_replan(current_plan_version=plan_version):
                    run_repo = OrchestrationRunRepository(db)
                    run_repo.transition(
                        run,
                        "waiting_for_user",
                        error_code="replan_limit_reached",
                        error_message=(
                            "Агент несколько раз перепланировал задачу, но результат не "
                            "прошёл проверку. Уточните запрос в чате или пополните баланс "
                            "и нажмите «Продолжить»."
                        ),
                    )
                    events_bus.emit(
                        db,
                        run_id=run.id,
                        event_type="waiting_for_user",
                        payload={"reason": "replan_limit_reached"},
                    )
                    db.commit()
                    return

                OrchestrationRunRepository(db).transition(run, "replanning")
                db.commit()

                if not _user_has_run_budget(db, user_id=run.user_id, provider_name=provider_name):
                    OrchestrationRunRepository(db).transition(
                        run,
                        "waiting_for_user",
                        error_code="budget_exceeded",
                        error_message=(
                            "Закончились кредиты — работа агента приостановлена. "
                            "Пополните баланс в профиле или подключите свой API-ключ."
                        ),
                    )
                    events_bus.emit(
                        db,
                        run_id=run.id,
                        event_type="waiting_for_user",
                        payload={"reason": "budget_exceeded", "during": "replan"},
                    )
                    db.commit()
                    return

                task_repo = AgentTaskRepository(db)
                completed_tasks = [
                    t for t in task_repo.list_by_plan(plan_id) if t.status == "completed"
                ]
                failed_task = task_repo.get(failed_task_id)
                evidence = (
                    TaskEvidence.model_validate_json(evidence_json) if evidence_json else None
                )
                assert outcome.evaluation is not None
                replanning_usage: list[dict] = []
                generation = await generate_replan(
                    original_request=run.original_request or "",
                    context_summary=run.context_summary or "",
                    completed_tasks=completed_tasks,
                    failed_task=failed_task,
                    evaluation=outcome.evaluation,
                    evidence=evidence,
                    provider_name=provider_name,
                    model=model,
                    api_key=api_key,
                    max_tasks=settings.orchestration_max_plan_tasks,
                    usage_sink=replanning_usage.append,
                    db=db,
                    run_id=run.id,
                )
                user = db.query(User).filter(User.id == run.user_id).one_or_none()
                for usage in replanning_usage:
                    replan_cost = _record_run_usage_charge(
                        db,
                        run=run,
                        project=project,
                        user=user,
                        provider_name=provider_name,
                        model=model,
                        usage=usage,
                        summary_text="",
                        category="replanning",
                    )
                    budget.record_credits(replan_cost)
                loop_detector.record_plan(
                    json.dumps(sorted(t.local_id for t in generation.plan.tasks))
                )
                reason = summarize_replan_reason(outcome.evaluation, failed_task)
                await _persist_new_plan(
                    db,
                    run=run,
                    project=project,
                    execution_plan=generation.plan,
                    context_summary=run.context_summary or "",
                    replan_reason=reason,
                    router=router,
                )
                db.commit()
                continue

            # Every wave member landed on "completed" / "waiting_for_user" / "failed" (no stop or
            # replan signal) - loop again; refresh_readiness reflects all of the wave's
            # consequences at once.
            db.commit()
        finally:
            db.close()


# --------------------------------------------------------------------------------------------
# Background-launch helper (shared by api/routers/orchestration.py and chat.py's own wiring) -
# both need "start a run, keep its asyncio.Task and CancellationToken alive independent of
# whichever HTTP request triggered it, so the run survives that request/SSE connection ending".
# --------------------------------------------------------------------------------------------

_background_tasks: dict[str, asyncio.Task] = {}
_active_tokens: dict[str, CancellationToken] = {}


def get_cancellation_token(run_id: uuid.UUID | str) -> CancellationToken | None:
    """The live token for a run this process is currently driving, if any - used to deliver an
    immediate in-process cancel signal (see api/routers/orchestration.py's cancel_run) on top of
    the persisted cancel_requested flag every run_orchestration() loop iteration polls regardless."""
    return _active_tokens.get(str(run_id))


def launch_run_in_background(
    run_id: uuid.UUID | str, *, provider_name: str, model: str, api_key: str
) -> None:
    """Fire-and-forget: schedules run_orchestration() on this process's event loop and returns
    immediately. The task (and its CancellationToken) are kept alive via the module-level dicts
    above for this process's lifetime - deliberately NOT tied to the lifetime of whichever
    request/generator called this, since a run must keep going after its triggering HTTP
    request/SSE connection ends (page reload, chat.py's own connection dropping, ...); that is
    the entire point of the durable, reconnectable design (events_bus.stream_events's replay).
    Must be called from a coroutine already running on the event loop - asyncio.create_task()
    requires one in the calling thread, so a plain sync/threadpooled caller must not call this
    directly (see orchestration.py's create_run being `async def` for exactly this reason)."""
    token = CancellationToken()
    key = str(run_id)
    _active_tokens[key] = token

    try:
        db = SessionLocal()
        try:
            run = OrchestrationRunRepository(db).get(run_id)
            if run is None:
                return
            if not _user_has_run_budget(db, user_id=run.user_id, provider_name=provider_name):
                run_repo = OrchestrationRunRepository(db)
                run_repo.transition(
                    run,
                    "waiting_for_user",
                    error_code="budget_exceeded",
                    error_message=(
                        "Закончились кредиты — работа агента приостановлена. "
                        "Пополните баланс в профиле или подключите свой API-ключ."
                    ),
                )
                db.commit()
                _active_tokens.pop(key, None)
                return
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - must not block launch on a preflight failure
        logger.exception("launch_run_in_background: preflight failed for run %s", run_id)

    async def _drive() -> None:
        try:
            await run_orchestration(
                run_id,
                db_factory=SessionLocal,
                provider_name=provider_name,
                model=model,
                api_key=api_key,
                cancellation=token,
            )
        finally:
            _active_tokens.pop(key, None)

    task = asyncio.create_task(_drive())
    _background_tasks[key] = task

    def _on_done(finished: asyncio.Task) -> None:
        _background_tasks.pop(key, None)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error("orchestration run %s background task raised", key, exc_info=exc)

    task.add_done_callback(_on_done)


def recover_stranded_runs(db_factory: Callable[[], Session] | None = None) -> int:
    """Restart recovery: relaunch runs that were mid-flight when this process last died.

    An OrchestrationRun is a DB row, not in-memory state, and run_orchestration() is written to be
    re-entrant on an in-progress run (it re-reads the persisted plan/task statuses rather than
    replanning - see TestRestartSafety), so recovery is just "find the stranded rows and drive
    them again". Without this the rows are durable but nothing ever picks them back up, and a
    backend crash strands those runs forever.

    Called from main.py's lifespan on startup. Returns how many runs were relaunched. Never
    raises: a failure to recover must not stop the API from booting.
    """
    factory = db_factory or SessionLocal
    relaunched = 0
    try:
        db = factory()
        try:
            stranded = OrchestrationRunRepository(db).list_resumable()
            # Capture as plain values - the session closes before the runs are driven.
            pending = [
                (r.id, r.provider or settings.provider_name, r.model or "", r.user_id)
                for r in stranded
            ]
        finally:
            db.close()

        for run_id, provider_name, model, user_id in pending:
            if str(run_id) in _background_tasks:
                continue  # already being driven by this process
            api_key = resolve_platform_api_key(provider_name) or ""
            if not api_key:
                logger.warning(
                    "orchestration restart recovery: skipping run %s - no API key for provider %s",
                    run_id,
                    provider_name,
                )
                continue
            try:
                skip_launch = False
                db = factory()
                try:
                    run = OrchestrationRunRepository(db).get(run_id)
                    if run is not None and not _user_has_run_budget(
                        db, user_id=user_id, provider_name=provider_name
                    ):
                        logger.info(
                            "orchestration restart recovery: skipping run %s - user has no credits",
                            run_id,
                        )
                        skip_launch = True
                    elif run is not None:
                        plan = OrchestrationPlanRepository(db).get_active(run.id)
                        if plan is not None:
                            AgentTaskRepository(db).reset_stale_running(plan.id, stale_seconds=60)
                            AgentTaskRepository(db).refresh_readiness(plan.id)
                            db.commit()
                finally:
                    db.close()
                if skip_launch:
                    continue
            except Exception:  # noqa: BLE001
                logger.exception(
                    "orchestration restart recovery: stale-task reset failed for run %s", run_id
                )
            launch_run_in_background(
                run_id, provider_name=provider_name, model=model, api_key=api_key
            )
            relaunched += 1
        if relaunched:
            logger.info("orchestration restart recovery: relaunched %d stranded run(s)", relaunched)
    except Exception:  # noqa: BLE001 - startup must never be blocked by recovery failing
        logger.exception("orchestration restart recovery failed")
    return relaunched
