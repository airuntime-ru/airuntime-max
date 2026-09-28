from src.core.config import settings
from src.services.agent import codex_worker


class _FakeVolume:
    attrs = {"Mountpoint": "/var/lib/docker/volumes/airuntime/_data"}


class _FakeVolumes:
    def get(self, name: str) -> _FakeVolume:
        assert name == settings.generated_projects_volume_name
        return _FakeVolume()


class _FakeClient:
    volumes = _FakeVolumes()


class _FakeContainer:
    removed = False

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


class _FakeContainers:
    def __init__(self, container: _FakeContainer) -> None:
        self.container = container

    def get(self, name: str) -> _FakeContainer:
        assert name == "airuntime-codex-job-1"
        return self.container


class _FakeCleanupClient:
    def __init__(self, container: _FakeContainer) -> None:
        self.containers = _FakeContainers(container)


def test_resolve_workspace_mount_preserves_shared_project_path(monkeypatch) -> None:
    # CI/conftest override GENERATED_PROJECTS_DIR to a temp path; pin the production layout
    # these tests describe so they do not depend on the host.
    monkeypatch.setattr(settings, "generated_projects_dir", "/data/airruntime-projects")
    result = codex_worker._resolve_workspace_mount(
        _FakeClient(), "/data/airruntime-projects/project-1"
    )
    assert result == "/var/lib/docker/volumes/airuntime/_data/project-1"


def test_resolve_workspace_mount_preserves_isolated_worktree_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "generated_projects_dir", "/data/airruntime-projects")
    result = codex_worker._resolve_workspace_mount(
        _FakeClient(),
        "/data/airruntime-projects/project-1__worktrees/task-1",
    )
    assert result == ("/var/lib/docker/volumes/airuntime/_data/project-1__worktrees/task-1")


def test_resolve_workspace_mount_rejects_path_outside_projects_volume() -> None:
    assert codex_worker._resolve_workspace_mount(_FakeClient(), "/etc") is None


def test_remove_failed_start_container_removes_created_container() -> None:
    container = _FakeContainer()

    codex_worker._remove_failed_start_container(
        _FakeCleanupClient(container), "airuntime-codex-job-1"
    )

    assert container.removed is True


def test_build_argv_uses_configured_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setattr(settings, "codex_reasoning_effort", "max")

    argv = codex_worker._build_argv(
        {"prompt": "Build the project"},
        model="gpt-5.6-sol",
        remapped_cwd=None,
    )

    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="max"'


def test_visual_review_container_has_no_docker_access(monkeypatch) -> None:
    monkeypatch.setattr(settings, "codex_docker_host", "tcp://docker-proxy:2375")

    assert codex_worker._container_volumes(None, allow_docker=False) == {}
    env = codex_worker._container_env("key", "project-1", allow_docker=False)
    assert "DOCKER_HOST" not in env
    assert "DOCKER_BUILDKIT" not in env


def test_project_container_disables_buildkit_for_socket_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "codex_docker_host", "tcp://docker-proxy:2375")

    env = codex_worker._container_env("key", "project-1", allow_docker=True)

    assert env["DOCKER_HOST"] == "tcp://docker-proxy:2375"
    assert env["DOCKER_BUILDKIT"] == "0"


def test_codex_model_id_prefixes_openai_when_proxy_base_url_is_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://routerai.ru/api/v1")
    assert codex_worker._codex_model_id("gpt-5.6-sol") == "openai/gpt-5.6-sol"
    assert codex_worker._codex_model_id("openai/gpt-5.6-luna") == "openai/gpt-5.6-luna"


def test_codex_model_id_unchanged_for_direct_openai(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", None)
    assert codex_worker._codex_model_id("gpt-5.6-sol") == "gpt-5.6-sol"


def test_proxy_base_url_skips_codex_login(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://routerai.ru/api/v1/")
    command = codex_worker._login_and_exec_command(["codex", "exec", "--json", "prompt"])
    script = command[-1]
    assert "codex login" not in script
    assert 'base_url = "https://routerai.ru/api/v1"' in script
    assert 'model_provider = "routerai"' in script
    assert "exec" in script


def test_direct_openai_still_logs_in(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", None)
    command = codex_worker._login_and_exec_command(["codex", "exec", "--json", "prompt"])
    assert "codex login --with-api-key" in command[-1]


def test_job_uses_per_turn_api_key_instead_of_platform() -> None:
    assert codex_worker._job_api_key({"api_key": "sk-user-openai"}) == "sk-user-openai"


def test_job_base_url_null_means_official_openai(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://routerai.ru/api/v1")
    assert codex_worker._job_base_url({"openai_base_url": None}) is None
    assert codex_worker._job_base_url({}) == "https://routerai.ru/api/v1"


def test_job_base_url_override_skips_login() -> None:
    command = codex_worker._login_and_exec_command(
        ["codex", "exec", "--json", "prompt"],
        base_url="https://routerai.ru/api/v1",
    )
    assert "codex login" not in command[-1]
    command = codex_worker._login_and_exec_command(
        ["codex", "exec", "--json", "prompt"],
        base_url=None,
    )
    assert "codex login --with-api-key" in command[-1]


def test_openai_byok_uses_official_openai(monkeypatch) -> None:
    from src.services.agent.codex_runtime import resolve_codex_base_url

    monkeypatch.setattr(settings, "openai_base_url", "https://routerai.ru/api/v1")
    monkeypatch.setattr(settings, "openai_api_key", "sk-platform-routerai")

    def _no_admin(_name: str) -> None:
        return None

    monkeypatch.setattr("src.services.agent.codex_runtime.resolve_api_key_for_provider", _no_admin)
    assert resolve_codex_base_url(provider_name="openai", api_key="sk-user-openai") is None
    assert (
        resolve_codex_base_url(provider_name="openai", api_key="sk-platform-routerai")
        == "https://routerai.ru/api/v1"
    )
    assert (
        resolve_codex_base_url(provider_name="routerai", api_key="sk-user-routerai")
        == "https://routerai.ru/api/v1"
    )
