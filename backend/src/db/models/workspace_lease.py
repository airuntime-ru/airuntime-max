import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class WorkspaceLease(Base):
    """DB-backed, authoritative concurrency guard for a project's git workspace - the
    "невозможно одновременное небезопасное изменение одного workspace" invariant.

    This is deliberately a real table with a real constraint, not another best-effort
    SETNX-with-TTL like project_git.py's `with_project_git_lock` (which silently degrades to a
    no-op `_NullLock` on Redis outage or ~6s contention - see that module's docstring). The
    orchestration engine acquires a lease row before any task touches the shared workspace;
    services/orchestration/workspace_isolation.py enforces "at most one *active* mode="shared"
    lease per project" via the partial unique index `ix_workspace_leases_active_shared`
    (project_id WHERE mode='shared' AND released_at IS NULL) created in the migration - a
    second concurrent acquire attempt gets a Postgres UniqueViolation, not a race.

    mode="isolated" leases (one per concurrently-running worktree task) are NOT covered by that
    unique index - many can be active at once, each with its own worktree_path/branch_name.

    A lease is also a TTL: `expires_at` bounds how long a crashed holder can block others (a
    stale lease past `expires_at` is treated as free by acquire logic), independent of whether
    the holder process is still alive - this is what makes the system restart-safe without a
    heartbeat mechanism.
    """

    __tablename__ = "workspace_leases"
    # Mirrors the migration's postgresql_where index exactly - declared here too (not just in
    # 0019_orchestration_core.py) because tests build their schema via
    # `Base.metadata.create_all()` (see tests/conftest.py's `ensure_tables`), which never runs
    # Alembic migrations. Without this, the mutual-exclusion guarantee this table exists for
    # would silently not exist under test.
    __table_args__ = (
        Index(
            "ix_workspace_leases_active_shared",
            "project_id",
            unique=True,
            postgresql_where=text("mode = 'shared' AND released_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )

    # Opaque identifier of the process/coroutine holding the lease (e.g.
    # "backend:<hostname>:<pid>:<uuid4>") - used only for observability/debugging, never for
    # authorization (the row's existence + released_at/expires_at is authoritative).
    holder: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # "shared" | "isolated"
    worktree_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
