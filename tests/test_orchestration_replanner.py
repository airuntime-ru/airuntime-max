"""Tests for services/orchestration/replanner.py. No live LLM: generate_plan is monkeypatched
where replanner.py imported it, same convention as test_orchestration_planner.py."""

from __future__ import annotations

import pytest

from src.db.models.agent_task import AgentTask
from src.services.orchestration import replanner
from src.services.orchestration.failure_policy import FailureEvaluation
from src.services.orchestration.schemas import (
    ExecutionPlan,
    FailureClass,
    FailureDecision,
    PlannedTask,
    SpecialistRole,
    TaskEvidence,
)


def _task(local_id: str, title: str) -> AgentTask:
    return AgentTask(
        local_id=local_id,
        title=title,
        role="implementer",
        execution_kind="codex_task",
        status="completed",
    )


class TestReplanGate:
    def test_allows_replan_within_budget(self) -> None:
        gate = replanner.ReplanGate(max_replans=3)
        assert gate.can_replan(current_plan_version=1) is True
        assert gate.can_replan(current_plan_version=3) is True

    def test_blocks_replan_once_budget_exhausted(self) -> None:
        gate = replanner.ReplanGate(max_replans=3)
        assert gate.can_replan(current_plan_version=4) is False


class TestBuildReplanUserMessage:
    def test_includes_completed_and_failure_context(self) -> None:
        completed = [_task("a", "Backend API")]
        failed = _task("b", "Frontend")
        evaluation = FailureEvaluation(
            failure_class=FailureClass.BUILD_ERROR,
            decision=FailureDecision.REPLAN,
            loop_detected=False,
            reason="build_error on attempt 3/3",
        )
        evidence = TaskEvidence(
            changed_files=["frontend/app.py"], build_result={"ok": False, "log": "SyntaxError"}
        )

        message = replanner.build_replan_user_message(
            original_request="build me a shop",
            completed_tasks=completed,
            failed_task=failed,
            evaluation=evaluation,
            evidence=evidence,
        )
        assert "Backend API" in message
        assert "Frontend" in message
        assert "build_error" in message
        assert "frontend/app.py" in message
        assert "Implementer или UI/UX Specialist" in message

    def test_handles_no_completed_tasks_and_missing_evidence(self) -> None:
        failed = _task("a", "Only task")
        evaluation = FailureEvaluation(
            failure_class=FailureClass.AGENT_ERROR,
            decision=FailureDecision.REPLAN,
            loop_detected=False,
            reason="x",
        )
        message = replanner.build_replan_user_message(
            original_request="req",
            completed_tasks=[],
            failed_task=failed,
            evaluation=evaluation,
            evidence=None,
        )
        assert "пока ничего не завершено" in message


class TestSummarizeReplanReason:
    def test_produces_russian_user_facing_summary(self) -> None:
        failed = _task("a", "Оплата")
        evaluation = FailureEvaluation(
            failure_class=FailureClass.VALIDATION_FAILED,
            decision=FailureDecision.REPLAN,
            loop_detected=True,
            reason="repeated failure",
        )
        summary = replanner.summarize_replan_reason(evaluation, failed)
        assert "Оплата" in summary
        assert "validation_failed" in summary


@pytest.mark.asyncio
class TestGenerateReplan:
    async def test_calls_planner_with_composed_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = {}

        async def _fake_generate_plan(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return replanner.PlanGenerationResult(
                plan=ExecutionPlan(
                    goal="remaining work",
                    complexity="simple",
                    tasks=[
                        PlannedTask(
                            local_id="c",
                            title="Fix it",
                            role=SpecialistRole.BUILD_FIXER,
                            goal="g",
                            reason="r",
                        )
                    ],
                ),
                source="llm",
            )

        monkeypatch.setattr(replanner, "generate_plan", _fake_generate_plan)

        evaluation = FailureEvaluation(
            failure_class=FailureClass.BUILD_ERROR,
            decision=FailureDecision.REPLAN,
            loop_detected=False,
            reason="build failed",
        )
        result = await replanner.generate_replan(
            original_request="build a shop",
            context_summary="ctx",
            completed_tasks=[_task("a", "Backend")],
            failed_task=_task("b", "Frontend"),
            evaluation=evaluation,
            evidence=TaskEvidence(),
            provider_name="openai",
            model="m",
            api_key="k",
            max_tasks=10,
        )

        assert result.plan.tasks[0].local_id == "c"
        assert "Backend" in captured["user_message"]
        assert captured["project_context_summary"] == "ctx"
