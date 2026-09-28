from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from src.core.config import settings


class OrchestrationRunCreateRequest(BaseModel):
    chat_id: UUID
    content: str = Field(min_length=1, max_length=500_000)
    provider: str | None = None
    model: str | None = None


class OrchestrationRunResponse(BaseModel):
    id: UUID
    project_id: UUID
    chat_id: UUID
    status: str
    goal: str | None
    original_request: str | None
    complexity: str | None
    plan_version: int
    provider: str | None
    model: str | None
    credits_used: int
    # None means "no explicit ceiling for this run" - the UI renders just the spend in that case.
    credit_budget: int | None = None
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field
    @property
    def cost_rub(self) -> float:
        return self.credits_used / settings.billing_credits_per_rub

    model_config = {"from_attributes": True}


class OrchestrationRunListResponse(BaseModel):
    items: list[OrchestrationRunResponse]
    total: int


class OrchestrationTaskResponse(BaseModel):
    id: UUID
    local_id: str
    title: str
    role: str
    execution_kind: str
    status: str
    attempt: int
    max_attempts: int
    sequence: int
    workspace_mode: str
    dependencies: list[str] = Field(default_factory=list)
    error_code: str | None
    error_message: str | None

    model_config = {"from_attributes": True}


class OrchestrationRunDetailResponse(OrchestrationRunResponse):
    tasks: list[OrchestrationTaskResponse]


class RunEventResponse(BaseModel):
    seq: int
    event_type: str
    payload: dict
    task_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunEventHistoryResponse(BaseModel):
    items: list[RunEventResponse]
    total: int
