from pydantic import BaseModel


class ProjectRuntimeLimitsResponse(BaseModel):
    running: int
    max_running: int
    # Totals let the UI explain the create limit before the user hits a 409.
    # max_total is None when no plan or global setting caps the count.
    total: int = 0
    max_total: int | None = None
