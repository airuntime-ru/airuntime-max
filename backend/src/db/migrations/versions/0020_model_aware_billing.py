"""Add model and token-price snapshots to credit ledger entries.

Revision ID: 0020_model_aware_billing
Revises: 0019_orchestration_core
Create Date: 2026-07-27 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_model_aware_billing"
down_revision: str | None = "0019_orchestration_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credit_ledger_entries", sa.Column("provider", sa.String(32), nullable=True))
    op.add_column("credit_ledger_entries", sa.Column("model", sa.String(128), nullable=True))
    op.add_column("credit_ledger_entries", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "credit_ledger_entries", sa.Column("cached_input_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "credit_ledger_entries",
        sa.Column("cache_write_input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column("credit_ledger_entries", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "credit_ledger_entries",
        sa.Column("provider_cost_usd_micros", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("credit_ledger_entries", "provider_cost_usd_micros")
    op.drop_column("credit_ledger_entries", "output_tokens")
    op.drop_column("credit_ledger_entries", "cache_write_input_tokens")
    op.drop_column("credit_ledger_entries", "cached_input_tokens")
    op.drop_column("credit_ledger_entries", "input_tokens")
    op.drop_column("credit_ledger_entries", "model")
    op.drop_column("credit_ledger_entries", "provider")
