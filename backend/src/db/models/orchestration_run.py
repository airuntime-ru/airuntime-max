import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class OrchestrationRun(Base):
    """One row per persistent orchestration run - the top-level unit of work spawned by every
    user chat message (the orchestration engine is the only chat-turn path). Everything else
    in the orchestration domain (OrchestrationPlan, AgentTask, WorkspaceLease, RunEvent) hangs
    off run_id, so a run surviving a backend/worker restart (it's a DB row, not in-memory state)
    is what makes the whole engine restart-safe - see services/orchestration/engine.py.

    `status` is a plain string (house convention, no Postgres ENUM - see pipeline_run_metric.py
    and every other model in this package) validated against
    services.orchestration.status.RUN_STATUSES / RUN_TRANSITIONS at the service layer.
    """

    __tablename__ = "orchestration_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: a run is created as soon as the SSE turn starts, before the assistant message
    # row exists; backfilled once the turn produces (or reuses) its Message row.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    complexity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Convenience pointer to the task currently executing - not a FK (avoids a circular
    # create-order dependency with agent_tasks and is purely a UI/resume hint; the
    # authoritative per-task state always lives on the AgentTask rows themselves).
    current_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Short provenance-tagged summary of global context handed to the planner/orchestrator -
    # see services/orchestration/context_engine.py. Never contains secret values.
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    base_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    credit_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Aggregated {"prompt_tokens":..,"completion_tokens":..,"by_task":{...}} - raw provider
    # usage is provider-specific, so this is kept as an opaque JSON blob like
    # agent_run_metric.usage_json, not normalized columns.
    token_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set by the cancel endpoint; the engine polls this between tasks/iterations (see
    # services/orchestration/cancellation.py) rather than relying on the HTTP connection alone,
    # since a Codex container must be actively killed, not just abandoned.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form extension bag (e.g. {"triggered_by": "chat", "legacy_turn": false}); never a
    # place to smuggle secret values - see services/orchestration/schemas.py's RunMetadata.
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
