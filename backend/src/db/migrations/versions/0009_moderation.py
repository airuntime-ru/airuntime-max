"""content moderation: blocked projects, user bans, audit trail

Revision ID: 0009_moderation
Revises: 0008_secret_placeholders
Create Date: 2026-07-10 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_moderation"
down_revision: str | None = "0008_secret_placeholders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("blocked_reason", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("banned_reason", sa.String(length=500), nullable=True))

    op.create_table(
        "moderation_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_moderation_events_user_id", "moderation_events", ["user_id"])
    op.create_index("ix_moderation_events_project_id", "moderation_events", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_moderation_events_project_id", table_name="moderation_events")
    op.drop_index("ix_moderation_events_user_id", table_name="moderation_events")
    op.drop_table("moderation_events")
    op.drop_column("users", "banned_reason")
    op.drop_column("users", "is_banned")
    op.drop_column("projects", "blocked_reason")
