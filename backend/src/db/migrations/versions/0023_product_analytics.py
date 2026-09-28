"""Product analytics: sessions and events.

Revision ID: 0023_product_analytics
Revises: 0022_byok_and_domains
Create Date: 2026-08-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_product_analytics"
down_revision: str | None = "0022_byok_and_domains"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("anonymous_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("entry_screen", sa.String(length=64), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("utm_source", sa.String(length=128), nullable=True),
        sa.Column("utm_medium", sa.String(length=128), nullable=True),
        sa.Column("utm_campaign", sa.String(length=128), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_sessions_anonymous_id", "analytics_sessions", ["anonymous_id"])
    op.create_index("ix_analytics_sessions_user_id", "analytics_sessions", ["user_id"])
    op.create_index("ix_analytics_sessions_platform", "analytics_sessions", ["platform"])
    op.create_index("ix_analytics_sessions_started_at", "analytics_sessions", ["started_at"])

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("anonymous_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("screen", sa.String(length=64), nullable=True),
        sa.Column("tab", sa.String(length=32), nullable=True),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("props", postgresql.JSONB(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["analytics_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_events_session_id", "analytics_events", ["session_id"])
    op.create_index("ix_analytics_events_anonymous_id", "analytics_events", ["anonymous_id"])
    op.create_index("ix_analytics_events_user_id", "analytics_events", ["user_id"])
    op.create_index("ix_analytics_events_name", "analytics_events", ["name"])
    op.create_index("ix_analytics_events_screen", "analytics_events", ["screen"])
    op.create_index("ix_analytics_events_platform", "analytics_events", ["platform"])
    op.create_index("ix_analytics_events_occurred_at", "analytics_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_analytics_events_occurred_at", table_name="analytics_events")
    op.drop_index("ix_analytics_events_platform", table_name="analytics_events")
    op.drop_index("ix_analytics_events_screen", table_name="analytics_events")
    op.drop_index("ix_analytics_events_name", table_name="analytics_events")
    op.drop_index("ix_analytics_events_user_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_anonymous_id", table_name="analytics_events")
    op.drop_index("ix_analytics_events_session_id", table_name="analytics_events")
    op.drop_table("analytics_events")

    op.drop_index("ix_analytics_sessions_started_at", table_name="analytics_sessions")
    op.drop_index("ix_analytics_sessions_platform", table_name="analytics_sessions")
    op.drop_index("ix_analytics_sessions_user_id", table_name="analytics_sessions")
    op.drop_index("ix_analytics_sessions_anonymous_id", table_name="analytics_sessions")
    op.drop_table("analytics_sessions")
