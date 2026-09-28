"""Status vocabularies and legal transition tables for the three stateful orchestration
entities (OrchestrationRun, OrchestrationPlan, AgentTask). Every status write in this
subsystem must go through `validate_transition()` (called from repository.py's update methods)
so an illegal jump - e.g. a stale/duplicate worker trying to move a `completed` run back to
`running` - raises instead of silently corrupting state. This is what makes state transitions
"атомарными; идемпотентными; проверяемыми; защищёнными от двойного запуска" (spec section 3):
atomic because each transition is one `UPDATE ... WHERE status = :expected` (see
repository.py), idempotent because re-applying the same transition is a no-op (checked before
raising), and checkable because illegal transitions raise a typed error instead of failing
silently.
"""

from __future__ import annotations

RUN_STATUSES = frozenset(
    {
        "created",
        "analyzing",
        "planning",
        "running",
        "validating",
        "replanning",
        "waiting_for_user",
        "integrating",
        "building",
        "deploying",
        "verifying_runtime",
        "completed",
        "failed",
        "cancelled",
    }
)

RUN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Forward-progress edges only; cancellation/failure edges are added for every non-terminal
# status below so they don't need to be repeated per-row.
_RUN_FORWARD_EDGES: dict[str, frozenset[str]] = {
    "created": frozenset({"analyzing"}),
    "analyzing": frozenset({"planning"}),
    "planning": frozenset({"running", "waiting_for_user"}),
    "running": frozenset({"validating", "replanning", "waiting_for_user", "integrating"}),
    "validating": frozenset({"running", "replanning", "integrating"}),
    "replanning": frozenset({"running", "planning", "waiting_for_user"}),
    "waiting_for_user": frozenset({"running", "planning"}),
    "integrating": frozenset({"building", "running"}),
    "building": frozenset({"deploying", "running"}),
    "deploying": frozenset({"verifying_runtime", "running"}),
    "verifying_runtime": frozenset({"completed", "running"}),
}

RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    status: (_RUN_FORWARD_EDGES.get(status, frozenset()) | {"failed", "cancelled"})
    for status in RUN_STATUSES
    if status not in RUN_TERMINAL_STATUSES
}
for _terminal in RUN_TERMINAL_STATUSES:
    RUN_TRANSITIONS[_terminal] = frozenset()


PLAN_STATUSES = frozenset({"draft", "active", "superseded"})
PLAN_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "superseded"}),
    "active": frozenset({"superseded"}),
    "superseded": frozenset(),
}


TASK_STATUSES = frozenset(
    {
        "pending",
        "blocked",
        "ready",
        "running",
        "collecting_evidence",
        "validating",
        "repairing",
        "waiting_for_user",
        "completed",
        "failed",
        "skipped",
        "cancelled",
    }
)
TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})

_TASK_FORWARD_EDGES: dict[str, frozenset[str]] = {
    "pending": frozenset({"blocked", "ready", "skipped"}),
    "blocked": frozenset({"ready", "skipped"}),
    # ready -> waiting_for_user: the run's budget ran out before this task ever started, so it is
    # parked (not skipped - skipped is terminal and would silently drop the work) until the user
    # tops up and resumes. See engine.py's BudgetStatus.EXCEEDED branch.
    "ready": frozenset({"running", "skipped", "waiting_for_user"}),
    # running -> waiting_for_user: budget/cancel can land after the task has already started
    # (wave member exception containment parks rather than crashing the whole run).
    "running": frozenset({"collecting_evidence", "ready", "waiting_for_user"}),
    "collecting_evidence": frozenset({"validating"}),
    "validating": frozenset({"completed", "repairing", "waiting_for_user"}),
    # repairing -> waiting_for_user: budget can run out between validation retries (engine.py
    # re-enters _run_one_task's loop while the task is still in repairing).
    "repairing": frozenset({"running", "waiting_for_user"}),
    "waiting_for_user": frozenset({"ready", "running"}),
}

TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    status: (_TASK_FORWARD_EDGES.get(status, frozenset()) | {"failed", "cancelled"})
    for status in TASK_STATUSES
    if status not in TASK_TERMINAL_STATUSES
}
for _terminal in TASK_TERMINAL_STATUSES:
    TASK_TRANSITIONS[_terminal] = frozenset()


class IllegalStatusTransition(RuntimeError):
    def __init__(self, *, entity: str, current: str, requested: str) -> None:
        self.entity = entity
        self.current = current
        self.requested = requested
        super().__init__(f"Illegal {entity} status transition: {current!r} -> {requested!r}")


def _validate(
    *, entity: str, transitions: dict[str, frozenset[str]], current: str, requested: str
) -> None:
    if current == requested:
        return  # idempotent no-op re-application
    legal = transitions.get(current)
    if legal is None:
        raise IllegalStatusTransition(entity=entity, current=current, requested=requested)
    if requested not in legal:
        raise IllegalStatusTransition(entity=entity, current=current, requested=requested)


def validate_run_transition(current: str, requested: str) -> None:
    _validate(
        entity="OrchestrationRun", transitions=RUN_TRANSITIONS, current=current, requested=requested
    )


def validate_plan_transition(current: str, requested: str) -> None:
    _validate(
        entity="OrchestrationPlan",
        transitions=PLAN_TRANSITIONS,
        current=current,
        requested=requested,
    )


def validate_task_transition(current: str, requested: str) -> None:
    _validate(
        entity="AgentTask", transitions=TASK_TRANSITIONS, current=current, requested=requested
    )


def is_run_terminal(status: str) -> bool:
    return status in RUN_TERMINAL_STATUSES


def is_task_terminal(status: str) -> bool:
    return status in TASK_TERMINAL_STATUSES
