from src.services import docker_control_actions
from src.workers import deployment_worker


class _FakeSessionWithGet:
    def __init__(self, project) -> None:
        self._project = project
        self.closed = False

    def get(self, model, id_):
        return self._project

    def close(self) -> None:
        self.closed = True


class _FakeAdapter:
    def __init__(self, stop_calls: list[str], image_calls: list[str] | None = None) -> None:
        self._stop_calls = stop_calls
        self._image_calls = image_calls if image_calls is not None else []
        self.client = _FakeDockerClient()

    def stop_project(self, project_id: str) -> None:
        self._stop_calls.append(project_id)

    def remove_project_images(self, project_id: str) -> None:
        self._image_calls.append(project_id)


class _EmptyCollection:
    def list(self, *args, **kwargs):
        return []

    def get(self, name):
        raise RuntimeError(f"{name} not found")


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _EmptyCollection()
        self.images = _EmptyCollection()
        self.networks = _EmptyCollection()


class _FakeQuery:
    def __init__(self, result) -> None:
        self._result = result

    def filter(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, *, has_services: bool) -> None:
        self._has_services = has_services
        self.closed = False

    def query(self, model):
        return _FakeQuery(object() if self._has_services else None)

    def close(self) -> None:
        self.closed = True


def test_process_control_job_stop_leaves_services_alone(monkeypatch):
    stop_calls: list[str] = []
    teardown_calls: list[tuple] = []
    results: list[dict] = []

    monkeypatch.setattr(
        docker_control_actions, "DockerDeploymentAdapter", lambda: _FakeAdapter(stop_calls)
    )
    monkeypatch.setattr(
        docker_control_actions,
        "teardown_service_containers",
        lambda client, project_id, *, remove_volumes: teardown_calls.append(
            (project_id, remove_volumes)
        ),
    )
    monkeypatch.setattr(
        deployment_worker, "push_control_result", lambda job_id, result: results.append(result)
    )

    deployment_worker.process_control_job({"job_id": "j1", "action": "stop", "project_id": "p1"})

    assert stop_calls == ["p1"]
    assert teardown_calls == []
    assert results == [{"ok": True}]


def test_process_control_job_cleanup_tears_down_services(monkeypatch):
    stop_calls: list[str] = []
    image_calls: list[str] = []
    teardown_calls: list[tuple] = []
    results: list[dict] = []

    monkeypatch.setattr(
        docker_control_actions,
        "DockerDeploymentAdapter",
        lambda: _FakeAdapter(stop_calls, image_calls),
    )
    monkeypatch.setattr(
        docker_control_actions, "SessionLocal", lambda: _FakeSession(has_services=True)
    )
    monkeypatch.setattr(
        docker_control_actions,
        "teardown_service_containers",
        lambda client, project_id, *, remove_volumes: teardown_calls.append(
            (project_id, remove_volumes)
        ),
    )
    monkeypatch.setattr(
        deployment_worker, "push_control_result", lambda job_id, result: results.append(result)
    )

    deployment_worker.process_control_job({"job_id": "j2", "action": "cleanup", "project_id": "p1"})

    assert stop_calls == ["p1"]
    assert image_calls == ["p1"]
    assert teardown_calls == [("p1", True)]
    assert results == [
        {
            "ok": True,
            "remaining": {
                "containers": [],
                "images": [],
                "networks": [],
                "volume_root": None,
            },
        }
    ]


def test_process_control_job_cleanup_tears_down_even_without_service_rows(monkeypatch):
    stop_calls: list[str] = []
    image_calls: list[str] = []
    teardown_calls: list[tuple] = []
    results: list[dict] = []

    monkeypatch.setattr(
        docker_control_actions,
        "DockerDeploymentAdapter",
        lambda: _FakeAdapter(stop_calls, image_calls),
    )
    monkeypatch.setattr(
        docker_control_actions, "SessionLocal", lambda: _FakeSession(has_services=False)
    )
    monkeypatch.setattr(
        docker_control_actions,
        "teardown_service_containers",
        lambda client, project_id, *, remove_volumes: teardown_calls.append(
            (project_id, remove_volumes)
        ),
    )
    monkeypatch.setattr(
        deployment_worker, "push_control_result", lambda job_id, result: results.append(result)
    )

    deployment_worker.process_control_job({"job_id": "j3", "action": "cleanup", "project_id": "p1"})

    assert stop_calls == ["p1"]
    assert image_calls == ["p1"]
    assert teardown_calls == [("p1", True)]
    assert results == [
        {
            "ok": True,
            "remaining": {
                "containers": [],
                "images": [],
                "networks": [],
                "volume_root": None,
            },
        }
    ]


def test_process_control_job_build_check_returns_build_result(monkeypatch):
    fake_project = object()
    monkeypatch.setattr(
        docker_control_actions, "SessionLocal", lambda: _FakeSessionWithGet(fake_project)
    )
    build_calls: list = []
    monkeypatch.setattr(
        docker_control_actions,
        "try_build_project_image",
        lambda project: build_calls.append(project) or {"ok": True, "log": "Build succeeded"},
    )
    results: list[dict] = []
    monkeypatch.setattr(
        deployment_worker, "push_control_result", lambda job_id, result: results.append(result)
    )

    deployment_worker.process_control_job(
        {"job_id": "j4", "action": "build_check", "project_id": "p1"}
    )

    assert build_calls == [fake_project]
    assert results == [{"ok": True, "log": "Build succeeded"}]


def test_process_control_job_build_check_missing_project(monkeypatch):
    monkeypatch.setattr(docker_control_actions, "SessionLocal", lambda: _FakeSessionWithGet(None))
    results: list[dict] = []
    monkeypatch.setattr(
        deployment_worker, "push_control_result", lambda job_id, result: results.append(result)
    )

    deployment_worker.process_control_job(
        {"job_id": "j5", "action": "build_check", "project_id": "p1"}
    )

    assert results == [{"ok": False, "log": "Project not found"}]
