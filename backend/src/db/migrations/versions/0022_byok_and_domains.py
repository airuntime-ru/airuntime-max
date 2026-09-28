"""BYOK provider credentials and per-project custom domains.

Revision ID: 0022_byok_and_domains
Revises: 0021_plan_budgets_and_requests
Create Date: 2026-08-07 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_byok_and_domains"
down_revision: str | None = "0021_plan_budgets_and_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_provider_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(8), nullable=False, server_default=""),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )
    op.create_index(
        "ix_user_provider_credentials_user_id", "user_provider_credentials", ["user_id"]
    )

    op.add_column("projects", sa.Column("custom_domain", sa.String(253), nullable=True))
    op.add_column(
        "projects",
        sa.Column("custom_domain_status", sa.String(20), nullable=False, server_default="none"),
    )
    op.add_column(
        "projects",
        sa.Column("custom_domain_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("projects", sa.Column("custom_domain_error", sa.String(500), nullable=True))
    op.add_column(
        "projects",
        sa.Column("custom_domain_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A hostname can only point at one project.
    op.create_index(
        "uq_projects_custom_domain",
        "projects",
        ["custom_domain"],
        unique=True,
        postgresql_where=sa.text("custom_domain IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_projects_custom_domain", table_name="projects")
    op.drop_column("projects", "custom_domain_checked_at")
    op.drop_column("projects", "custom_domain_error")
    op.drop_column("projects", "custom_domain_verified_at")
    op.drop_column("projects", "custom_domain_status")
    op.drop_column("projects", "custom_domain")

    op.drop_index("ix_user_provider_credentials_user_id", table_name="user_provider_credentials")
    op.drop_table("user_provider_credentials")
