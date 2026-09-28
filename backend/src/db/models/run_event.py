import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class RunEvent(Base):
    """Durable, ordered log of everything the orchestration engine emits for one run - the
    backing store for both the live SSE stream and reload/reconnect recovery.

    Today's chat SSE stream (chat.py) has no server-side record of what it sent - a page
    reload mid-turn can only show a stale client-side snapshot (see chat-stream-runtime.ts's
    sessionStorage hack) with no way to reconcile against what's actually still running. Every
    orchestration event is written here BEFORE (or atomically with) being pushed to any live
    subscriber, so a reconnecting client can always fetch `seq > last_seen_seq` and get an
    exact replay, then keep tailing new events - see services/orchestration/events_bus.py.

    Also doubles as the audit log for capability/skill/MCP invocations (section 11's "каждый
    вызов записывается в audit log") - those are just RunEvents with
    event_type="capability_invoked" and a structured payload, rather than a separate table.

    `(run_id, seq)` is unique - `seq` is a per-run monotonic counter assigned by
    events_bus.py, not the DB id, so ordering survives even if ids are UUIDs.
    """

    __tablename__ = "run_events"
    # See workspace_lease.py's __table_args__ comment - mirrored here so
    # `Base.metadata.create_all()` (used by tests) enforces the same per-run seq uniqueness the
    # migration declares for real deployments.
    __table_args__ = (Index("ix_run_events_run_id_seq", "run_id", "seq", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Structured payload (never a secret value - see services/orchestration/context_engine.py's
    # redaction pass, applied before any event carrying log/error text is persisted here).
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
