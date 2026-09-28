from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.api.dependencies.admin import get_current_admin
from src.api.dto.support import (
    StaffConversationListResponse,
    StaffConversationSummary,
    SupportConversationResponse,
    SupportMessageResponse,
    SupportReadRequest,
    SupportSendRequest,
)
from src.db.models.user import User
from src.db.session import get_db
from src.services import support_chat

router = APIRouter(prefix="/support/staff", tags=["support-staff"])


@router.get("/conversations", response_model=StaffConversationListResponse)
def list_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    status: str = Query(default="all"),
    unread_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> StaffConversationListResponse:
    data = support_chat.staff_conversation_summaries(
        db,
        page=page,
        page_size=page_size,
        status_filter=status,
        unread_only=unread_only,
        q=q,
    )
    items = [
        StaffConversationSummary(
            id=UUID(item["id"]),
            user_id=UUID(item["user_id"]),
            user_email=item["user_email"],
            credits_balance=item["credits_balance"],
            status=item["status"],
            unread_from_user=item["unread_from_user"],
            last_message_at=item["last_message_at"],
            last_message_preview=item["last_message_preview"],
            last_sender_party=item["last_sender_party"],
        )
        for item in data["items"]
    ]
    return StaffConversationListResponse(
        items=items,
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
    )


@router.get("/conversations/{conversation_id}/messages", response_model=SupportConversationResponse)
def get_conversation_messages(
    conversation_id: UUID,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SupportConversationResponse:
    from src.db.models.support import SupportConversation

    conv = db.get(SupportConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = support_chat.list_messages(db, conversation_id, limit=200)
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


@router.post("/conversations/{conversation_id}/messages", response_model=SupportMessageResponse)
def staff_send_message(
    conversation_id: UUID,
    payload: SupportSendRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SupportMessageResponse:
    from src.db.models.support import SupportConversation

    conv = db.get(SupportConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    message = support_chat.create_message(
        db,
        conversation=conv,
        sender_party=support_chat.SENDER_STAFF,
        sender_user_id=admin.id,
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


@router.post("/conversations/{conversation_id}/read")
def staff_mark_read(
    conversation_id: UUID,
    payload: SupportReadRequest,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    updated = support_chat.mark_read(
        db,
        conversation_id=conversation_id,
        reader_party=support_chat.SENDER_STAFF,
        message_ids=payload.message_ids,
    )
    return {"updated": [str(mid) for mid in updated]}


@router.post("/conversations/{conversation_id}/close")
def staff_close(
    conversation_id: UUID,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    conv = support_chat.close_conversation(db, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": conv.status}


@router.post("/conversations/open-for-user/{user_id}", response_model=SupportConversationResponse)
def open_for_user(
    user_id: UUID,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SupportConversationResponse:
    conv = support_chat.get_or_create_conversation(db, user_id)
    messages = support_chat.list_messages(db, conv.id, limit=200)
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
