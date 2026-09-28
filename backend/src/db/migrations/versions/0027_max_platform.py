"""MAX messenger surface: owners, generated services, customer leads.

Revision ID: 0027_max_platform
Revises: 0026_mid_tier_plans
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_max_platform"
down_revision = "0026_mid_tier_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "max_owners",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("max_user_id", sa.BigInteger(), nullable=False),
        sa.Column("max_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("dialog_state", sa.String(length=32), nullable=False, server_default="idle"),
        sa.Column("dialog_context_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("max_user_id", name="uq_max_owners_max_user_id"),
    )
    op.create_index("ix_max_owners_max_user_id", "max_owners", ["max_user_id"])

    op.create_table(
        "max_services",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("site_project_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["max_owners.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_max_services_slug"),
    )
    op.create_index("ix_max_services_slug", "max_services", ["slug"])
    op.create_index("ix_max_services_owner_id", "max_services", ["owner_id"])

    op.create_table(
        "max_leads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=False),
        sa.Column("max_user_id", sa.BigInteger(), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("item_title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("slot_label", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["service_id"], ["max_services.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_max_leads_service_created", "max_leads", ["service_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_max_leads_service_created", table_name="max_leads")
    op.drop_table("max_leads")
    op.drop_index("ix_max_services_owner_id", table_name="max_services")
    op.drop_index("ix_max_services_slug", table_name="max_services")
    op.drop_table("max_services")
    op.drop_index("ix_max_owners_max_user_id", table_name="max_owners")
    op.drop_table("max_owners")
