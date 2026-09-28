"""Add persistent orchestration domain: runs, versioned plans, tasks, workspace leases,
durable run events, and the MCP server allowlist.

This is the storage layer for the new orchestration engine (services/orchestration/) that
sits behind `settings.enable_orchestration_engine` (default False - see core/config.py).
With the flag off, chat.py's dispatch is byte-identical to today (same pattern already proven
by `enable_product_pipeline`); these tables simply don't get written to.

Six tables, in FK-dependency order:
  - orchestration_runs   -> projects, chats, messages, users
  - orchestration_plans  -> orchestration_runs
  - agent_tasks          -> orchestration_runs, orchestration_plans, self (parent_task_id)
  - workspace_leases     -> projects, orchestration_runs, agent_tasks
  - run_events           -> orchestration_runs, agent_tasks
  - mcp_servers          -> (none; admin-managed allowlist)

`workspace_leases` gets one partial unique index enforcing "at most one active mode='shared'
lease per project" at the database level (project_id WHERE mode='shared' AND
released_at IS NULL) - this is the actual "one shared workspace, one writer" guarantee, not
just an application-level convention. `run_events` gets a unique (run_id, seq) index since seq
is an application-assigned monotonic counter, not the row id, and is what SSE reconnect replay
keys off.

Revision ID: 0019_orchestration_core
Revises: 0018_credit_ledger_project_name
Create Date: 2026-07-24 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_orchestration_core"
down_revision: str | None = "0018_credit_ledger_project_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orchestration_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("original_request", sa.Text(), nullable=True),
        sa.Column("complexity", sa.String(16), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("context_summary", sa.Text(), nullable=True),
        sa.Column("base_commit_sha", sa.String(64), nullable=True),
        sa.Column("final_commit_sha", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("credit_budget", sa.Integer(), nullable=True),
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_usage_json", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_orchestration_runs_project_id", "orchestration_runs", ["project_id"])
    op.create_index("ix_orchestration_runs_chat_id", "orchestration_runs", ["chat_id"])

    op.create_table(
        "orchestration_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("risks_json", sa.Text(), nullable=True),
        sa.Column("acceptance_criteria_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replan_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_orchestration_plans_run_id", "orchestration_plans", ["run_id"])
    op.create_index(
        "ix_orchestration_plans_run_id_version",
        "orchestration_plans",
        ["run_id", "version"],
        unique=True,
    )

    op.create_table(
        "agent_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("local_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("execution_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("task_contract_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("validation_result_json", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("depends_on_json", sa.Text(), nullable=True),
        sa.Column("skill_id", sa.String(64), nullable=True),
        sa.Column("capability_id", sa.String(128), nullable=True),
        sa.Column(
            "workspace_mode", sa.String(32), nullable=False, server_default="shared_sequential"
        ),
        sa.Column("base_commit_sha", sa.String(64), nullable=True),
        sa.Column("accepted_commit_sha", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_agent_tasks_run_id", "agent_tasks", ["run_id"])
    op.create_index("ix_agent_tasks_plan_id", "agent_tasks", ["plan_id"])
    op.create_index(
        "ix_agent_tasks_plan_id_local_id", "agent_tasks", ["plan_id", "local_id"], unique=True
    )

    op.create_table(
        "workspace_leases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("holder", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("worktree_path", sa.String(1024), nullable=True),
        sa.Column("branch_name", sa.String(255), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workspace_leases_project_id", "workspace_leases", ["project_id"])
    op.create_index("ix_workspace_leases_run_id", "workspace_leases", ["run_id"])
    # Authoritative "one active shared-mode lease per project" guarantee - a concurrent second
    # acquire attempt hits a Postgres UniqueViolation instead of racing.
    op.create_index(
        "ix_workspace_leases_active_shared",
        "workspace_leases",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("mode = 'shared' AND released_at IS NULL"),
    )

    op.create_table(
        "run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_run_id_seq", "run_events", ["run_id", "seq"], unique=True)

    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("endpoint", sa.String(1024), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("allowed_roles_json", sa.Text(), nullable=True),
        sa.Column("credential_ref", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("mcp_servers")

    op.drop_index("ix_run_events_run_id_seq", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")

    op.drop_index("ix_workspace_leases_active_shared", table_name="workspace_leases")
    op.drop_index("ix_workspace_leases_run_id", table_name="workspace_leases")
    op.drop_index("ix_workspace_leases_project_id", table_name="workspace_leases")
    op.drop_table("workspace_leases")

    op.drop_index("ix_agent_tasks_plan_id_local_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_plan_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_run_id", table_name="agent_tasks")
    op.drop_table("agent_tasks")

    op.drop_index("ix_orchestration_plans_run_id_version", table_name="orchestration_plans")
    op.drop_index("ix_orchestration_plans_run_id", table_name="orchestration_plans")
    op.drop_table("orchestration_plans")

    op.drop_index("ix_orchestration_runs_chat_id", table_name="orchestration_runs")
    op.drop_index("ix_orchestration_runs_project_id", table_name="orchestration_runs")
    op.drop_table("orchestration_runs")
