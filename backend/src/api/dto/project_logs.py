from pydantic import BaseModel


class ProjectLogsResponse(BaseModel):
    project_logs: str
    deployment_logs: str
    runtime_logs: str
    runtime_error: str | None
    deployment_status: str | None
    container_id: str | None
    logs_ref: str | None
