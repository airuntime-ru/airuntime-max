"""Tests for services/orchestration/planner.py. No live LLM: `complete_structured` is
monkeypatched where planner.py imported it, matching this repo's existing test convention
(see test_product_pipeline.py's `_make_complete_structured`)."""

from __future__ import annotations

import pytest

from src.services.orchestration import planner
from src.services.orchestration.schemas import (
    AcceptanceCriterion,
    ExecutionPlan,
    PlannedTask,
    SpecialistRole,
)


def _task(
    local_id: str, deps: list[str] | None = None, role: SpecialistRole = SpecialistRole.IMPLEMENTER
) -> PlannedTask:
    return PlannedTask(
        local_id=local_id,
        title=f"Task {local_id}",
        role=role,
        goal=f"do {local_id}",
        reason="planned",
        dependencies=deps or [],
        acceptance_criteria=[
            AcceptanceCriterion(
                id=f"{local_id}-ac1", description="works", verification_method="build"
            )
        ],
    )


class TestFindDependencyCycle:
    def test_no_cycle_in_dag(self) -> None:
        tasks = [_task("a"), _task("b", ["a"]), _task("c", ["a", "b"])]
        assert planner.find_dependency_cycle(tasks) is None

    def test_direct_cycle(self) -> None:
        tasks = [_task("a", ["b"]), _task("b", ["a"])]
        cycle = planner.find_dependency_cycle(tasks)
        assert cycle is not None
        assert set(cycle) == {"a", "b"}

    def test_self_dependency_rejected_at_model_level(self) -> None:
        with pytest.raises(ValueError, match="cannot depend on itself"):
            _task("a", ["a"])

    def test_indirect_cycle(self) -> None:
        tasks = [_task("a", ["c"]), _task("b", ["a"]), _task("c", ["b"])]
        cycle = planner.find_dependency_cycle(tasks)
        assert cycle is not None
        assert {"a", "b", "c"} <= set(cycle)

    def test_dangling_dependency_is_not_a_cycle(self) -> None:
        tasks = [_task("a", ["ghost"])]
        assert planner.find_dependency_cycle(tasks) is None


class TestValidatePlanStructure:
    def _plan(self, tasks: list[PlannedTask], complexity: str = "compound") -> ExecutionPlan:
        return ExecutionPlan(goal="g", complexity=complexity, tasks=tasks)

    def test_valid_plan_has_no_errors(self) -> None:
        plan = self._plan([_task("a"), _task("b", ["a"])])
        assert planner.validate_plan_structure(plan, max_tasks=10) == []

    def test_too_many_tasks(self) -> None:
        plan = self._plan([_task(str(i)) for i in range(5)])
        errors = planner.validate_plan_structure(plan, max_tasks=3)
        assert any("exceeding the limit" in e for e in errors)

    def test_dangling_dependency_reported(self) -> None:
        plan = self._plan([_task("a", ["missing"])])
        errors = planner.validate_plan_structure(plan, max_tasks=10)
        assert any("unknown local_id" in e for e in errors)

    def test_cycle_reported(self) -> None:
        plan = self._plan([_task("a", ["b"]), _task("b", ["a"])])
        errors = planner.validate_plan_structure(plan, max_tasks=10)
        assert any("cycle" in e for e in errors)

    def test_simple_complexity_with_multiple_tasks_rejected(self) -> None:
        plan = self._plan([_task("a"), _task("b")], complexity="simple")
        errors = planner.validate_plan_structure(plan, max_tasks=10)
        assert any("complexity=simple" in e for e in errors)


class TestBuildSingleTaskPlan:
    def test_produces_one_implementer_task(self) -> None:
        plan = planner.build_single_task_plan("add a contact form", reason="test")
        assert plan.complexity == "simple"
        assert len(plan.tasks) == 1
        assert plan.tasks[0].role == SpecialistRole.IMPLEMENTER
        assert planner.validate_plan_structure(plan, max_tasks=10) == []

    def test_website_quality_fallback_adds_preview_and_independent_qa(self) -> None:
        plan = planner.build_single_task_plan(
            "Сделай лендинг кофейни", reason="test", website_quality=True
        )

        assert plan.complexity == "compound"
        assert [task.role for task in plan.tasks] == [
            SpecialistRole.IMPLEMENTER,
            SpecialistRole.QA_REVIEWER,
        ]
        assert any(
            criterion.verification_method == "preview"
            for criterion in plan.tasks[0].acceptance_criteria
        )
        assert plan.tasks[1].dependencies == ["main"]
        assert plan.tasks[1].suggested_skills == ["visual_preview_review"]

    def test_bot_fallback_adds_independent_product_qa(self) -> None:
        plan = planner.build_single_task_plan(
            "Добавь команду /help",
            reason="test",
            project_type="telegram_bot",
        )

        assert plan.complexity == "compound"
        assert [task.role for task in plan.tasks] == [
            SpecialistRole.IMPLEMENTER,
            SpecialistRole.QA_REVIEWER,
        ]
        assert plan.tasks[1].dependencies == ["main"]
        assert plan.tasks[1].suggested_skills == ["product_quality_review"]


class TestPlannerOutputNormalization:
    def test_missing_acceptance_id_gets_stable_generated_id(self) -> None:
        raw = {
            "description": "The landing page builds",
            "verification_method": "build",
        }
        first = AcceptanceCriterion.model_validate(raw)
        second = AcceptanceCriterion.model_validate(raw)

        assert first.id.startswith("criterion_")
        assert first.id == second.id

    @pytest.mark.parametrize("alias", ["parallel", "sequential"])
    def test_scheduling_alias_is_normalized_to_either(self, alias: str) -> None:
        task = PlannedTask.model_validate(
            {
                "local_id": "landing",
                "title": "Landing",
                "role": "implementer",
                "goal": "Build it",
                "reason": "Requested",
                "execution_preference": alias,
                "acceptance_criteria": [
                    {
                        "description": "Build succeeds",
                        "verification_method": "build",
                    }
                ],
            }
        )

        assert task.execution_preference.value == "either"
        assert task.acceptance_criteria[0].id

    def test_missing_reason_and_role_aliases_are_coerced(self) -> None:
        task = PlannedTask.model_validate(
            {
                "local_id": "photos",
                "role": "Developer",
                "goal": "Добавить фото машин",
                "acceptance_criteria": [
                    {"description": "Фото на странице", "verification_method": "manual"}
                ],
            }
        )
        assert task.role == SpecialistRole.IMPLEMENTER
        assert task.title == "Добавить фото машин"
        assert task.reason == "Добавить фото машин"

    def test_plan_complexity_aliases(self) -> None:
        plan = ExecutionPlan.model_validate(
            {
                "complexity": "small",
                "tasks": [
                    {
                        "local_id": "a",
                        "role": "implementer",
                        "goal": "Do it",
                        "acceptance_criteria": [],
                    }
                ],
            }
        )
        assert plan.complexity == "simple"
        assert plan.goal == "Do it"

    def test_string_risks_and_criteria_are_coerced(self) -> None:
        plan = ExecutionPlan.model_validate(
            {
                "goal": "Лендинг",
                "complexity": "simple",
                "tasks": [
                    {
                        "local_id": "landing",
                        "role": "implementer",
                        "goal": "Сделать лендинг",
                        "acceptance_criteria": ["Hero и CTA видны в preview"],
                    }
                ],
                "final_acceptance_criteria": ["Сайт открывается без ошибок"],
                "risks": [
                    "Качество и доступность в preview.",
                    {
                        "description": "Срыв сроков",
                        "severity": "высокий",
                        "mitigation": "урезать scope",
                    },
                ],
            }
        )
        assert [risk.description for risk in plan.risks] == [
            "Качество и доступность в preview.",
            "Срыв сроков",
        ]
        assert plan.risks[0].severity.value == "medium"
        assert plan.risks[1].severity.value == "high"
        assert plan.tasks[0].acceptance_criteria[0].description == "Hero и CTA видны в preview"
        assert plan.final_acceptance_criteria[0].description == "Сайт открывается без ошибок"


@pytest.mark.asyncio
class TestGeneratePlan:
    async def test_short_message_skips_llm_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(**kwargs):  # noqa: ANN003
            raise AssertionError("complete_structured must not be called for trivial requests")

        monkeypatch.setattr(planner, "complete_structured", _boom)
        result = await planner.generate_plan(
            user_message="fix typo",
            project_context_summary="",
            provider_name="openai",
            model="m",
            api_key="k",
        )
        assert result.source == "heuristic_simple"
        assert len(result.plan.tasks) == 1

    async def test_short_website_request_keeps_quality_planning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good_plan = ExecutionPlan(
            goal="coffee landing",
            complexity="simple",
            tasks=[_task("landing")],
        )
        calls = []

        async def _fake_complete(**kwargs):  # noqa: ANN003
            calls.append(kwargs)
            return good_plan

        monkeypatch.setattr(planner, "complete_structured", _fake_complete)
        result = await planner.generate_plan(
            user_message="Сделай лендинг кофейни",
            project_context_summary="Project: Coffee (website)",
            provider_name="openai",
            model="gpt-5.6-sol",
            api_key="k",
        )

        assert len(calls) == 1
        assert result.source == "llm"
        assert len(result.plan.tasks) == 2
        assert result.plan.tasks[-1].role == SpecialistRole.QA_REVIEWER
        assert "visual_preview_review" in result.plan.tasks[-1].suggested_skills

    async def test_short_bot_followup_keeps_quality_planning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        async def _fake_complete(**kwargs):  # noqa: ANN003
            calls.append(kwargs)
            return ExecutionPlan(
                goal="help command",
                complexity="simple",
                tasks=[_task("help_command")],
            )

        monkeypatch.setattr(planner, "complete_structured", _fake_complete)
        result = await planner.generate_plan(
            user_message="Добавь команду /help",
            project_context_summary="Project: Support (telegram_bot)",
            provider_name="openai",
            model="gpt-5.6-sol",
            api_key="k",
        )

        assert len(calls) == 1
        assert result.source == "llm"
        assert result.plan.tasks[-1].suggested_skills == ["product_quality_review"]
        assert result.plan.tasks[-1].dependencies == ["help_command"]

    async def test_mixed_product_gets_visual_and_functional_review(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_complete(**kwargs):  # noqa: ANN003
            return ExecutionPlan(
                goal="site and bot",
                complexity="simple",
                tasks=[_task("implementation")],
            )

        monkeypatch.setattr(planner, "complete_structured", _fake_complete)
        result = await planner.generate_plan(
            user_message="Сделай сайт и Telegram-бот для записи",
            project_context_summary="Project: Booking (mixed)",
            provider_name="openai",
            model="gpt-5.6-sol",
            api_key="k",
        )

        qa_skills = [
            task.suggested_skills
            for task in result.plan.tasks
            if task.role == SpecialistRole.QA_REVIEWER
        ]
        assert ["visual_preview_review"] in qa_skills
        assert ["product_quality_review"] in qa_skills

    async def test_valid_llm_plan_is_used_as_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        good_plan = ExecutionPlan(
            goal="build a shop",
            complexity="compound",
            tasks=[_task("backend"), _task("frontend", ["backend"])],
        )

        async def _fake_complete(**kwargs):  # noqa: ANN003
            assert kwargs["response_model"] is ExecutionPlan
            return good_plan

        monkeypatch.setattr(planner, "complete_structured", _fake_complete)
        result = await planner.generate_plan(
            user_message="x" * 200,
            project_context_summary="ctx",
            provider_name="openai",
            model="m",
            api_key="k",
        )
        assert result.source == "llm"
        assert result.plan is good_plan
        assert result.errors == []

    async def test_llm_failure_falls_back_to_single_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_none(**kwargs):  # noqa: ANN003
            return None

        monkeypatch.setattr(planner, "complete_structured", _fake_none)
        result = await planner.generate_plan(
            user_message="y" * 200,
            project_context_summary="",
            provider_name="openai",
            model="m",
            api_key="k",
        )
        assert result.source == "fallback_llm_failed"
        assert len(result.plan.tasks) == 1

    async def test_invalid_plan_gets_one_repair_attempt_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cyclic_plan = ExecutionPlan(
            goal="g", complexity="compound", tasks=[_task("a", ["b"]), _task("b", ["a"])]
        )
        fixed_plan = ExecutionPlan(
            goal="g", complexity="compound", tasks=[_task("a"), _task("b", ["a"])]
        )
        calls = []

        async def _fake_sequence(**kwargs):  # noqa: ANN003
            calls.append(kwargs["user_text"])
            return cyclic_plan if len(calls) == 1 else fixed_plan

        monkeypatch.setattr(planner, "complete_structured", _fake_sequence)
        result = await planner.generate_plan(
            user_message="z" * 200,
            project_context_summary="",
            provider_name="openai",
            model="m",
            api_key="k",
        )
        assert len(calls) == 2
        assert "не прошёл структурную проверку" in calls[1]
        assert result.source == "llm_repaired"
        assert result.plan is fixed_plan

    async def test_invalid_plan_after_repair_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cyclic_plan = ExecutionPlan(
            goal="g", complexity="compound", tasks=[_task("a", ["b"]), _task("b", ["a"])]
        )

        async def _always_cyclic(**kwargs):  # noqa: ANN003
            return cyclic_plan

        monkeypatch.setattr(planner, "complete_structured", _always_cyclic)
        result = await planner.generate_plan(
            user_message="w" * 200,
            project_context_summary="",
            provider_name="openai",
            model="m",
            api_key="k",
        )
        assert result.source == "fallback_invalid"
        assert len(result.plan.tasks) == 1
        assert any("cycle" in e for e in result.errors)


class TestDeriveComplexity:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [(0, "simple"), (1, "simple"), (2, "compound"), (4, "compound"), (5, "large")],
    )
    def test_thresholds(self, count: int, expected: str) -> None:
        assert planner.derive_complexity_from_task_count(count) == expected
