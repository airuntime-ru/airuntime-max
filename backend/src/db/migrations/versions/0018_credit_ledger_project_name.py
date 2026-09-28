"""Add project_name snapshot to credit ledger entries

Revision ID: 0018_credit_ledger_project_name
Revises: 0017_pipeline_run_metrics
Create Date: 2026-07-24 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_credit_ledger_project_name"
down_revision: str | None = "0017_pipeline_run_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "credit_ledger_entries",
        sa.Column("project_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("credit_ledger_entries", "project_name")
