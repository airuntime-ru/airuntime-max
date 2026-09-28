from docker.errors import NotFound

from src.core.config import settings
from src.services.deployment import docker_adapter
from src.services.deployment.docker_adapter import DeployRequest, DockerDeploymentAdapter


class _FakeContainer:
    id = "container-123"
    status = "running"
    name = "airuntime-11111111"
    labels: dict[str, str] = {}

    def __init__(self, name: str = "airuntime-11111111", labels: dict[str, str] | None = None):
        self.name = name
        self.labels = labels or {}
        self.removed = False

    def reload(self) -> None:
        pass

    def remove(self, force: bool = False) -> None:
        self.removed = True

    def stop(self, timeout: int = 10) -> None:
        self.status = "exited"


class _FakeContainers:
    def __init__(self) -> None:
        self.run_kwargs = None
        self._listed: list[_FakeContainer] = []

    def list(self, all: bool, filters: dict[str, str]) -> list:
        needle = (filters or {}).get("name", "")
        return [c for c in self._listed if needle in (c.name or "")]

    def run(self, *args, **kwargs) -> _FakeContainer:
        self.run_kwargs = kwargs
        return _FakeContainer(name=kwargs.get("name", "airuntime-11111111"))


class _FakeNetwork:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected_containers: list[str] = []

    def connect(self, container_id: str) -> None:
        self.connected_containers.append(container_id)

    def remove(self) -> None:
        pass


class _FakeNetworks:
    def __init__(self) -> None:
        self._networks: dict[str, _FakeNetwork] = {}

    def get(self, name: str) -> _FakeNetwork:
        if name not in self._networks:
            raise NotFound("network not found")
        return self._networks[name]

    def create(self, name: str, driver: str = "bridge") -> _FakeNetwork:
        network = _FakeNetwork(name)
        self._networks[name] = network
        return network


class _FakeImage:
    def __init__(
        self, image_id: str, tags: list[str], labels: dict[str, str] | None = None
    ) -> None:
        self.id = image_id
        self.tags = tags
        self.attrs = {"Config": {"Labels": labels or {}}}


class _FakeImages:
    def __init__(self) -> None:
        self._images: list[_FakeImage] = []
        self.removed_ids: list[str] = []

    def list(self) -> list[_FakeImage]:
        return list(self._images)

    def remove(self, image: str, force: bool = False) -> None:
        self.removed_ids.append(image)


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()
        self.networks = _FakeNetworks()
        self.images = _FakeImages()


def test_deploy_uses_traefik_labels_without_host_ports(monkeypatch):
    client = _FakeDockerClient()
    monkeypatch.setattr(docker_adapter.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "deployment_public_network", "airuntime_public")
    monkeypatch.setattr(settings, "deployment_expose_host_ports", False)
    monkeypatch.setattr(settings, "app_domain", "airuntime.ru")
    monkeypatch.setattr(settings, "public_base_domain", None)

    result = DockerDeploymentAdapter().deploy(
        DeployRequest(
            project_id="11111111-1111-4111-8111-111111111111",
            image_ref="airuntime-generated-site:latest",
            subdomain="demo-11111111",
        )
    )

    labels = client.containers.run_kwargs["labels"]
    assert result["url"] == "https://demo-11111111.airuntime.ru"
    assert client.containers.run_kwargs["ports"] is None
    assert client.containers.run_kwargs["network"] == "airuntime_public"
    assert client.containers.run_kwargs["restart_policy"] == {"Name": "unless-stopped"}
    assert labels["traefik.enable"] == "true"
    assert labels["traefik.http.routers.airuntime-11111111.rule"] == (
        "Host(`demo-11111111.airuntime.ru`)"
    )


def test_deploy_maps_host_port_and_returns_local_url_without_public_network(monkeypatch):
    client = _FakeDockerClient()
    monkeypatch.setattr(docker_adapter.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "deployment_public_network", None)
    monkeypatch.setattr(settings, "deployment_expose_host_ports", True)

    result = DockerDeploymentAdapter().deploy(
        DeployRequest(
            project_id="22222222-2222-4222-8222-222222222222",
            image_ref="airuntime-generated-site:latest",
            subdomain="demo-22222222",
        )
    )

    assert client.containers.run_kwargs["ports"] == {"80/tcp": 19962}
    assert client.containers.run_kwargs["network"] is None
    assert "traefik.enable" not in client.containers.run_kwargs["labels"]
    assert result["url"] == "http://localhost:19962"


def test_deploy_attaches_public_network_when_service_network_given(monkeypatch):
    """App starts on the private service network, then attaches Traefik/public second."""
    client = _FakeDockerClient()
    monkeypatch.setattr(docker_adapter.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "deployment_public_network", "airuntime_public")
    monkeypatch.setattr(settings, "deployment_expose_host_ports", False)

    adapter = DockerDeploymentAdapter()
    client.networks.create("airuntime_public")
    network_name = adapter.ensure_private_network("33333333-3333-4333-8333-333333333333")

    adapter.deploy(
        DeployRequest(
            project_id="33333333-3333-4333-8333-333333333333",
            image_ref="airuntime-generated-site:latest",
            subdomain="demo-33333333",
            service_network=network_name,
        )
    )

    assert client.containers.run_kwargs["network"] == network_name
    assert client.networks.get("airuntime_public").connected_containers == ["container-123"]


def test_deploy_cleanup_does_not_remove_postgres_sidecar(monkeypatch):
    client = _FakeDockerClient()
    monkeypatch.setattr(docker_adapter.docker, "from_env", lambda: client)
    monkeypatch.setattr(settings, "deployment_public_network", "airuntime_public")
    monkeypatch.setattr(settings, "deployment_expose_host_ports", False)

    project_id = "44444444-4444-4444-8444-444444444444"
    app = _FakeContainer(name="airuntime-44444444")
    postgres = _FakeContainer(
        name="airuntime-44444444-postgres",
        labels={"airuntime.role": "service", "airuntime.project_id": project_id},
    )
    client.containers._listed = [app, postgres]

    DockerDeploymentAdapter().deploy(
        DeployRequest(
            project_id=project_id,
            image_ref="airuntime-generated-bot:latest",
            subdomain="demo-44444444",
            expose_http=False,
            service_network="airuntime-svc-44444444",
        )
    )

    assert app.removed is True
    assert postgres.removed is False


def test_app_container_status_uses_exact_canonical_name(monkeypatch):
    client = _FakeDockerClient()
    monkeypatch.setattr(docker_adapter.docker, "from_env", lambda: client)
    project_id = "44444444-4444-4444-8444-444444444444"
    sidecar = _FakeContainer(
        name="airuntime-44444444-postgres",
        labels={"airuntime.role": "service", "airuntime.project_id": project_id},
    )
    app = _FakeContainer(name="airuntime-44444444")
    client.containers._listed = [sidecar, app]

    assert DockerDeploymentAdapter().app_container_status(project_id) == "running"

    client.containers._listed = [sidecar]
    assert DockerDeploymentAdapter().app_container_status(project_id) is None


def test_remove_project_images_matches_by_id_fragment_across_type_prefixes(monkeypatch):
    """_image_tag's prefix (site/bot/mixed/app) follows project.type, which can change over a
    project's life (reconcile_type_with_workspace/update_project_type_from_prompt) - an image
    built under an earlier type must still be found and removed on deletion, not just whatever
    the project's current type would tag today."""
    client = _FakeDockerClient()
    monkeypatch.setattr(docker_adapter.docker, "from_env", lambda: client)

    project_id = "55555555-5555-4555-8555-555555555555"
    other_project_id = "66666666-6666-4666-8666-666666666666"
    id_fragment = project_id[:12]

    site_image = _FakeImage("img-site", [f"airuntime-generated-site-{id_fragment}:latest"])
    mixed_image = _FakeImage("img-mixed", [f"airuntime-generated-mixed-{id_fragment}:latest"])
    scratch_image = _FakeImage("img-scratch", [f"airuntime-scratch-{id_fragment}:latest"])
    labeled_image = _FakeImage(
        "img-labeled", ["custom-test-name:latest"], {"airuntime.project_id": project_id}
    )
    other_project_image = _FakeImage(
        "img-other", [f"airuntime-generated-site-{other_project_id[:12]}:latest"]
    )
    unrelated_image = _FakeImage("img-unrelated", ["nginx:alpine"])
    client.images._images = [
        site_image,
        mixed_image,
        scratch_image,
        labeled_image,
        other_project_image,
        unrelated_image,
    ]

    DockerDeploymentAdapter().remove_project_images(project_id)

    assert set(client.images.removed_ids) == {
        "img-site",
        "img-mixed",
        "img-scratch",
        "img-labeled",
    }
