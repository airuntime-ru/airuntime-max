"""Replanner (spec section 13): produces a new plan version for the unfinished remainder of a
run after a failure escalates to FailureDecision.REPLAN.

Design note on "preserving completed tasks": this module does NOT merge old and new task
lists. A replan asks planner.generate_plan() for a plan covering only the *remaining* work,
informed by a summary of what's already done (context, not graph nodes) and factual evidence
of why the previous attempt failed. Completed AgentTask rows from earlier plan versions are
never touched, deleted, or recreated - they stay in the database against `run_id` permanently
(engine.py queries across all plan versions via AgentTaskRepository.list_by_run when it needs
dependency results from earlier-completed work), which is what actually satisfies "сохраняет
завершённые принятые задачи". Re-deriving a merged graph object here would just be a second,
divergent copy of information the database already holds authoritatively.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.services.orchestration.failure_policy import FailureEvaluation
from src.services.orchestration.planner import PlanGenerationResult, generate_plan
from src.services.orchestration.schemas import TaskEvidence


@dataclass
class ReplanGate:
    """`plan_version` starts at 1 for the initial plan; each replan increments it, so version N
    means N-1 replans have already happened this run - caps runaway replan/fail cycles
    independent of (and in addition to) failure_policy.LoopDetector's fingerprint-based check,
    which only fires on *identical* repeated failures. This gate fires even when each replan
    fails for a *different* reason, which the fingerprint check alone would miss."""

    max_replans: int

    def can_replan(self, *, current_plan_version: int) -> bool:
        return (current_plan_version - 1) < self.max_replans


def summarize_replan_reason(evaluation: FailureEvaluation, failed_task: AgentTask) -> str:
    return (
        f"Задача «{failed_task.title}» не прошла проверку "
        f"({evaluation.failure_class.value}): {evaluation.reason}. План скорректирован."
    )


def _format_completed_summary(completed_tasks: list[AgentTask]) -> str:
    if not completed_tasks:
        return "(пока ничего не завершено)"
    return "\n".join(
        f"- [{t.local_id}] {t.title} - принято, не повторяй эту работу" for t in completed_tasks
    )


def _format_evidence_summary(evidence: TaskEvidence | None) -> str:
    if evidence is None:
        return "(evidence недоступна)"
    parts = [f"Изменённые файлы: {', '.join(evidence.changed_files) or '(нет)'}"]
    if evidence.build_result is not None:
        parts.append(f"Build result: {evidence.build_result}")
    if evidence.git_diff_stat:
        parts.append(f"Diff:\n{evidence.git_diff_stat[:2000]}")
    return "\n".join(parts)


def build_replan_user_message(
    *,
    original_request: str,
    completed_tasks: list[AgentTask],
    failed_task: AgentTask,
    evaluation: FailureEvaluation,
    evidence: TaskEvidence | None,
) -> str:
    return (
        f"Исходный запрос пользователя:\n{original_request}\n\n"
        f"Уже выполнено и принято (НЕ включай эти задачи в новый план):\n"
        f"{_format_completed_summary(completed_tasks)}\n\n"
        f"Задача, которая не удалась: [{failed_task.local_id}] {failed_task.title}\n"
        f"Классификация сбоя: {evaluation.failure_class.value}\n"
        f"Причина: {evaluation.reason}\n\n"
        f"Фактические данные о неудачной попытке:\n{_format_evidence_summary(evidence)}\n\n"
        "Построй план ТОЛЬКО для оставшейся работы с учётом этой информации - не переделывай "
        "то, что уже принято. Если сбой связан с preview/вёрсткой/контентом — следующая задача "
        "должна быть у Implementer или UI/UX Specialist, который ПРАВИТ файлы. Не ставь "
        "ещё один QA/visual_preview_review первым узлом: ревью без правки ничего не изменит."
    )


async def generate_replan(
    *,
    original_request: str,
    context_summary: str,
    completed_tasks: list[AgentTask],
    failed_task: AgentTask,
    evaluation: FailureEvaluation,
    evidence: TaskEvidence | None,
    provider_name: str,
    model: str,
    api_key: str,
    max_tasks: int,
    usage_sink: Callable[[dict[str, Any]], None] | None = None,
    db: Session | None = None,
    run_id: object | None = None,
) -> PlanGenerationResult:
    user_message = build_replan_user_message(
        original_request=original_request,
        completed_tasks=completed_tasks,
        failed_task=failed_task,
        evaluation=evaluation,
        evidence=evidence,
    )
    return await generate_plan(
        user_message=user_message,
        project_context_summary=context_summary,
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        max_tasks=max_tasks,
        usage_sink=usage_sink,
        db=db,
        run_id=run_id,
    )
