from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ChatFileResponse(BaseModel):
    id: UUID
    project_id: UUID
    chat_id: UUID
    message_id: UUID | None
    original_filename: str
    content_type: str
    size_bytes: int
    download_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# Match prompt_guard.MAX_USER_MESSAGE_CHARS — large enough for pasted deploy/runtime logs.
_MAX_CHAT_CONTENT = 500_000


class MessageCreateRequest(BaseModel):
    content: str = Field(default="", max_length=_MAX_CHAT_CONTENT)
    attachment_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_content_or_attachments(self) -> "MessageCreateRequest":
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("Message must include text or attachments")
        return self


class StreamRequest(BaseModel):
    content: str = Field(default="", max_length=_MAX_CHAT_CONTENT)
    attachment_ids: list[UUID] = Field(default_factory=list)
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_content_or_attachments(self) -> "StreamRequest":
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("Message must include text or attachments")
        return self


class RepairStreamRequest(BaseModel):
    """Optional log excerpt from the logs/deployments UI so repair uses what the user saw."""

    error_log: str | None = Field(default=None, max_length=_MAX_CHAT_CONTENT)
