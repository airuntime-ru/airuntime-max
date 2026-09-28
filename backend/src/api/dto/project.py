from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectType(StrEnum):
    telegram_bot = "telegram_bot"
    website = "website"
    mixed = "mixed"


class ProjectCreateRequest(BaseModel):
    type: ProjectType | None = None
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, max_length=64)
    deploy_subdomain: str | None = Field(default=None, max_length=63)


class ProjectResponse(BaseModel):
    id: UUID
    type: ProjectType
    name: str
    description: str
    status: str
    logs: str
    deployment_url: str | None
    deploy_subdomain: str | None
    blocked_reason: str | None = None
    planned_site_url: str | None = None
    git_history: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_project(cls, project) -> "ProjectResponse":
        from src.services.project_subdomain import planned_public_url

        return cls.model_validate(project).model_copy(
            update={"planned_site_url": planned_public_url(project)}
        )

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
    deployed_total: int


class ProjectGenerationUsageResponse(BaseModel):
    """Cumulative generation cost for the project overview card."""

    credits_spent: int
    cost_rub: float
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    total_tokens: int
    generation_seconds: float
    runs_count: int
    charge_events: int
