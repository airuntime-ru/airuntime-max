import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class AgentTask(Base):
    """One row per node in an OrchestrationPlan's execution graph, and the unit that a
    specialist agent/skill/MCP capability actually executes against. Carries three distinct
    JSON payloads that must never be conflated (see services/orchestration/schemas.py):

    - `task_contract_json` - the self-sufficient TaskContract the server built and sent to the
      executor (goal, current state, allowed paths, acceptance criteria, budget, ...).
    - `result_json` - the executor's own CLAIMED TaskResult. Never trusted on its own.
    - `evidence_json` - the server's FACTUAL TaskEvidence, collected independently after
      execution (real git diff, real build/test result, secret scan, ...). Validation
      (services/orchestration/validation.py) is computed from evidence, not from the claim.

    `local_id` matches the `PlannedTask.local_id` this row was created from (see
    OrchestrationPlan.graph_json) - the stable handle used for `depends_on_json` edges.
    """

    __tablename__ = "agent_tasks"
    # See workspace_lease.py's __table_args__ comment - mirrored here so
    # `Base.metadata.create_all()` (used by tests) enforces the same "local_id is unique within
    # a plan" uniqueness the migration declares for real deployments.
    __table_args__ = (Index("ix_agent_tasks_plan_id_local_id", "plan_id", "local_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_plans.id", ondelete="CASCADE"), nullable=False
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )

    local_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # services.orchestration.schemas.SpecialistRole value, e.g. "implementer", "build_fixer".
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    # skill | mcp | specialist_agent | codex_task | deterministic_validation | user_input |
    # integration - see services/orchestration/schemas.py's ExecutionKind.
    execution_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    task_contract_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # JSON list[str] of local_ids this task depends on (mirrors PlannedTask.dependencies).
    depends_on_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    skill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capability_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # shared_sequential | parallel_read_only | isolated_worktree - see
    # services/orchestration/workspace_isolation.py.
    workspace_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="shared_sequential"
    )

    base_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
