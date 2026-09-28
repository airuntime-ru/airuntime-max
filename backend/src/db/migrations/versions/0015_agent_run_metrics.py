"""Add agent_run_metrics table for objective per-turn timing/usage history

Revision ID: 0015_agent_run_metrics
Revises: 0014_deployment_error_text
Create Date: 2026-07-17 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_agent_run_metrics"
down_revision: str | None = "0014_deployment_error_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "chat_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chats.id"), nullable=False
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("agent_seconds", sa.Float(), nullable=False),
        sa.Column("deploy_seconds", sa.Float(), nullable=False),
        sa.Column("usage_json", sa.Text(), nullable=True),
        sa.Column("agent_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_run_metrics_project_id", "agent_run_metrics", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_run_metrics_project_id", table_name="agent_run_metrics")
    op.drop_table("agent_run_metrics")
