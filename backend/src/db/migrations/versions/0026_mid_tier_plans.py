"""Add Team and Studio plans between Business and Ultra.

Revision ID: 0026_mid_tier_plans
Revises: 0025_robokassa_inv_id
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0026_mid_tier_plans"
down_revision = "0025_robokassa_inv_id"
branch_labels = None
depends_on = None

# Between Business (2 990) and Ultra (30 000). Budgets keep ~70% of price, same margin
# as Pro 990/700 and Business 2990/2200.
NEW_PLANS = (
    {
        "key": "team",
        "name": "Команда",
        "description": "Для небольшой команды, которая ведёт много проектов сразу.",
        "price_rub": 7990,
        "monthly_budget_rub": 5600,
        "max_projects": 80,
        "max_concurrent_projects": 40,
        "sort_order": 3,
    },
    {
        "key": "studio",
        "name": "Студия",
        "description": "Для студии или агентства с постоянной нагрузкой.",
        "price_rub": 14990,
        "monthly_budget_rub": 10500,
        "max_projects": 90,
        "max_concurrent_projects": 70,
        "sort_order": 4,
    },
)


def upgrade() -> None:
    conn = op.get_bind()
    for plan in NEW_PLANS:
        exists = conn.execute(
            sa.text("SELECT 1 FROM plans WHERE key = :key"), {"key": plan["key"]}
        ).scalar()
        if exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO plans (
                    id, key, name, description, monthly_budget_rub,
                    max_concurrent_projects, max_projects, price_rub,
                    grant_renews, allowed_models, is_default, is_active, sort_order
                ) VALUES (
                    :id, :key, :name, :description, :monthly_budget_rub,
                    :max_concurrent_projects, :max_projects, :price_rub,
                    true, NULL, false, true, :sort_order
                )
                """
            ),
            {**plan, "id": uuid.uuid4()},
        )
    conn.execute(sa.text("UPDATE plans SET sort_order = 5 WHERE key = 'ultra' AND sort_order < 5"))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM plans WHERE key IN ('team', 'studio')"))
    op.execute(sa.text("UPDATE plans SET sort_order = 4 WHERE key = 'ultra'"))
