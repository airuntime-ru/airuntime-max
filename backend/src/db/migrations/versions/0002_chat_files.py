"""0002 chat files

Revision ID: 0002_chat_files
Revises: 0001_initial
Create Date: 2026-07-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_chat_files"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "chat_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chats.id"), nullable=False
        ),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id"), nullable=True
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_files_chat_id", "chat_files", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_files_chat_id", table_name="chat_files")
    op.drop_table("chat_files")
