"""Support chat: one conversation per user, staff replies from Django bridge.

Revision ID: 0024_support_chat
Revises: 0023_product_analytics
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_support_chat"
down_revision = "0023_product_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_conversations_user_id", "support_conversations", ["user_id"])
    op.create_index(
        "ix_support_conversations_last_message_at",
        "support_conversations",
        ["last_message_at"],
    )

    op.create_table(
        "support_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("sender_party", sa.String(length=16), nullable=False),
        sa.Column("sender_user_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["support_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_messages_conversation_created",
        "support_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_messages_conversation_created", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_index("ix_support_conversations_last_message_at", table_name="support_conversations")
    op.drop_index("ix_support_conversations_user_id", table_name="support_conversations")
    op.drop_table("support_conversations")
