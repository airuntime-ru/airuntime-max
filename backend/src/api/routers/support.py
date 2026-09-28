from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.support import (
    SupportConversationResponse,
    SupportMessageResponse,
    SupportReadRequest,
    SupportSendRequest,
    SupportUnreadResponse,
)
from src.db.models.user import User
from src.db.session import get_db
from src.services import support_chat

router = APIRouter(prefix="/support", tags=["support"])


def _conv_response(db: Session, conversation_id: UUID) -> SupportConversationResponse:
    from src.db.models.support import SupportConversation

    conv = db.get(SupportConversation, conversation_id)
    assert conv is not None
    messages = support_chat.list_messages(db, conversation_id, limit=100)
    return SupportConversationResponse(
        id=conv.id,
        status=conv.status,
        messages=[
            SupportMessageResponse(
                id=m.id,
                conversation_id=m.conversation_id,
                sender_party=m.sender_party,
                sender_user_id=m.sender_user_id,
                body=m.body or "",
                read_at=m.read_at,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


@router.get("/conversation", response_model=SupportConversationResponse)
def get_my_conversation(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupportConversationResponse:
    conv = support_chat.get_or_create_conversation(db, current_user.id)
    return _conv_response(db, conv.id)


@router.get("/unread-count", response_model=SupportUnreadResponse)
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupportUnreadResponse:
    return SupportUnreadResponse(unread=support_chat.count_unread_for_user(db, current_user.id))


@router.post("/messages", response_model=SupportMessageResponse)
def send_message(
    payload: SupportSendRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupportMessageResponse:
    conv = support_chat.get_or_create_conversation(db, current_user.id)
    message = support_chat.create_message(
        db,
        conversation=conv,
        sender_party=support_chat.SENDER_USER,
        sender_user_id=current_user.id,
        body=payload.body,
    )
    return SupportMessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_party=message.sender_party,
        sender_user_id=message.sender_user_id,
        body=message.body or "",
        read_at=message.read_at,
        created_at=message.created_at,
    )


@router.post("/read")
def mark_read(
    payload: SupportReadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conv = support_chat.get_or_create_conversation(db, current_user.id)
    updated = support_chat.mark_read(
        db,
        conversation_id=conv.id,
        reader_party=support_chat.SENDER_USER,
        message_ids=payload.message_ids,
    )
    return {"updated": [str(mid) for mid in updated]}
