from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SupportMessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_party: str
    sender_user_id: UUID
    body: str
    read_at: datetime | None
    created_at: datetime


class SupportConversationResponse(BaseModel):
    id: UUID
    status: str
    messages: list[SupportMessageResponse]


class SupportSendRequest(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class SupportReadRequest(BaseModel):
    message_ids: list[UUID] | None = None


class SupportUnreadResponse(BaseModel):
    unread: int


class StaffConversationSummary(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    credits_balance: int
    status: str
    unread_from_user: int
    last_message_at: str | None
    last_message_preview: str
    last_sender_party: str | None


class StaffConversationListResponse(BaseModel):
    items: list[StaffConversationSummary]
    total: int
    page: int
    page_size: int
