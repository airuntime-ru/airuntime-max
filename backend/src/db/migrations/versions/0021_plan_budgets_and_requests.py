"""Budget-in-rubles plans, per-plan project limits, plan change requests, markup snapshot.

Replaces `plans.monthly_credits` with `plans.monthly_budget_rub` (credits stay an internal unit
derived from billing_credits_per_rub), adds `plans.max_projects` so limits come from the plan
rather than a global system setting, adds `plans.grant_renews` so the free grant can be one-shot,
adds `plans.allowed_models` for the platform-key model allowlist, introduces
`plan_change_requests`, and snapshots the billing markup on each ledger row.

Also drops the legacy 1_000_000_000 server_default on users.credits_balance - a new user's
balance now comes from the default plan's signup grant.

Revision ID: 0021_plan_budgets_and_requests
Revises: 0020_model_aware_billing
Create Date: 2026-08-07 12:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_plan_budgets_and_requests"
down_revision: str | None = "0020_model_aware_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match settings.billing_credits_per_rub. Hardcoded here on purpose: a migration has to be
# reproducible years later, independent of whatever the runtime setting has drifted to.
CREDITS_PER_RUB = 100

# Product decision 2026-08-07: free gets a one-time 100 ₽ grant, paid plans include a monthly
# budget and renew. Existing free rows (50 000 credits = 500 ₽) are reset to the new offer.
#
# Paid budgets are deliberately below the plan price so the subscription keeps a margin even
# before the +10% usage markup: Pro 990 ₽ / 700 ₽ budget, Business 2990 ₽ / 2200 ₽ budget.
# Prices themselves are left untouched here - changing what people already pay is a business
# decision, not a migration.
PLAN_DEFAULTS = {
    "free": {"budget": 100, "max_projects": 1, "concurrent": 1, "renews": False},
    "pro": {"budget": 700, "max_projects": 10, "concurrent": 5, "renews": True},
    "business": {"budget": 2200, "max_projects": 50, "concurrent": 20, "renews": True},
}

FREE_MODELS = ["gpt-5.6-luna"]


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("monthly_budget_rub", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "plans", sa.Column("max_projects", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "plans", sa.Column("grant_renews", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column("plans", sa.Column("allowed_models", postgresql.JSONB(), nullable=True))

    # Carry existing budgets over before the column disappears.
    op.execute(f"UPDATE plans SET monthly_budget_rub = monthly_credits / {CREDITS_PER_RUB}")

    for key, cfg in PLAN_DEFAULTS.items():
        op.execute(
            sa.text(
                "UPDATE plans SET monthly_budget_rub = :budget, max_projects = :max_projects, "
                "max_concurrent_projects = :concurrent, grant_renews = :renews WHERE key = :key"
            ).bindparams(
                budget=cfg["budget"],
                max_projects=cfg["max_projects"],
                concurrent=cfg["concurrent"],
                renews=cfg["renews"],
                key=key,
            )
        )

    # Free is restricted to the economy model on the platform key; paid plans stay unrestricted.
    op.execute(
        sa.text(
            "UPDATE plans SET allowed_models = CAST(:models AS jsonb) WHERE key = 'free'"
        ).bindparams(models=json.dumps(FREE_MODELS))
    )

    op.drop_column("plans", "monthly_credits")

    op.add_column("credit_ledger_entries", sa.Column("markup_percent", sa.Integer(), nullable=True))

    op.alter_column("users", "credits_balance", server_default="0")

    op.create_table(
        "plan_change_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_plan_change_requests_user_id", "plan_change_requests", ["user_id"])
    op.create_index("ix_plan_change_requests_status", "plan_change_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_plan_change_requests_status", table_name="plan_change_requests")
    op.drop_index("ix_plan_change_requests_user_id", table_name="plan_change_requests")
    op.drop_table("plan_change_requests")

    op.alter_column("users", "credits_balance", server_default="1000000000")
    op.drop_column("credit_ledger_entries", "markup_percent")

    op.add_column(
        "plans", sa.Column("monthly_credits", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute(f"UPDATE plans SET monthly_credits = monthly_budget_rub * {CREDITS_PER_RUB}")
    op.drop_column("plans", "allowed_models")
    op.drop_column("plans", "grant_renews")
    op.drop_column("plans", "max_projects")
    op.drop_column("plans", "monthly_budget_rub")
