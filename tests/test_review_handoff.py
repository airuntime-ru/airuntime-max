"""Unit tests for QA verdict → implementer TODO handoff. No DB, no LLM."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.orchestration.review_handoff import (
    build_judge_fix_planned_tasks,
    can_enqueue_judge_fix,
    collect_review_todos,
    collect_todos_from_task_result,
    format_judge_todos_for_implementer,
    judge_fix_round_count,
    verdict_from_task_result,
)
from src.services.orchestration.schemas import SpecialistRole


class TestCollectReviewTodos:
    def test_prefers_explicit_todos_over_major_issues(self) -> None:
        todos = collect_review_todos(
            {
                "verdict": "revise",
                "todos": ["public/index.html: подними CTA"],
                "major_issues": ["generic hero"],
                "recommended_fixes": ["rework composition"],
            }
        )
        assert todos == ["public/index.html: подними CTA"]

    def test_falls_back_to_recommended_fixes(self) -> None:
        todos = collect_review_todos(
            {
                "verdict": "revise",
                "todos": [],
                "recommended_fixes": ["Убери горизонтальный overflow"],
                "major_issues": ["overflow"],
            }
        )
        assert todos == ["Убери горизонтальный overflow"]

    def test_revise_without_items_gets_a_fallback_todo(self) -> None:
        todos = collect_review_todos({"verdict": "revise"})
        assert len(todos) == 1
        assert "не переписывая" in todos[0]

    def test_pass_without_items_is_empty(self) -> None:
        assert collect_review_todos({"verdict": "pass"}) == []


class TestTaskResultTodos:
    def test_reads_unresolved_and_verdict_from_summary(self) -> None:
        claimed = {
            "status": "partial",
            "summary": "skill visual_preview_review: failed; review=revise; overflow",
            "unresolved": ["Убери горизонтальный скролл в hero на 390px"],
        }
        assert verdict_from_task_result(claimed) == "revise"
        assert collect_todos_from_task_result(claimed) == [
            "Убери горизонтальный скролл в hero на 390px"
        ]

    def test_pass_summary_without_unresolved(self) -> None:
        claimed = {
            "status": "completed",
            "summary": "skill visual_preview_review: completed; review=pass",
        }
        assert verdict_from_task_result(claimed) == "pass"
        assert collect_todos_from_task_result(claimed) == []


class TestJudgeFixRounds:
    def test_caps_enqueue_after_one_fix_round(self) -> None:
        tasks = [
            SimpleNamespace(local_id="main"),
            SimpleNamespace(local_id="qa"),
            SimpleNamespace(local_id="judge_fix_1"),
        ]
        assert judge_fix_round_count(tasks) == 1
        assert can_enqueue_judge_fix(tasks, max_rounds=1) is False
        assert can_enqueue_judge_fix(tasks, max_rounds=2) is True

    def test_disabled_when_max_rounds_is_zero(self) -> None:
        assert can_enqueue_judge_fix([], max_rounds=0) is False


class TestBuildJudgeFixTasks:
    def test_implementer_goal_contains_numbered_todos(self) -> None:
        planned = build_judge_fix_planned_tasks(
            todos=["Почини overflow", "Подними CTA"],
            qa_local_ids=["qa"],
            relevant_paths=["public/index.html"],
            suggested_skills=["visual_preview_review"],
            round_number=1,
            include_followup_qa=True,
        )
        assert [task.local_id for task in planned] == ["judge_fix_1", "judge_qa_1"]
        assert planned[0].role == SpecialistRole.IMPLEMENTER
        assert planned[1].role == SpecialistRole.QA_REVIEWER
        assert planned[1].dependencies == ["judge_fix_1"]
        assert "1. Почини overflow" in planned[0].goal
        assert "2. Подними CTA" in planned[0].goal
        assert format_judge_todos_for_implementer(["a"]) == "TODO судьи:\n1. a"

    def test_can_skip_followup_qa_when_plan_is_full(self) -> None:
        planned = build_judge_fix_planned_tasks(
            todos=["Почини overflow"],
            qa_local_ids=["qa"],
            relevant_paths=["public/index.html"],
            suggested_skills=["visual_preview_review"],
            round_number=1,
            include_followup_qa=False,
        )
        assert [task.local_id for task in planned] == ["judge_fix_1"]
