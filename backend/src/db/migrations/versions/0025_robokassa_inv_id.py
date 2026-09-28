"""Robokassa InvId: integer invoice numbers for credit top-ups.

Revision ID: 0025_robokassa_inv_id
Revises: 0024_support_chat
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_robokassa_inv_id"
down_revision = "0024_support_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SEQUENCE credit_topups_inv_id_seq"))
    op.add_column("credit_topups", sa.Column("inv_id", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE credit_topups SET inv_id = nextval('credit_topups_inv_id_seq') "
            "WHERE inv_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE credit_topups "
            "ALTER COLUMN inv_id SET DEFAULT nextval('credit_topups_inv_id_seq'), "
            "ALTER COLUMN inv_id SET NOT NULL"
        )
    )
    op.execute(sa.text("ALTER SEQUENCE credit_topups_inv_id_seq OWNED BY credit_topups.inv_id"))
    op.create_index("ix_credit_topups_inv_id", "credit_topups", ["inv_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_credit_topups_inv_id", table_name="credit_topups")
    op.drop_column("credit_topups", "inv_id")
