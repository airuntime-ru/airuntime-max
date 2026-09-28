"""MAX messenger surface: owners, generated services and the leads they collect.

One AIRuntime MAX bot serves two audiences at once, which is why the owner and the
customer never share a table:

- an **owner** is whoever opened the bot and described a service in one prompt; the bot
  keeps their wizard state here so a half-finished description survives a restart;
- a **service** is the generated storefront (booking / menu / landing). Its whole shape
  lives in ``config_json`` so one multi-tenant mini app renders every tenant without a
  deploy per tenant;
- a **lead** is what a customer submits from the mini app. It lands in the owner's MAX
  chat, which is the point of the product - the business talks to its clients in MAX.

JSON columns are ``Text`` rather than ``JSONB`` to match the dominant convention in this
package (``*_json``) and keep the SQLite test shim working.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base

# Wizard states for the owner-facing dialog (services/max/bot.py owns the transitions).
DIALOG_IDLE = "idle"
DIALOG_AWAITING_BRIEF = "awaiting_brief"
DIALOG_AWAITING_EDIT = "awaiting_edit"

SERVICE_DRAFT = "draft"
SERVICE_LIVE = "live"
SERVICE_DISABLED = "disabled"

LEAD_NEW = "new"
LEAD_CONFIRMED = "confirmed"
LEAD_DECLINED = "declined"
LEAD_DONE = "done"


class MaxOwner(Base):
    """A MAX user who created at least one service (or started the wizard)."""

    __tablename__ = "max_owners"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # MAX user ids are platform-wide numeric ids, not UUIDs.
    max_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    # The dialog chat with the bot - where leads get delivered.
    max_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    username: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    dialog_state: Mapped[str] = mapped_column(String(32), default=DIALOG_IDLE, nullable=False)
    # Free-form scratch space for the wizard (e.g. which service an edit applies to).
    dialog_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MaxService(Base):
    """A storefront generated from one prompt and served by the multi-tenant mini app."""

    __tablename__ = "max_services"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("max_owners.id", ondelete="CASCADE"), nullable=False
    )
    # Goes into the deep link: https://max.ru/<bot>?startapp=<slug>
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=SERVICE_DRAFT, nullable=False)
    # Validated against services/max/schema.py before it is ever written.
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    # The original words the owner used - kept for regeneration and for the demo story.
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Set once the owner also asked for a public HTTPS site, which goes through the regular
    # AIRuntime generation pipeline instead of the config renderer.
    site_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MaxLead(Base):
    """A booking / order / enquiry submitted by a customer from the mini app."""

    __tablename__ = "max_leads"
    __table_args__ = (Index("ix_max_leads_service_created", "service_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("max_services.id", ondelete="CASCADE"), nullable=False
    )
    # The customer's MAX id when the mini app was opened inside MAX (always, in practice).
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Phone is only stored when the customer explicitly shared it via requestContact or typed it.
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    item_title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    slot_label: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=LEAD_NEW, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
