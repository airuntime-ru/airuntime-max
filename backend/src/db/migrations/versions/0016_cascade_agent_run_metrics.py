"""cascade deletes for agent_run_metrics

0007_cascade_project_deletes added ON DELETE CASCADE for chats/messages/deployments/secrets/
chat_files so a project could actually be deleted. agent_run_metrics (0015, filed after 0007)
was never included - its project_id/chat_id/message_id FKs were created with the default NO
ACTION, so any project that ever had a chat turn recorded (i.e. almost every real project) fails
to delete: `DELETE FROM projects ...` (and the chats/messages it cascades to) hits this table's
NO ACTION constraint and the whole delete_project request 500s with an IntegrityError - after the
Docker cleanup job already ran and the workspace files were already removed, since those happen
before the DB delete in projects.py's delete_project. This just closes that gap the same way
0007 did for the other tables.

Revision ID: 0016_cascade_agent_run_metrics
Revises: 0015_agent_run_metrics
Create Date: 2026-07-23 00:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_cascade_agent_run_metrics"
down_revision: str | None = "0015_agent_run_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint_name, source_table, source_column, target_table, target_column)
_FOREIGN_KEYS = [
    ("agent_run_metrics_project_id_fkey", "agent_run_metrics", "project_id", "projects", "id"),
    ("agent_run_metrics_chat_id_fkey", "agent_run_metrics", "chat_id", "chats", "id"),
    ("agent_run_metrics_message_id_fkey", "agent_run_metrics", "message_id", "messages", "id"),
]


def upgrade() -> None:
    for name, source, column, target, target_column in _FOREIGN_KEYS:
        op.drop_constraint(name, source, type_="foreignkey")
        op.create_foreign_key(name, source, target, [column], [target_column], ondelete="CASCADE")


def downgrade() -> None:
    for name, source, column, target, target_column in _FOREIGN_KEYS:
        op.drop_constraint(name, source, type_="foreignkey")
        op.create_foreign_key(name, source, target, [column], [target_column])
