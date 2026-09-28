"""credit ledger: per-transaction history of balance changes

Revision ID: 0011_credit_ledger
Revises: 0010_billing_plans
Create Date: 2026-07-11 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_credit_ledger"
down_revision: str | None = "0010_billing_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credit_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_credit_ledger_entries_user_id", "credit_ledger_entries", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_entries_user_id", table_name="credit_ledger_entries")
    op.drop_table("credit_ledger_entries")
