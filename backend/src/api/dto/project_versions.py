from datetime import datetime

from pydantic import BaseModel

from src.api.dto.deployment import DeploymentResponse


class ProjectVersionResponse(BaseModel):
    commit_hash: str
    created_at: datetime
    message: str


class ProjectRollbackResponse(BaseModel):
    rollback_commit_hash: str
    deployment: DeploymentResponse


class ProjectVersionTreeEntryResponse(BaseModel):
    name: str
    entry_type: str
    size_bytes: int | None = None


class ProjectVersionFileResponse(BaseModel):
    path: str
    content: str
    is_binary: bool
    truncated: bool
    size_bytes: int
