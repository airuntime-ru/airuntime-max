from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DeploymentResponse(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    image_ref: str | None
    container_id: str | None
    logs_ref: str | None
    error_text: str | None = None
    log_text: str | None = None
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class DeploymentListResponse(BaseModel):
    items: list[DeploymentResponse]
    total: int
