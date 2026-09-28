from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.api.dto.files import ChatFileResponse, MessageCreateRequest, StreamRequest

__all__ = [
    "ChatCreateResponse",
    "MessageCreateRequest",
    "MessageResponse",
    "StreamRequest",
    "ChatFileResponse",
]


class ChatCreateResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content_markdown: str
    created_at: datetime
    attachments: list[ChatFileResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}
