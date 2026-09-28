import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class McpServer(Base):
    """Admin-configured allowlist of MCP servers the platform is willing to connect to (section
    11: "серверы подключаются только через admin allowlist"). Nothing in this table is
    reachable by an agent/LLM directly - services/orchestration/mcp/registry.py reads it to
    build the in-process capability cache, and services/orchestration/role_policy.py's policy
    matrix (allowed_capabilities per SpecialistRole) is the separate, second gate that decides
    which *project*/*role* combination may actually invoke a given server's capabilities.

    `credential_ref` is a reference/key into the existing encrypted Secret store
    (db/models/secret.py, services/secrets.py) - e.g. "mcp:<server_id>:api_key" - never a raw
    credential value. The MCP client resolves and injects the real value server-side at call
    time (services/orchestration/mcp/client.py), the same "never in an LLM prompt" boundary
    request_secret already enforces for project secrets.
    """

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # "stdio" (spawn a local command) | "http" (JSON-RPC/HTTP endpoint).
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    # Command line (stdio) or URL (http) - never contains an inline secret; use credential_ref.
    endpoint: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    # JSON list[str] of SpecialistRole values allowed to use this server at all (still further
    # narrowed per-capability by role_policy.py).
    allowed_roles_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
