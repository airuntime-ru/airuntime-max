"""allow secrets without a value yet (agent-requested placeholders)

Revision ID: 0008_secret_placeholders
Revises: 0007_cascade_project_deletes
Create Date: 2026-07-10 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_secret_placeholders"
down_revision: str | None = "0007_cascade_project_deletes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("secrets", "encrypted_value", existing_type=sa.Text(), nullable=True)
    op.add_column("secrets", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("secrets", "reason")
    op.execute("DELETE FROM secrets WHERE encrypted_value IS NULL")
    op.alter_column("secrets", "encrypted_value", existing_type=sa.Text(), nullable=False)
