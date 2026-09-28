import re
from dataclasses import dataclass

import docker
from docker.errors import DockerException, NotFound

from src.core.config import settings


@dataclass
class DeployRequest:
    project_id: str
    image_ref: str
    subdomain: str
    environment: dict[str, str] | None = None
    expose_http: bool = True
    service_network: str | None = None


def app_container_name(project_id: str) -> str:
    return f"airuntime-{project_id[:8]}"


class DockerDeploymentAdapter:
    """Deploy per-project containers via the Docker Engine API."""

    def __init__(self) -> None:
        self._client = docker.from_env()

    @property
    def client(self):
        return self._client

    def fetch_container_logs(self, container_id: str, *, tail: int = 400) -> str:
        try:
            container = self._client.containers.get(container_id)
            raw_logs = container.logs(tail=tail, timestamps=True)
        except NotFound as exc:
            raise RuntimeError(f"Container {container_id} was not found.") from exc
        return raw_logs.decode("utf-8", errors="replace")

    def _logs_tail_for_error(self, container, *, tail: int = 400, max_chars: int = 8000) -> str:
        """Best-effort stdout/stderr from an exited/unhealthy container for deploy error_text."""
        try:
            # Prefer the live object (still present after exit) so we never race a NotFound
            # from a parallel cleanup before the error is recorded.
            raw_logs = container.logs(tail=tail, timestamps=True)
            text = raw_logs.decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001 - never mask the status failure with a log fetch error
            try:
                text = self.fetch_container_logs(container.id, tail=tail).strip()
            except Exception as log_exc:  # noqa: BLE001
                return f"(could not fetch container logs: {log_exc})"
        if not text:
            return "(container produced no stdout/stderr)"
        return text[-max_chars:]

    def _not_running_error(self, container, *, status: str, preface: str) -> RuntimeError:
        logs = self._logs_tail_for_error(container)
        return RuntimeError(f"{preface} (status: {status}).\n{logs}")

    def ensure_private_network(self, project_id: str) -> str:
        """Idempotently create the per-project bridge network used by sidecar services, so the
        app container and any Postgres/Redis sidecar can reach each other by container name."""
        from src.services.project_services import network_name

        name = network_name(project_id)
        try:
            self._client.networks.get(name)
        except NotFound:
            self._client.networks.create(name, driver="bridge")
        return name

    def attach_to_network(self, container_id: str, network_name: str) -> None:
        """containers.run() only accepts one `network` kwarg at creation time - attaching a
        second network requires this explicit post-creation connect call."""
        self._client.networks.get(network_name).connect(container_id)

    def _iter_app_containers(self, project_id: str):
        """Yield only the project app container.

        Docker's `name=` filter is a substring match, so `airuntime-{id8}` also matches
        sidecars like `airuntime-{id8}-postgres`. Exact name (+ role label) keeps stop/redeploy
        from deleting Postgres/Redis right after they were started.
        """
        exact = app_container_name(project_id)
        for existing in self._client.containers.list(all=True, filters={"name": exact}):
            name = (existing.name or "").lstrip("/")
            if name != exact:
                continue
            labels = getattr(existing, "labels", None) or {}
            if labels.get("airuntime.role") == "service":
                continue
            yield existing

    def app_container_status(self, project_id: str) -> str | None:
        """Return the canonical app container status, or ``None`` when it disappeared.

        Project.status is only bookkeeping. A host reboot, manual Docker cleanup, or an old
        deployment bug can leave a project marked ``live`` with no routable container at all.
        Keeping this lookup beside ``_iter_app_containers`` ensures every caller uses the same
        exact-name/role filtering as deploy and stop operations.
        """
        for container in self._iter_app_containers(project_id):
            try:
                container.reload()
            except DockerException:
                return None
            return str(container.status or "unknown")
        return None

    def stop_project(self, project_id: str) -> None:
        for existing in self._iter_app_containers(project_id):
            try:
                if existing.status == "running":
                    existing.stop(timeout=10)
            except DockerException:
                pass
            try:
                existing.remove(force=True)
            except DockerException:
                pass

    def remove_project_images(self, project_id: str) -> None:
        """Remove every locally built image for this project (see artifacts.py's _image_tag -
        `airuntime-generated-{site,bot,mixed,app}-{project_id[:12]}:latest`).

        Matches by id fragment rather than recomputing one exact tag: a project's `type` (and so
        its tag prefix) can change over its life via reconcile_type_with_workspace/
        update_project_type_from_prompt, so an earlier build may sit under a different prefix than
        the project's current type - only called from cleanup (project deletion), never a plain
        stop/redeploy, which must keep the current image for a fast restart.
        """
        id_fragment = str(project_id)[:12]
        try:
            # docker-py's images.list(name=...) filters on an exact repository match, not a
            # prefix/substring - not usable here since the tag's prefix depends on project.type
            # (see docstring above), so list everything and match tags in Python instead.
            images = self._client.images.list()
        except DockerException:
            return
        for image in images:
            tags = getattr(image, "tags", None) or []
            labels = ((getattr(image, "attrs", None) or {}).get("Config") or {}).get("Labels") or {}
            tagged_for_project = any(
                (tag.startswith("airuntime-generated-") or tag.startswith("airuntime-scratch-"))
                and id_fragment in tag
                for tag in tags
            )
            labeled_for_project = labels.get("airuntime.project_id") == str(project_id)
            if not tagged_for_project and not labeled_for_project:
                continue
            try:
                self._client.images.remove(image=image.id, force=True)
            except DockerException:
                pass

    def deploy(self, request: DeployRequest) -> dict:
        container_name = app_container_name(request.project_id)
        host_port = self._allocate_port(request.project_id)
        service_name = re.sub(r"[^a-z0-9-]", "-", container_name.lower()).strip("-")
        public_network = (
            settings.deployment_public_network
            if request.expose_http and settings.deployment_public_network
            else None
        )
        # Local/dev deployments expose a host port and have no Traefik/DNS route. Returning the
        # production-looking subdomain there gives the chat a dead link even though the runtime
        # is healthy. In public-network mode the canonical HTTPS host remains correct.
        deploy_url = (
            f"http://localhost:{host_port}"
            if request.expose_http and settings.deployment_expose_host_ports and not public_network
            else settings.build_project_url(request.subdomain)
        )

        for existing in self._iter_app_containers(request.project_id):
            existing.remove(force=True)

        # Prefer the private service network as the primary network when sidecars exist, so
        # DATABASE_URL hostnames resolve immediately at process start. Traefik/public is
        # attached second for HTTP sites (dual-homed).
        primary_network = request.service_network or public_network

        try:
            ports = (
                {"80/tcp": host_port}
                if request.expose_http and settings.deployment_expose_host_ports
                else None
            )
            labels = {
                "airuntime.project_id": request.project_id,
                "airuntime.managed": "true",
            }
            if public_network:
                host = f"{request.subdomain}.{settings.resolved_app_domain}"
                labels.update(
                    {
                        "traefik.enable": "true",
                        "traefik.docker.network": public_network,
                        f"traefik.http.routers.{service_name}.rule": f"Host(`{host}`)",
                        f"traefik.http.routers.{service_name}.entrypoints": "websecure",
                        f"traefik.http.routers.{service_name}.tls.certresolver": "letsencrypt",
                        f"traefik.http.routers.{service_name}.service": service_name,
                        f"traefik.http.services.{service_name}.loadbalancer.server.port": "80",
                    }
                )
            # unless-stopped: survive process crashes / host reboots until the user
            # (or billing/control) explicitly stops the project. Without this, an exited
            # container stays dead and Traefik drops the route — sites look "private"
            # until someone redeploys from the cabinet.
            container = self._client.containers.run(
                request.image_ref,
                detach=True,
                name=container_name,
                labels=labels,
                ports=ports,
                environment=request.environment or None,
                mem_limit=settings.deployment_memory_limit,
                nano_cpus=int(float(settings.deployment_cpu_limit) * 1_000_000_000),
                network=primary_network,
                restart_policy={"Name": "unless-stopped"},
            )
        except DockerException as exc:
            raise RuntimeError(str(exc)) from exc

        # Dual-home HTTP apps: already on private (DNS to Postgres); attach Traefik/public second.
        if request.service_network and public_network and request.service_network != public_network:
            try:
                self.attach_to_network(container.id, public_network)
            except DockerException as exc:
                container.reload()
                if container.status != "running":
                    raise self._not_running_error(
                        container,
                        status=container.status,
                        preface="Container is not running",
                    ) from exc
                raise RuntimeError(str(exc)) from exc

        # Do not remove the container on failure — exited containers keep their logs, and
        # the next deploy / stop_project cleans them up. Capture logs *before* any remove.
        container.reload()
        if container.status != "running":
            raise self._not_running_error(
                container,
                status=container.status,
                preface="Container is not running",
            )

        container_id = container.id
        logs_ref = f"docker://{container_id}"

        return {
            "status": "running",
            "container_id": container_id,
            "url": deploy_url,
            "host_port": host_port,
            "image_ref": request.image_ref,
            "logs_ref": logs_ref,
        }

    def verify_still_running(self, container_id: str, *, settle_seconds: float = 5.0) -> str:
        """Wait briefly after start, then require the container to still be running.

        Returns the latest container log tail for startup-error scanning.
        """
        import time

        if settle_seconds > 0:
            time.sleep(settle_seconds)
        try:
            container = self._client.containers.get(container_id)
            container.reload()
        except NotFound as exc:
            raise RuntimeError(f"Container {container_id} was not found.") from exc
        if container.status != "running":
            raise self._not_running_error(
                container,
                status=container.status,
                preface="Container exited shortly after start",
            )
        return self.fetch_container_logs(container_id, tail=400)

    def _allocate_port(self, project_id: str) -> int:
        base = settings.deployment_port_base
        offset = int(project_id.replace("-", "")[:6], 16) % 5000
        return base + offset


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return slug or "project"
