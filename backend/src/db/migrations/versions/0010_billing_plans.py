"""billing: plans, credit top-ups, per-user billing period

Revision ID: 0010_billing_plans
Revises: 0009_moderation
Create Date: 2026-07-10 21:00:00
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_billing_plans"
down_revision: str | None = "0009_moderation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(length=50), nullable=False, unique=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("monthly_credits", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_projects", sa.Integer(), nullable=False),
        sa.Column("price_rub", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "credit_topups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("amount_rub", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_credit_topups_user_id", "credit_topups", ["user_id"])

    op.add_column(
        "users",
        sa.Column(
            "plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("plans.id"), nullable=True
        ),
    )
    op.add_column(
        "users", sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("low_credits_notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("period_ending_notified_at", sa.DateTime(timezone=True), nullable=True)
    )

    plans_table = sa.table(
        "plans",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("monthly_credits", sa.Integer),
        sa.column("max_concurrent_projects", sa.Integer),
        sa.column("price_rub", sa.Integer),
        sa.column("is_default", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    free_id = uuid.uuid4()
    op.bulk_insert(
        plans_table,
        [
            {
                "id": free_id,
                "key": "free",
                "name": "Бесплатный",
                "description": "Чтобы попробовать платформу и запустить первый проект.",
                "monthly_credits": 50_000,
                "max_concurrent_projects": 1,
                "price_rub": 0,
                "is_default": True,
                "is_active": True,
                "sort_order": 0,
            },
            {
                "id": uuid.uuid4(),
                "key": "pro",
                "name": "Про",
                "description": "Для тех, кто ведёт несколько проектов одновременно.",
                "monthly_credits": 300_000,
                "max_concurrent_projects": 3,
                "price_rub": 990,
                "is_default": False,
                "is_active": True,
                "sort_order": 1,
            },
            {
                "id": uuid.uuid4(),
                "key": "business",
                "name": "Бизнес",
                "description": "Максимум проектов и запаса на активную разработку.",
                "monthly_credits": 1_000_000,
                "max_concurrent_projects": 10,
                "price_rub": 2990,
                "is_default": False,
                "is_active": True,
                "sort_order": 2,
            },
        ],
    )

    now = datetime.now(UTC)
    period_end = now + timedelta(days=30)
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE users SET plan_id = :plan_id, billing_period_start = :start, "
            "billing_period_end = :end WHERE plan_id IS NULL"
        ),
        {"plan_id": str(free_id), "start": now, "end": period_end},
    )


def downgrade() -> None:
    op.drop_column("users", "period_ending_notified_at")
    op.drop_column("users", "low_credits_notified_at")
    op.drop_column("users", "billing_period_end")
    op.drop_column("users", "billing_period_start")
    op.drop_column("users", "plan_id")
    op.drop_index("ix_credit_topups_user_id", table_name="credit_topups")
    op.drop_table("credit_topups")
    op.drop_table("plans")
