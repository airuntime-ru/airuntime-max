"""preview_runner.py is worker-only Docker orchestration - these tests fake the docker-py
client (same style as test_deployment_adapter.py's _FakeDockerClient) rather than needing a
real daemon, which this sandbox doesn't have. The actual in-browser detection logic
(deployment/preview/run_preview.py) is exercised for real against a live headless Chromium in
test_run_preview_browser.py - this file is only about the container/network plumbing around it.
"""

import json
import types
import uuid
from pathlib import Path

from docker.errors import ImageNotFound, NotFound

from src.core.config import settings
from src.services import preview_runner


class _FakeContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.id = f"{name}-id"
        self.removed = False
        self.wait_error: Exception | None = None
        self.exit_code = 0
        self.log_output = b""

    def wait(self, timeout: int | None = None) -> dict:
        if self.wait_error is not None:
            raise self.wait_error
        return {"StatusCode": self.exit_code}

    def logs(self, **kwargs) -> bytes:
        return self.log_output

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeNetwork:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected: list[str] = []

    def connect(self, container_id: str) -> None:
        self.connected.append(container_id)

    def remove(self) -> None:
        pass


class _FakeNetworks:
    def __init__(self) -> None:
        self._nets: dict[str, _FakeNetwork] = {}

    def get(self, name: str) -> _FakeNetwork:
        if name not in self._nets:
            raise NotFound("network not found")
        return self._nets[name]

    def create(self, name: str, driver: str = "bridge", internal: bool = False) -> _FakeNetwork:
        net = _FakeNetwork(name)
        net.internal = internal  # type: ignore[attr-defined]
        self._nets[name] = net
        return net


class _FakeImages:
    def __init__(self, known_tags: set[str]) -> None:
        self.known_tags = known_tags

    def get(self, tag: str):
        if tag not in self.known_tags:
            raise ImageNotFound(f"no such image: {tag}")
        return object()


class _FakeVolumes:
    def get(self, name: str):
        # Always force the whole-volume fallback path (see preview_runner._prepare_output_mount)
        # so tests don't depend on this host's real Docker volume mount points.
        raise NotFound("volume not found")


class _FakeContainers:
    def __init__(self, generated_projects_dir: Path) -> None:
        self.run_calls: list[dict] = []
        self.created: list[_FakeContainer] = []
        self._generated_projects_dir = generated_projects_dir
        self.wait_error_for_preview: Exception | None = None
        self.fake_result: dict | None = None

    def run(self, image, **kwargs) -> _FakeContainer:
        self.run_calls.append({"image": image, **kwargs})
        container = _FakeContainer(kwargs.get("name", image))
        self.created.append(container)
        if image == settings.preview_image:
            if self.wait_error_for_preview is not None:
                container.wait_error = self.wait_error_for_preview
            elif self.fake_result is not None:
                env = kwargs.get("environment") or {}
                out_dir = env.get("PREVIEW_OUTPUT_DIR", "")
                marker = "/output_root/"
                if marker in out_dir:
                    relative = out_dir.split(marker, 1)[1]
                    target = self._generated_projects_dir / relative
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "result.json").write_text(json.dumps(self.fake_result))
        return container


class _FakeClient:
    def __init__(self, generated_projects_dir: Path, known_image_tags: set[str]) -> None:
        self.containers = _FakeContainers(generated_projects_dir)
        self.networks = _FakeNetworks()
        self.images = _FakeImages(known_image_tags)
        self.volumes = _FakeVolumes()


def _project(project_type: str = "website"):
    return types.SimpleNamespace(
        id=uuid.UUID("11111111-1111-4111-8111-111111111111"), type=project_type, name="Demo"
    )


def _image_tag_for(project) -> str:
    from src.services.artifacts import _image_tag

    return _image_tag(project)


def test_run_preview_rejects_telegram_bot_projects(monkeypatch):
    def _boom():
        raise AssertionError("must not touch Docker for a bot-only project")

    monkeypatch.setattr(preview_runner.docker, "from_env", _boom)

    result = preview_runner.run_preview(_project("telegram_bot"))
    assert result["status"] == "failed"
    assert "website/mixed" in result["fatal_errors"][0]


def test_run_preview_reports_missing_image(monkeypatch, tmp_path):
    project = _project("website")
    client = _FakeClient(tmp_path, known_image_tags=set())  # image never built
    monkeypatch.setattr(preview_runner.docker, "from_env", lambda: client)

    result = preview_runner.run_preview(project)
    assert result["status"] == "failed"
    assert "not built yet" in result["fatal_errors"][0]
    assert client.containers.run_calls == []  # never even tried to start a container


def test_run_preview_timeout_cleans_up_both_containers(monkeypatch, tmp_path):
    project = _project("website")
    tag = _image_tag_for(project)
    client = _FakeClient(tmp_path, known_image_tags={tag})
    client.containers.wait_error_for_preview = TimeoutError("wait timed out")
    monkeypatch.setattr(preview_runner.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))

    result = preview_runner.run_preview(project)

    assert result["status"] == "failed"
    assert "timed out" in result["fatal_errors"][0]
    assert len(client.containers.created) == 2  # app container + preview runner container
    assert all(c.removed for c in client.containers.created)


def test_run_preview_happy_path_reads_result_json(monkeypatch, tmp_path):
    project = _project("website")
    tag = _image_tag_for(project)
    client = _FakeClient(tmp_path, known_image_tags={tag})
    fake_result = {
        "status": "passed",
        "pages": [{"url": "http://app/", "viewport": {"width": 1440, "height": 900}}],
        "fatal_errors": [],
        "warnings": [],
    }
    client.containers.fake_result = fake_result
    monkeypatch.setattr(preview_runner.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))

    result = preview_runner.run_preview(project, environment={"DATABASE_URL": "postgresql://x"})

    assert result == fake_result
    # App container got the caller-supplied environment (so it renders like production).
    app_call = next(c for c in client.containers.run_calls if c["image"] == tag)
    assert app_call["environment"] == {"DATABASE_URL": "postgresql://x"}
    # Preview runner never receives a URL/host from outside - only paths, resolved server-side.
    runner_call = next(
        c for c in client.containers.run_calls if c["image"] == settings.preview_image
    )
    assert "PREVIEW_TARGET_HOST" in runner_call["environment"]
    assert runner_call["environment"]["PREVIEW_TARGET_HOST"] == app_call["name"]
    assert all(c.removed for c in client.containers.created)


def test_run_preview_missing_result_file_is_a_clean_failure(monkeypatch, tmp_path):
    project = _project("website")
    tag = _image_tag_for(project)
    client = _FakeClient(tmp_path, known_image_tags={tag})
    # No fake_result configured - the "container" exits without writing anything.
    monkeypatch.setattr(preview_runner.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))

    result = preview_runner.run_preview(project)
    assert result["status"] == "failed"
    assert "without producing a result" in result["fatal_errors"][0]


def test_run_preview_missing_result_includes_bounded_runner_diagnostics(monkeypatch, tmp_path):
    project = _project("website")
    tag = _image_tag_for(project)
    client = _FakeClient(tmp_path, known_image_tags={tag})
    monkeypatch.setattr(preview_runner.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))

    original_run = client.containers.run

    def run_with_failure(image, **kwargs):
        container = original_run(image, **kwargs)
        if image == settings.preview_image:
            container.exit_code = 1
            container.log_output = b"ModuleNotFoundError: No module named 'playwright'"
        return container

    client.containers.run = run_with_failure

    result = preview_runner.run_preview(project)

    assert "exit code 1" in result["fatal_errors"][0]
    assert "ModuleNotFoundError" in result["fatal_errors"][0]


def test_run_preview_never_raises_on_docker_exception(monkeypatch):
    from docker.errors import DockerException

    def _raise():
        raise DockerException("daemon unreachable")

    monkeypatch.setattr(preview_runner.docker, "from_env", _raise)
    result = preview_runner.run_preview(_project("website"))
    assert result["status"] == "failed"
    assert "Docker" in result["fatal_errors"][0]
