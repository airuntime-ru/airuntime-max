import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class PipelineRunMetric(Base):
    """Objective per-turn timing/iteration history for the product pipeline
    (product_pipeline.py) - one row per turn, mirroring agent_run_metric.py's shape/precedent
    but for pipeline-specific stages (brief/preview/review/fix) instead of raw provider usage.

    Complements, not replaces, the plain `logger.info("pipeline_run_metrics %s", ...)` line
    product_pipeline.py already logs on every run for live grep/debugging - this table is for
    the aggregate/historical queries a log line alone doesn't support (e.g. "what fraction of
    turns passed review on the first try").
    """

    __tablename__ = "pipeline_run_metrics"

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
    build_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preview_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # e.g. "clarifying_questions", "implementation_error", "preview_infra_failure" - null means
    # the turn ran the pipeline to a normal conclusion (review passed, or the project type
    # doesn't support preview at all).
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Full PipelineMetrics.as_log_dict()-shaped payload (stage seconds, review scores, ...) -
    # never a Secret value, see product_pipeline.py's module docstring.
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
