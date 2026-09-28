from __future__ import annotations

from unittest.mock import MagicMock

from src.db.models.agent_task import AgentTask
from src.services.orchestration.repository import AgentTaskRepository


def test_successful_repair_clears_previous_attempt_error() -> None:
    task = AgentTask(
        status="validating",
        error_code="loop_detected",
        error_message="old preview overflow",
    )
    repository = AgentTaskRepository(MagicMock())

    repository.transition(task, "completed")

    assert task.status == "completed"
    assert task.error_code is None
    assert task.error_message is None
