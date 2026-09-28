"""Store full deployment error and live build log text

Revision ID: 0014_deployment_error_text
Revises: 0013_service_images
Create Date: 2026-07-16 19:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_deployment_error_text"
down_revision: str | None = "0013_service_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("error_text", sa.Text(), nullable=True))
    op.add_column("deployments", sa.Column("log_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("deployments", "log_text")
    op.drop_column("deployments", "error_text")
