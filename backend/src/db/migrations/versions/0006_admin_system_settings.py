"""admin system settings

Revision ID: 0006_admin_system_settings
Revises: 0005_project_deploy_subdomain
Create Date: 2026-07-08 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_admin_system_settings"
down_revision: str | None = "0005_project_deploy_subdomain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_system_settings" in inspector.get_table_names():
        # Table is created by django-admin migrations on production deploys.
        return

    op.create_table(
        "admin_system_settings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("setting_type", sa.String(length=32), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("value_number", sa.Integer(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cron_expression", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_admin_system_settings_key", "admin_system_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_admin_system_settings_key", table_name="admin_system_settings")
    op.drop_table("admin_system_settings")
