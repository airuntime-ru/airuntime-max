import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class OrchestrationPlan(Base):
    """A versioned execution graph for one OrchestrationRun. Planning never overwrites a prior
    plan in place - replanning (services/orchestration/replanner.py) always inserts a new row
    with `version = previous.version + 1` and marks the previous one `status="superseded"`
    (`superseded_at` set), so the full planning history of a run stays inspectable. Deliberately
    named `OrchestrationPlan`, not `Plan` - `Plan` is already the billing/subscription tier model
    (db/models/plan.py) and this must never collide with it.

    `graph_json` holds the serialized services.orchestration.schemas.ExecutionPlan (the
    declarative list of PlannedTask nodes + dependency edges the planner produced). The live,
    mutable per-task execution state is NOT duplicated here - it lives on the AgentTask rows
    created from this plan (one AgentTask per PlannedTask, linked by plan_id + local_id).
    """

    __tablename__ = "orchestration_plans"
    # See workspace_lease.py's __table_args__ comment - mirrored here so
    # `Base.metadata.create_all()` (used by tests) enforces the same "one version number per
    # run" uniqueness the migration declares for real deployments.
    __table_args__ = (
        Index("ix_orchestration_plans_run_id_version", "run_id", "version", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # "draft" (built, not yet validated) | "active" (currently driving execution) |
    # "superseded" (replaced by a later version) - see services/orchestration/status.py.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")

    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    graph_json: Mapped[str] = mapped_column(Text, nullable=False)
    risks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_criteria_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short human-readable explanation of why THIS version was created (empty/None for the
    # initial version of a run) - surfaced to the user as "plan_revised" SSE events.
    replan_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
