import json
from pathlib import Path
from uuid import uuid4

import pytest
from docker.errors import NotFound
from tests.conftest import auth_tokens

from src.core.config import settings
from src.db.models.project import Project
from src.db.models.project_service import ProjectService
from src.db.models.user import User
from src.services.project_services import (
    ProjectServiceError,
    build_connection_env,
    ensure_service_containers,
    ensure_service_request,
    host_volume_path,
    is_likely_service_credential_key,
    is_platform_managed_secret_key,
    network_name,
    project_volumes_root,
    teardown_service_containers,
    user_facing_secret_keys,
)
from src.services.secrets import encrypt_secret


def _project(db, client, *, email: str, project_type: str = "website") -> Project:
    auth_tokens(client, email)
    user = db.query(User).filter(User.email == email).first()
    project = Project(
        id=uuid4(), user_id=user.id, type=project_type, name="svc-test", description=""
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


class _FakeContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.id = name
        self.status = "running"
        self.started = False
        self.stopped = False
        self.removed = False

    def start(self) -> None:
        self.started = True
        self.status = "running"

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self) -> None:
        self._containers: dict[str, _FakeContainer] = {}
        self.run_calls: list[dict] = []

    def get(self, name: str) -> _FakeContainer:
        if name not in self._containers:
            raise NotFound("not found")
        return self._containers[name]

    def run(self, image: str, **kwargs) -> _FakeContainer:
        name = kwargs["name"]
        container = _FakeContainer(name)
        self._containers[name] = container
        self.run_calls.append({"image": image, **kwargs})
        return container

    def list(self, all: bool = False, filters: dict | None = None) -> list:
        return list(self._containers.values())


class _FakeVolume:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeVolumes:
    def __init__(self) -> None:
        self._volumes: dict[str, _FakeVolume] = {}

    def get(self, name: str) -> _FakeVolume:
        if name not in self._volumes:
            raise NotFound("not found")
        return self._volumes[name]

    def create(self, name: str, labels: dict | None = None) -> _FakeVolume:
        volume = _FakeVolume(name)
        self._volumes[name] = volume
        return volume

    def list(self, filters: dict | None = None) -> list:
        return list(self._volumes.values())


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
        self._networks: dict[str, _FakeNetwork] = {}

    def get(self, name: str) -> _FakeNetwork:
        if name not in self._networks:
            raise NotFound("not found")
        return self._networks[name]

    def create(self, name: str, driver: str = "bridge") -> _FakeNetwork:
        network = _FakeNetwork(name)
        self._networks[name] = network
        return network


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()
        self.volumes = _FakeVolumes()
        self.networks = _FakeNetworks()


def test_ensure_service_request_creates_row_once(client, db):
    project = _project(db, client, email="svc-once@airuntime.dev")

    row, created = ensure_service_request(db, project, "postgres", "нужна БД")
    assert created is True
    assert row.container_name.endswith("-postgres")

    row_again, created_again = ensure_service_request(db, project, "postgres", "нужна БД")
    assert created_again is False
    assert row_again.id == row.id
    assert db.query(ProjectService).filter(ProjectService.project_id == project.id).count() == 1


def test_ensure_service_request_enforces_max_services(client, db, monkeypatch):
    monkeypatch.setattr(settings, "max_services_per_project", 1)
    project = _project(db, client, email="svc-max@airuntime.dev")

    ensure_service_request(db, project, "postgres", "нужна БД")
    with pytest.raises(ProjectServiceError):
        ensure_service_request(db, project, "redis", "нужен кэш")


def test_ensure_service_request_rejects_unknown_kind_without_image(client, db):
    project = _project(db, client, email="svc-unknown@airuntime.dev")
    with pytest.raises(ProjectServiceError):
        ensure_service_request(db, project, "search", "нужен полнотекстовый поиск")


def test_ensure_service_request_accepts_arbitrary_image(client, db):
    project = _project(db, client, email="svc-arbitrary@airuntime.dev")

    row, created = ensure_service_request(
        db,
        project,
        "search",
        "нужен полнотекстовый поиск",
        image="elasticsearch:8.15.0",
        env={"discovery.type": "single-node"},
        data_path="/usr/share/elasticsearch/data",
    )

    assert created is True
    assert row.image == "elasticsearch:8.15.0"
    assert row.data_path == "/usr/share/elasticsearch/data"
    assert row.container_name.endswith("-search")

    # Custom kinds don't get an auto-generated connection string - only presets do.
    env = build_connection_env(db, project)
    assert env == {}


@pytest.mark.parametrize(
    "key",
    [
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "MYSQL_ROOT_PASSWORD",
        "REDIS_PASSWORD",
        "DATABASE_URL",
    ],
)
def test_is_likely_service_credential_key_flags_component_credentials(key):
    assert is_likely_service_credential_key(key, {"postgres", "mysql", "redis"}) is True


def test_is_likely_service_credential_key_ignores_unrelated_secret():
    assert not is_likely_service_credential_key("STRIPE_SECRET_KEY", {"postgres"})
    assert not is_likely_service_credential_key("TELEGRAM_BOT_TOKEN", {"postgres", "redis"})


def test_platform_managed_secrets_do_not_need_a_service_row():
    assert is_platform_managed_secret_key("DATABASE_URL")
    assert is_platform_managed_secret_key("POSTGRES_PASSWORD")
    assert is_platform_managed_secret_key("REDIS_URL")
    assert not is_platform_managed_secret_key("STRIPE_SECRET_KEY")
    assert not is_platform_managed_secret_key("TELEGRAM_BOT_TOKEN")
    assert user_facing_secret_keys(["DATABASE_URL", "STRIPE_SECRET_KEY"]) == ["STRIPE_SECRET_KEY"]


def test_build_connection_env_postgres_and_redis(client, db):
    project = _project(db, client, email="svc-env@airuntime.dev")
    ensure_service_request(db, project, "postgres", "")
    ensure_service_request(db, project, "redis", "")

    env = build_connection_env(db, project)

    assert env["DATABASE_URL"].startswith("postgresql://app:")
    assert "@airuntime-" in env["DATABASE_URL"]
    assert env["DATABASE_URL"].endswith("/app")
    assert env["REDIS_URL"].startswith("redis://:")
    assert env["REDIS_URL"].endswith(":6379/0")


def _fake_row(
    *,
    project_id: str,
    kind: str,
    container_name: str,
    volume_name: str,
    image: str,
    data_path: str | None,
    credentials: dict,
) -> ProjectService:
    return ProjectService(
        project_id=project_id,
        kind=kind,
        image=image,
        data_path=data_path,
        container_name=container_name,
        volume_name=volume_name,
        encrypted_credentials=encrypt_secret(json.dumps({"credentials": credentials, "env": {}})),
    )


def test_ensure_service_containers_creates_network_and_container():
    client_fake = _FakeDockerClient()
    project_id = "44444444-4444-4444-8444-444444444444"
    row = _fake_row(
        project_id=project_id,
        kind="postgres",
        container_name="airuntime-44444444-postgres",
        volume_name="airuntime-44444444-postgres-data",
        image="postgres:16-alpine",
        data_path="/var/lib/postgresql/data",
        credentials={"user": "app", "password": "secret", "database": "app"},
    )

    ensure_service_containers(client_fake, project_id, [row])

    client_fake.networks.get(network_name(project_id))  # raises if not created
    run_call = client_fake.containers.run_calls[0]
    assert run_call["image"] == "postgres:16-alpine"
    assert run_call["name"] == "airuntime-44444444-postgres"
    assert "ports" not in run_call
    assert run_call["network"] == network_name(project_id)
    assert run_call["restart_policy"] == {"Name": "unless-stopped"}
    assert run_call["volumes"] == {
        host_volume_path(project_id, "postgres"): {
            "bind": "/var/lib/postgresql/data",
            "mode": "rw",
        }
    }


def test_ensure_service_containers_supports_arbitrary_image():
    client_fake = _FakeDockerClient()
    project_id = "77777777-7777-4777-8777-777777777777"
    row = _fake_row(
        project_id=project_id,
        kind="search",
        container_name="airuntime-77777777-search",
        volume_name="airuntime-77777777-search-data",
        image="elasticsearch:8.15.0",
        data_path="/usr/share/elasticsearch/data",
        credentials={},
    )

    ensure_service_containers(client_fake, project_id, [row])

    run_call = client_fake.containers.run_calls[0]
    assert run_call["image"] == "elasticsearch:8.15.0"
    assert run_call["volumes"] == {
        host_volume_path(project_id, "search"): {"bind": row.data_path, "mode": "rw"}
    }


def test_ensure_service_containers_is_idempotent():
    client_fake = _FakeDockerClient()
    project_id = "55555555-5555-4555-8555-555555555555"
    row = _fake_row(
        project_id=project_id,
        kind="redis",
        container_name="airuntime-55555555-redis",
        volume_name="airuntime-55555555-redis-data",
        image="redis:7-alpine",
        data_path="/data",
        credentials={"password": "secret"},
    )

    ensure_service_containers(client_fake, project_id, [row])
    ensure_service_containers(client_fake, project_id, [row])

    assert len(client_fake.containers.run_calls) == 1


def test_teardown_service_containers_removes_volumes_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "deployment_volumes_dir", str(tmp_path))
    client_fake = _FakeDockerClient()
    project_id = "66666666-6666-4666-8666-666666666666"
    row = _fake_row(
        project_id=project_id,
        kind="postgres",
        container_name="airuntime-66666666-postgres",
        volume_name="airuntime-66666666-postgres-data",
        image="postgres:16-alpine",
        data_path="/var/lib/postgresql/data",
        credentials={"user": "app", "password": "secret", "database": "app"},
    )
    ensure_service_containers(client_fake, project_id, [row])
    host_dir = host_volume_path(project_id, "postgres")
    assert Path(host_dir).is_dir()
    (Path(host_dir) / "marker").write_text("keep", encoding="utf-8")

    teardown_service_containers(client_fake, project_id, remove_volumes=False)
    assert client_fake.containers.list()[0].removed is True
    assert Path(host_dir).joinpath("marker").exists()

    teardown_service_containers(client_fake, project_id, remove_volumes=True)
    assert not Path(project_volumes_root(project_id)).exists()
