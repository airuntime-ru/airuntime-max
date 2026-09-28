"""1-on-1 support chat: one conversation thread per customer, staff replies via admin bridge."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.db.models.support import SupportConversation, SupportMessage
from src.db.models.user import User

SENDER_USER = "user"
SENDER_STAFF = "staff"
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


def get_or_create_conversation(db: Session, user_id: UUID) -> SupportConversation:
    row = db.scalar(
        select(SupportConversation)
        .where(SupportConversation.user_id == user_id)
        .order_by(
            SupportConversation.last_message_at.desc().nulls_last(),
            SupportConversation.updated_at.desc(),
        )
        .limit(1)
    )
    if row is not None:
        return row
    row = SupportConversation(user_id=user_id, status=STATUS_OPEN)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def message_to_dict(message: SupportMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "sender_party": message.sender_party,
        "sender_user_id": str(message.sender_user_id),
        "body": message.body or "",
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def list_messages(
    db: Session, conversation_id: UUID, *, limit: int = 100, before_id: UUID | None = None
) -> list[SupportMessage]:
    query = select(SupportMessage).where(SupportMessage.conversation_id == conversation_id)
    if before_id is not None:
        anchor = db.get(SupportMessage, before_id)
        if anchor is not None:
            query = query.where(SupportMessage.created_at < anchor.created_at)
    rows = db.scalars(query.order_by(SupportMessage.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))


def create_message(
    db: Session,
    *,
    conversation: SupportConversation,
    sender_party: str,
    sender_user_id: UUID,
    body: str,
) -> SupportMessage:
    now = datetime.now(UTC)
    text = body.strip()
    message = SupportMessage(
        conversation_id=conversation.id,
        sender_party=sender_party,
        sender_user_id=sender_user_id,
        body=text or None,
    )
    conversation.last_message_at = now
    conversation.updated_at = now
    if sender_party == SENDER_USER and conversation.status == STATUS_CLOSED:
        conversation.status = STATUS_OPEN
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def count_unread_for_user(db: Session, user_id: UUID) -> int:
    conversation = db.scalar(
        select(SupportConversation)
        .where(SupportConversation.user_id == user_id)
        .order_by(SupportConversation.last_message_at.desc().nulls_last())
        .limit(1)
    )
    if conversation is None:
        return 0
    return (
        db.scalar(
            select(func.count())
            .select_from(SupportMessage)
            .where(
                SupportMessage.conversation_id == conversation.id,
                SupportMessage.sender_party == SENDER_STAFF,
                SupportMessage.read_at.is_(None),
            )
        )
        or 0
    )


def mark_read(
    db: Session,
    *,
    conversation_id: UUID,
    reader_party: str,
    message_ids: list[UUID] | None = None,
) -> list[UUID]:
    incoming = SENDER_STAFF if reader_party == SENDER_USER else SENDER_USER
    query = select(SupportMessage).where(
        SupportMessage.conversation_id == conversation_id,
        SupportMessage.sender_party == incoming,
        SupportMessage.read_at.is_(None),
    )
    if message_ids:
        query = query.where(SupportMessage.id.in_(message_ids))
    now = datetime.now(UTC)
    updated: list[UUID] = []
    for message in db.scalars(query).all():
        message.read_at = now
        updated.append(message.id)
    if updated:
        db.commit()
    return updated


def close_conversation(db: Session, conversation_id: UUID) -> SupportConversation | None:
    row = db.get(SupportConversation, conversation_id)
    if row is None:
        return None
    row.status = STATUS_CLOSED
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


def staff_conversation_summaries(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 30,
    status_filter: str = "all",
    unread_only: bool = False,
    q: str | None = None,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    unread_subq = (
        select(func.count())
        .select_from(SupportMessage)
        .where(
            SupportMessage.conversation_id == SupportConversation.id,
            SupportMessage.sender_party == SENDER_USER,
            SupportMessage.read_at.is_(None),
        )
        .scalar_subquery()
    )
    base = select(SupportConversation, User.email, User.credits_balance, unread_subq).join(
        User, User.id == SupportConversation.user_id
    )
    if status_filter == STATUS_OPEN:
        base = base.where(SupportConversation.status == STATUS_OPEN)
    elif status_filter == STATUS_CLOSED:
        base = base.where(SupportConversation.status == STATUS_CLOSED)
    if unread_only:
        base = base.where(unread_subq > 0)
    if q and (clean := q.strip()[:200]):
        like = f"%{clean}%"
        base = base.where(or_(User.email.ilike(like)))
    count_query = select(func.count()).select_from(base.subquery())
    total = db.scalar(count_query) or 0
    rows = db.execute(
        base.order_by(SupportConversation.last_message_at.desc().nulls_last())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for conv, email, credits, unread in rows:
        last = db.scalar(
            select(SupportMessage)
            .where(SupportMessage.conversation_id == conv.id)
            .order_by(SupportMessage.created_at.desc())
            .limit(1)
        )
        items.append(
            {
                "id": str(conv.id),
                "user_id": str(conv.user_id),
                "user_email": email,
                "credits_balance": credits,
                "status": conv.status,
                "unread_from_user": int(unread or 0),
                "last_message_at": conv.last_message_at.isoformat()
                if conv.last_message_at
                else None,
                "last_message_preview": (last.body or "")[:160] if last else "",
                "last_sender_party": last.sender_party if last else None,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
