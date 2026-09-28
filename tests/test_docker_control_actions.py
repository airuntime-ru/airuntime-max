from unittest.mock import Mock

from docker.errors import APIError

from src.services.docker_control_actions import (
    _removal_already_in_progress,
    _remove_owned_containers,
)


class _Containers:
    def __init__(self, container) -> None:
        self.container = container
        self.calls = 0

    def list(self, **kwargs):
        self.calls += 1
        return [self.container] if self.calls == 1 else []


def test_remove_owned_containers_treats_concurrent_docker_removal_as_idempotent():
    response = Mock(status_code=409)
    container = Mock(status="exited")
    container.remove.side_effect = APIError(
        "conflict",
        response=response,
        explanation="removal of container abc is already in progress",
    )
    client = Mock()
    client.containers = _Containers(container)

    _remove_owned_containers(client, {"label": "airuntime.project_id=project"})

    container.remove.assert_called_once_with(force=True)
    assert client.containers.calls == 2


def test_unrelated_conflict_is_not_misclassified_as_removal_in_progress():
    exc = APIError(
        "conflict",
        response=Mock(status_code=409),
        explanation="network has active endpoints",
    )
    assert _removal_already_in_progress(exc) is False
