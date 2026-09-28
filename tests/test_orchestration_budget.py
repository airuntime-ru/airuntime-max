"""Tests for services/orchestration/budget.py - pure in-memory tracker logic, plus
charge_credits_for_run against the real test DB (it's a one-line pass-through to billing.py,
worth confirming it actually moves the needle on a real User row)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from src.db.models.user import User
from src.services.orchestration import budget


class TestEstimateTaskCost:
    def test_uses_reported_total_tokens_when_present(self) -> None:
        # Well above the 100-credit floor so this exercises the real formula, not the clamp.
        cost = budget.estimate_task_cost(usage={"total_tokens": 50_000}, summary_text="short")
        assert cost == 500  # 50000 * 10 / 1000

    def test_sums_prompt_and_completion_tokens_when_no_total(self) -> None:
        cost = budget.estimate_task_cost(
            usage={"prompt_tokens": 30_000, "completion_tokens": 20_000}, summary_text="short"
        )
        assert cost == 500

    def test_never_charges_below_the_floor(self) -> None:
        cost = budget.estimate_task_cost(usage={"total_tokens": 1}, summary_text="")
        assert cost == 100

    def test_falls_back_to_char_count_when_no_usage_reported(self) -> None:
        text = "x" * 250
        assert budget.estimate_task_cost(usage=None, summary_text=text) == 250

    def test_empty_usage_dict_falls_back_too(self) -> None:
        assert budget.estimate_task_cost(usage={}, summary_text="x" * 150) == 150

    def test_uses_selected_model_price_when_detailed_usage_is_available(self) -> None:
        usage = {"input_tokens": 1_000, "output_tokens": 100}
        sol = budget.estimate_task_cost(
            usage=usage,
            summary_text="",
            provider_name="openai",
            model="gpt-5.6-sol",
        )
        luna = budget.estimate_task_cost(
            usage=usage,
            summary_text="",
            provider_name="openai",
            model="gpt-5.6-luna",
        )

        # 80 raw credits + the 10% platform markup (Epic A3).
        assert sol == 88
        assert luna == 18


class TestBudgetTrackerCheck:
    def test_ok_when_nothing_configured(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits())
        assert tracker.check().status == budget.BudgetStatus.OK

    def test_exceeded_when_task_count_hits_limit(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_tasks=2))
        tracker.record_task()
        tracker.record_task()
        result = tracker.check()
        assert result.status == budget.BudgetStatus.EXCEEDED
        assert result.dimension == "tasks"

    def test_approaching_before_exceeded(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_tasks=10))
        for _ in range(8):
            tracker.record_task()
        result = tracker.check()
        assert result.status == budget.BudgetStatus.APPROACHING
        assert result.ok is True

    def test_exceeded_result_is_not_ok(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_build_attempts=1))
        tracker.record_build_attempt()
        assert tracker.check().ok is False

    def test_credits_dimension(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_credits=1000))
        tracker.record_credits(1000)
        result = tracker.check()
        assert result.status == budget.BudgetStatus.EXCEEDED
        assert result.dimension == "credits"

    def test_mcp_and_skill_call_dimensions(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_mcp_calls=1, max_skill_calls=5))
        tracker.record_mcp_call()
        assert tracker.check().dimension == "mcp_calls"

    def test_negative_credit_amount_is_ignored(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits())
        tracker.record_credits(-50)
        assert tracker.usage.credits_used == 0


class TestBudgetTrackerClipping:
    def test_clip_context_respects_limit(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_context_chars=10))
        clipped = tracker.clip_context("a" * 100)
        assert len(clipped) < 100

    def test_clip_output_respects_limit(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_output_chars=10))
        clipped = tracker.clip_output("b" * 100)
        assert len(clipped) < 100


class TestAttemptsAllowed:
    def test_within_default_ceiling(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_task_attempts=3))
        assert tracker.attempts_allowed(0) is True
        assert tracker.attempts_allowed(2) is True
        assert tracker.attempts_allowed(3) is False

    def test_role_specific_override(self) -> None:
        tracker = budget.BudgetTracker(budget.BudgetLimits(max_task_attempts=3))
        assert tracker.attempts_allowed(4, override_max=5) is True


class TestChargeCreditsForRun:
    def test_charges_real_user_balance(self, db: Session) -> None:
        user = User(email=f"{uuid.uuid4().hex}@example.com", credits_balance=1000)
        db.add(user)
        db.flush()

        budget.charge_credits_for_run(db, user, project_id=uuid.uuid4(), amount=150)
        assert user.credits_balance == 850

    def test_zero_or_negative_amount_is_a_noop(self, db: Session) -> None:
        user = User(email=f"{uuid.uuid4().hex}@example.com", credits_balance=1000)
        db.add(user)
        db.flush()

        budget.charge_credits_for_run(db, user, project_id=uuid.uuid4(), amount=0)
        assert user.credits_balance == 1000
