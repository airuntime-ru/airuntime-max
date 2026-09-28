"""Add pipeline_run_metrics table for product-pipeline stage timing/iteration history

FKs use ondelete="CASCADE" from the start (unlike agent_run_metrics in 0015, which needed a
follow-up migration - 0016 - because it originally didn't) so deleting a project that used the
product pipeline never hits an IntegrityError the way plain agent turns briefly did.

Revision ID: 0017_pipeline_run_metrics
Revises: 0016_cascade_agent_run_metrics
Create Date: 2026-07-24 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_pipeline_run_metrics"
down_revision: str | None = "0016_cascade_agent_run_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_run_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("build_iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_reason", sa.String(64), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pipeline_run_metrics_project_id", "pipeline_run_metrics", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_run_metrics_project_id", table_name="pipeline_run_metrics")
    op.drop_table("pipeline_run_metrics")
