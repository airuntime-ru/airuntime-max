import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Budget is stored in rubles, not credits: credits are an internal unit derived from
    # `billing_credits_per_rub`, and pricing/emails/UI all talk to the user in ₽.
    monthly_budget_rub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_concurrent_projects: Mapped[int] = mapped_column(Integer, nullable=False)
    max_projects: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Free plans grant their budget once at signup and never renew, so a signup wave cannot
    # turn into an unbounded monthly token bill.
    grant_renews: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Models a plan may run on the *platform* key. Empty list = whole catalog.
    allowed_models: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
