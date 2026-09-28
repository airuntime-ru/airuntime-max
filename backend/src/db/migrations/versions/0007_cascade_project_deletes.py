"""cascade deletes for project-owned rows

Revision ID: 0007_cascade_project_deletes
Revises: 0006_admin_system_settings
Create Date: 2026-07-10 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_cascade_project_deletes"
down_revision: str | None = "0006_admin_system_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint_name, source_table, source_column, target_table, target_column)
_FOREIGN_KEYS = [
    ("chats_project_id_fkey", "chats", "project_id", "projects", "id"),
    ("messages_chat_id_fkey", "messages", "chat_id", "chats", "id"),
    ("deployments_project_id_fkey", "deployments", "project_id", "projects", "id"),
    ("secrets_project_id_fkey", "secrets", "project_id", "projects", "id"),
    ("chat_files_project_id_fkey", "chat_files", "project_id", "projects", "id"),
    ("chat_files_chat_id_fkey", "chat_files", "chat_id", "chats", "id"),
    ("chat_files_message_id_fkey", "chat_files", "message_id", "messages", "id"),
]


def upgrade() -> None:
    for name, source, column, target, target_column in _FOREIGN_KEYS:
        op.drop_constraint(name, source, type_="foreignkey")
        op.create_foreign_key(name, source, target, [column], [target_column], ondelete="CASCADE")


def downgrade() -> None:
    for name, source, column, target, target_column in _FOREIGN_KEYS:
        op.drop_constraint(name, source, type_="foreignkey")
        op.create_foreign_key(name, source, target, [column], [target_column])
