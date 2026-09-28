"""project services: support arbitrary images (not just the postgres/redis presets)

Revision ID: 0013_service_images
Revises: 0012_project_services
Create Date: 2026-07-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_service_images"
down_revision: str | None = "0012_project_services"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_services",
        sa.Column("image", sa.String(length=512), nullable=False, server_default=""),
    )
    op.add_column("project_services", sa.Column("data_path", sa.String(length=255), nullable=True))
    op.alter_column("project_services", "image", server_default=None)


def downgrade() -> None:
    op.drop_column("project_services", "data_path")
    op.drop_column("project_services", "image")
