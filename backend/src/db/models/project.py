import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="created", nullable=False)
    logs: Mapped[str] = mapped_column(Text, default="", nullable=False)
    deployment_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deploy_subdomain: Mapped[str | None] = mapped_column(
        String(63), nullable=True, unique=True, index=True
    )
    git_history: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Custom domain (Epic D). Status: none | pending_dns | verified | error.
    custom_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    custom_domain_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    custom_domain_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    custom_domain_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    custom_domain_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
