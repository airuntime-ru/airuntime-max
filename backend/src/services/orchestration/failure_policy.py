"""FailurePolicyEngine (spec section 13): classify a failure, decide retry/repair/replace/
replan/wait/rollback/fail, and detect loops before a run grinds forever on the same problem.

Classification prefers structured signals (ValidationResult findings, an explicit error_code)
over guessing from exception text - `classify_failure` only falls back to generic
AGENT_ERROR/TOOL_ERROR when nothing more specific is available.

Loop detection is fingerprint-based (spec section 13's four signals): repeated identical error
fingerprint, repeated identical diff fingerprint ("no progress" - the agent keeps producing the
same patch and it keeps failing the same way), repeated identical plan fingerprint (replanning
converges on the same graph twice in a row - futile to try a third time), all counted by
LoopDetector, which is per-run state the caller (engine.py) owns and keeps across attempts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from src.services.orchestration.schemas import FailureClass, FailureDecision, ValidationResult


def fingerprint_text(text: str) -> str:
    """Normalizes away hex ids/hashes/timestamps/line numbers so two structurally-identical
    errors that differ only in a random id or a changing line number still fingerprint the
    same - otherwise loop detection would never fire on real repeated failures."""
    normalized = re.sub(r"\b[0-9a-fA-F]{6,}\b", "<hex>", text or "")
    normalized = re.sub(r"\d+", "<n>", normalized)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


_EXCEPTION_CLASS_HINTS: tuple[tuple[str, FailureClass], ...] = (
    ("TimeoutError", FailureClass.TIMEOUT),
    ("asyncio.TimeoutError", FailureClass.TIMEOUT),
    ("McpTimeoutError", FailureClass.TIMEOUT),
    ("McpError", FailureClass.MCP_ERROR),
    ("CancelledError", FailureClass.CANCELLED),
)

_VALIDATION_STEP_TO_CLASS: dict[str, FailureClass] = {
    "scope": FailureClass.SCOPE_VIOLATION,
    "build": FailureClass.BUILD_ERROR,
    "static": FailureClass.TEST_ERROR,
    "preview": FailureClass.VALIDATION_FAILED,
    "runtime": FailureClass.RUNTIME_ERROR,
    "security": FailureClass.VALIDATION_FAILED,
}


def classify_failure(
    *,
    validation_result: ValidationResult | None = None,
    exception: BaseException | None = None,
    error_code: str | None = None,
) -> FailureClass:
    if error_code:
        try:
            return FailureClass(error_code)
        except ValueError:
            pass

    if validation_result is not None and not validation_result.accepted:
        failed_steps = {f.step for f in validation_result.findings if not f.passed}
        for step, failure_class in _VALIDATION_STEP_TO_CLASS.items():
            if step in failed_steps:
                return failure_class
        return FailureClass.VALIDATION_FAILED

    if exception is not None:
        exception_name = type(exception).__name__
        for marker, failure_class in _EXCEPTION_CLASS_HINTS:
            if marker in exception_name or marker in str(exception):
                return failure_class
        return FailureClass.TOOL_ERROR

    return FailureClass.AGENT_ERROR


_DEFAULT_DECISIONS: dict[FailureClass, FailureDecision] = {
    FailureClass.PLANNING_ERROR: FailureDecision.REPLAN,
    FailureClass.CONTRACT_INCOMPLETE: FailureDecision.REPLAN,
    FailureClass.AGENT_ERROR: FailureDecision.RETRY,
    FailureClass.INVALID_OUTPUT: FailureDecision.RETRY,
    FailureClass.SCOPE_VIOLATION: FailureDecision.REPAIR,
    FailureClass.TOOL_ERROR: FailureDecision.RETRY,
    FailureClass.SKILL_ERROR: FailureDecision.REPLACE_SKILL,
    FailureClass.MCP_ERROR: FailureDecision.REPLACE_EXECUTOR,
    FailureClass.BUILD_ERROR: FailureDecision.REPAIR,
    FailureClass.TEST_ERROR: FailureDecision.REPAIR,
    FailureClass.DEPLOY_ERROR: FailureDecision.REPAIR,
    FailureClass.RUNTIME_ERROR: FailureDecision.REPAIR,
    FailureClass.MISSING_SECRET: FailureDecision.WAIT_FOR_USER,
    FailureClass.MISSING_SERVICE: FailureDecision.WAIT_FOR_USER,
    FailureClass.VALIDATION_FAILED: FailureDecision.REPAIR,
    FailureClass.TIMEOUT: FailureDecision.RETRY,
    FailureClass.BUDGET_EXCEEDED: FailureDecision.FAIL,
    FailureClass.CANCELLED: FailureDecision.FAIL,
}


def decide(
    *, failure_class: FailureClass, attempt: int, max_attempts: int, replanning_enabled: bool
) -> FailureDecision:
    if failure_class == FailureClass.CANCELLED:
        return FailureDecision.FAIL  # never retried - a cancelled run stays cancelled

    base = _DEFAULT_DECISIONS.get(failure_class, FailureDecision.FAIL)

    if base == FailureDecision.REPLAN and not replanning_enabled:
        return FailureDecision.FAIL

    if attempt >= max_attempts and base in (FailureDecision.RETRY, FailureDecision.REPAIR):
        return FailureDecision.REPLAN if replanning_enabled else FailureDecision.FAIL

    return base


@dataclass
class LoopDetector:
    """Per-run state (owned by engine.py, not persisted as its own table - reconstructible
    from AgentTask.error_code/evidence history on restart if ever needed, see engine.py's
    resume path). `max_repeats=2` means "the same fingerprint three times in a row" trips
    detection - two is normal (a legitimate retry can fail the same way once more before a
    repair actually changes anything); three in a row is the point diminishing returns are
    obvious."""

    max_repeats: int = 2
    _error_history: list[str] = field(default_factory=list)
    _diff_history: list[str] = field(default_factory=list)
    _plan_history: list[str] = field(default_factory=list)

    def record_failure(
        self, *, error_fingerprint: str, diff_fingerprint: str | None = None
    ) -> None:
        self._error_history.append(error_fingerprint)
        if diff_fingerprint is not None:
            self._diff_history.append(diff_fingerprint)

    def record_plan(self, plan_fingerprint: str) -> None:
        self._plan_history.append(plan_fingerprint)

    def _tail_is_uniform(self, history: list[str]) -> bool:
        window = self.max_repeats + 1
        if len(history) < window:
            return False
        return len(set(history[-window:])) == 1

    def is_repeating_error(self) -> bool:
        return self._tail_is_uniform(self._error_history)

    def is_no_progress(self) -> bool:
        return self._tail_is_uniform(self._diff_history)

    def is_repeating_plan(self) -> bool:
        return self._tail_is_uniform(self._plan_history)

    def any_loop_detected(self) -> bool:
        return self.is_repeating_error() or self.is_no_progress() or self.is_repeating_plan()


@dataclass
class FailureEvaluation:
    failure_class: FailureClass
    decision: FailureDecision
    loop_detected: bool
    reason: str


def evaluate_failure(
    *,
    attempt: int,
    max_attempts: int,
    replanning_enabled: bool,
    loop_detector: LoopDetector,
    error_message: str,
    validation_result: ValidationResult | None = None,
    exception: BaseException | None = None,
    error_code: str | None = None,
    diff_stat: str | None = None,
) -> FailureEvaluation:
    failure_class = classify_failure(
        validation_result=validation_result, exception=exception, error_code=error_code
    )

    loop_detector.record_failure(
        error_fingerprint=fingerprint_text(error_message),
        diff_fingerprint=fingerprint_text(diff_stat) if diff_stat else None,
    )
    loop_detected = loop_detector.any_loop_detected()

    decision = decide(
        failure_class=failure_class,
        attempt=attempt,
        max_attempts=max_attempts,
        replanning_enabled=replanning_enabled,
    )
    if loop_detected and decision in (
        FailureDecision.RETRY,
        FailureDecision.REPAIR,
        FailureDecision.REPLAN,
    ):
        # Grinding on an identical fingerprint burns budget for zero expected value — park
        # the run so the user can clarify the request instead of auto-replanning again.
        decision = FailureDecision.WAIT_FOR_USER

    reason = f"{failure_class.value} on attempt {attempt}/{max_attempts}"
    if loop_detected:
        reason += " - repeated failure/no-progress pattern detected"

    return FailureEvaluation(
        failure_class=failure_class, decision=decision, loop_detected=loop_detected, reason=reason
    )
