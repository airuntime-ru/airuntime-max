"""project services: provisioned Postgres/Redis sidecars per project

Revision ID: 0012_project_services
Revises: 0011_credit_ledger
Create Date: 2026-07-16 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_project_services"
down_revision: str | None = "0011_credit_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_services",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("container_name", sa.String(length=255), nullable=False),
        sa.Column("volume_name", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "kind", name="uq_project_services_project_kind"),
    )
    op.create_index("ix_project_services_project_id", "project_services", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_services_project_id", table_name="project_services")
    op.drop_table("project_services")
