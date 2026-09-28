import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class AgentRunMetric(Base):
    """Objective per-turn timing/usage, one row per coding-agent turn that actually ran the
    agent (see chat.py's _stream_events - not written for turns that never had an API key, or
    that were only a clarifying question with no agent run at all).

    Same numbers as the "### Отчёт о выполнении" footer chat.py appends to the assistant message
    (see prompt.py's REPORT_HEADING) - kept here too, structured, so they survive as queryable
    history instead of only living as text inside one chat message.
    """

    __tablename__ = "agent_run_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    deploy_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    # Raw provider usage payload as JSON text (shape is provider-specific - see AgentDone.usage
    # in agent/events.py), not normalized into columns since the real field names weren't
    # confirmed against a live Codex run at the time this was written.
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
