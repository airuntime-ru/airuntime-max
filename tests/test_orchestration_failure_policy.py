"""Tests for services/orchestration/failure_policy.py - pure logic, no DB/LLM needed."""

from __future__ import annotations

from src.services.orchestration import failure_policy as fp
from src.services.orchestration.schemas import (
    FailureClass,
    FailureDecision,
    ValidationFinding,
    ValidationResult,
)


class TestFingerprintText:
    def test_same_shape_different_ids_fingerprint_identically(self) -> None:
        a = fp.fingerprint_text("Error at line 42: connection refused to abc123def456")
        b = fp.fingerprint_text("Error at line 99: connection refused to fed654cba321")
        assert a == b

    def test_different_errors_fingerprint_differently(self) -> None:
        a = fp.fingerprint_text("ModuleNotFoundError: no module named flask")
        b = fp.fingerprint_text("SyntaxError: invalid syntax")
        assert a != b

    def test_empty_text_does_not_crash(self) -> None:
        assert fp.fingerprint_text("") is not None


class TestClassifyFailure:
    def test_error_code_takes_priority(self) -> None:
        assert fp.classify_failure(error_code="missing_secret") == FailureClass.MISSING_SECRET

    def test_invalid_error_code_falls_through(self) -> None:
        assert fp.classify_failure(error_code="not_a_real_class") == FailureClass.AGENT_ERROR

    def test_validation_scope_failure_classified(self) -> None:
        vr = ValidationResult(
            accepted=False, findings=[ValidationFinding(step="scope", passed=False, message="x")]
        )
        assert fp.classify_failure(validation_result=vr) == FailureClass.SCOPE_VIOLATION

    def test_validation_build_failure_classified(self) -> None:
        vr = ValidationResult(
            accepted=False, findings=[ValidationFinding(step="build", passed=False, message="x")]
        )
        assert fp.classify_failure(validation_result=vr) == FailureClass.BUILD_ERROR

    def test_validation_runtime_failure_classified(self) -> None:
        vr = ValidationResult(
            accepted=False, findings=[ValidationFinding(step="runtime", passed=False, message="x")]
        )
        assert fp.classify_failure(validation_result=vr) == FailureClass.RUNTIME_ERROR

    def test_scope_takes_priority_over_build_when_both_fail(self) -> None:
        vr = ValidationResult(
            accepted=False,
            findings=[
                ValidationFinding(step="build", passed=False, message="x"),
                ValidationFinding(step="scope", passed=False, message="y"),
            ],
        )
        assert fp.classify_failure(validation_result=vr) == FailureClass.SCOPE_VIOLATION

    def test_timeout_exception_classified(self) -> None:
        assert fp.classify_failure(exception=TimeoutError("slow")) == FailureClass.TIMEOUT

    def test_unknown_exception_falls_back_to_tool_error(self) -> None:
        assert fp.classify_failure(exception=ValueError("weird")) == FailureClass.TOOL_ERROR

    def test_nothing_provided_falls_back_to_agent_error(self) -> None:
        assert fp.classify_failure() == FailureClass.AGENT_ERROR


class TestDecide:
    def test_retry_class_retries_while_attempts_remain(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.AGENT_ERROR,
            attempt=1,
            max_attempts=3,
            replanning_enabled=True,
        )
        assert decision == FailureDecision.RETRY

    def test_retry_class_escalates_to_replan_once_exhausted(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.AGENT_ERROR,
            attempt=3,
            max_attempts=3,
            replanning_enabled=True,
        )
        assert decision == FailureDecision.REPLAN

    def test_escalation_falls_back_to_fail_when_replanning_disabled(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.AGENT_ERROR,
            attempt=3,
            max_attempts=3,
            replanning_enabled=False,
        )
        assert decision == FailureDecision.FAIL

    def test_planning_error_wants_replan_but_falls_back_to_fail_when_disabled(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.PLANNING_ERROR,
            attempt=1,
            max_attempts=3,
            replanning_enabled=False,
        )
        assert decision == FailureDecision.FAIL

    def test_cancelled_always_fails_even_with_attempts_remaining(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.CANCELLED, attempt=1, max_attempts=3, replanning_enabled=True
        )
        assert decision == FailureDecision.FAIL

    def test_missing_secret_waits_for_user_regardless_of_attempts(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.MISSING_SECRET,
            attempt=1,
            max_attempts=3,
            replanning_enabled=True,
        )
        assert decision == FailureDecision.WAIT_FOR_USER

    def test_skill_error_replaces_skill(self) -> None:
        decision = fp.decide(
            failure_class=FailureClass.SKILL_ERROR,
            attempt=1,
            max_attempts=3,
            replanning_enabled=True,
        )
        assert decision == FailureDecision.REPLACE_SKILL


class TestLoopDetector:
    def test_no_loop_with_few_failures(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        detector.record_failure(error_fingerprint="a")
        detector.record_failure(error_fingerprint="a")
        assert detector.is_repeating_error() is False

    def test_loop_detected_after_three_identical_errors(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        for _ in range(3):
            detector.record_failure(error_fingerprint="same")
        assert detector.is_repeating_error() is True

    def test_no_loop_when_errors_differ(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        detector.record_failure(error_fingerprint="a")
        detector.record_failure(error_fingerprint="b")
        detector.record_failure(error_fingerprint="c")
        assert detector.is_repeating_error() is False

    def test_no_progress_detected_from_identical_diffs(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        for _ in range(3):
            detector.record_failure(error_fingerprint="varies", diff_fingerprint="same-diff")
        assert detector.is_no_progress() is True

    def test_repeating_plan_detected(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        for _ in range(3):
            detector.record_plan("plan-fingerprint-a")
        assert detector.is_repeating_plan() is True

    def test_any_loop_detected_combines_all_signals(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        for _ in range(3):
            detector.record_plan("same-plan")
        assert detector.any_loop_detected() is True


class TestEvaluateFailure:
    def test_first_failure_is_a_plain_retry(self) -> None:
        evaluation = fp.evaluate_failure(
            attempt=1,
            max_attempts=3,
            replanning_enabled=True,
            loop_detector=fp.LoopDetector(),
            error_message="ModuleNotFoundError: flask",
        )
        assert evaluation.decision == FailureDecision.RETRY
        assert evaluation.loop_detected is False

    def test_repeated_identical_failure_parks_at_waiting_for_user(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        evaluation = None
        for attempt in range(1, 4):
            evaluation = fp.evaluate_failure(
                attempt=attempt,
                max_attempts=10,
                replanning_enabled=True,
                loop_detector=detector,
                error_message="same failure every time",
            )
        assert evaluation.loop_detected is True
        assert evaluation.decision == FailureDecision.WAIT_FOR_USER

    def test_repeated_failure_without_replanning_waits_for_user_not_infinite_retry(self) -> None:
        detector = fp.LoopDetector(max_repeats=2)
        evaluation = None
        for attempt in range(1, 4):
            evaluation = fp.evaluate_failure(
                attempt=attempt,
                max_attempts=10,
                replanning_enabled=False,
                loop_detector=detector,
                error_message="same failure every time",
            )
        assert evaluation.decision == FailureDecision.WAIT_FOR_USER

    def test_validation_result_drives_classification(self) -> None:
        vr = ValidationResult(
            accepted=False, findings=[ValidationFinding(step="scope", passed=False, message="x")]
        )
        evaluation = fp.evaluate_failure(
            attempt=1,
            max_attempts=3,
            replanning_enabled=True,
            loop_detector=fp.LoopDetector(),
            error_message="scope violation",
            validation_result=vr,
        )
        assert evaluation.failure_class == FailureClass.SCOPE_VIOLATION
        assert evaluation.decision == FailureDecision.REPAIR
