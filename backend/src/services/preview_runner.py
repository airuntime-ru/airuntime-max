"""Worker-only: runs a project's already-built image in an isolated, unpublished container,
then a fresh Playwright container (deployment/preview) against it to check requested pages -
see agent/tools.py's preview_project tool and docker_control_actions.py's "preview" action.

Isolation model (mirrors codex_worker.py's per-turn container + project_services.py's private
sidecar network - see those modules for the same reasoning applied to Codex/DB sidecars):
- The app-under-test container joins the project's existing private service network (the same
  one Postgres/Redis sidecars use - DockerDeploymentAdapter.ensure_private_network) so it renders
  exactly like production would, but gets no host port and no Traefik labels - it is otherwise
  unreachable.
- A second, separate network created with internal=True joins only that app container and the
  Playwright runner container. internal=True means Docker gives it no default route out at all -
  the runner cannot reach the internet, the Docker socket, cloud metadata, or any host-private
  address, no matter what page paths were requested.
- The Playwright runner never receives a URL, host, or scheme from the caller - only same-origin
  relative paths (validated again here, defense in depth against tools.py's own validation) - this
  module always builds the actual target itself from the app container's own name.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from src.core.config import settings
from src.db.models.project import Project
from src.services.artifacts import _image_tag
from src.services.deployment.docker_adapter import DockerDeploymentAdapter

logger = logging.getLogger(__name__)

_APP_PORT = 80
_PREVIEW_NET_PREFIX = "airuntime-preview-"
_WHOLE_VOLUME_MOUNT = "/output_root"
DEFAULT_VIEWPORT: dict[str, int] = {"width": 1440, "height": 900}
MOBILE_VIEWPORT: dict[str, int] = {"width": 390, "height": 844}
MAX_TARGETS = 10


def _preview_network_name(project_id: str) -> str:
    return f"{_PREVIEW_NET_PREFIX}{project_id[:8]}"


def _sanitize_path(raw: object) -> str | None:
    """Only a same-origin relative path is ever forwarded to the browser - never a scheme/
    host/parent traversal. Returns None for anything invalid; this is the same validation
    tools.py's preview_project handler already applies before this ever runs, kept here too
    so this module is safe to call from anywhere, not just that one path."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return None
    if ".." in candidate or "\\" in candidate or "://" in candidate:
        return None
    return candidate


def _sanitize_paths(paths: list[str] | None) -> list[str]:
    cleaned = [p for raw in (paths or ["/"]) if (p := _sanitize_path(raw)) is not None]
    return cleaned[:5] or ["/"]


def _sanitize_viewport(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return DEFAULT_VIEWPORT
    try:
        width = int(raw.get("width", DEFAULT_VIEWPORT["width"]))
        height = int(raw.get("height", DEFAULT_VIEWPORT["height"]))
    except (TypeError, ValueError):
        return DEFAULT_VIEWPORT
    if width <= 0 or height <= 0 or width > 4000 or height > 4000:
        return DEFAULT_VIEWPORT
    return {"width": width, "height": height}


def _sanitize_targets(targets: list[dict] | None) -> list[dict]:
    """Each target is {"path": str, "viewport": {"width": int, "height": int}}. Invalid
    entries are dropped individually; an empty/all-invalid list falls back to one desktop
    home check, same fail-open posture as _sanitize_paths."""
    cleaned: list[dict] = []
    for raw in targets or []:
        if not isinstance(raw, dict):
            continue
        path = _sanitize_path(raw.get("path"))
        if path is None:
            continue
        cleaned.append({"path": path, "viewport": _sanitize_viewport(raw.get("viewport"))})
        if len(cleaned) >= MAX_TARGETS:
            break
    return cleaned or [{"path": "/", "viewport": DEFAULT_VIEWPORT}]


def _targets_from_paths(paths: list[str] | None) -> list[dict]:
    return [{"path": path, "viewport": DEFAULT_VIEWPORT} for path in _sanitize_paths(paths)]


def _resolve_project_host_path(client: docker.DockerClient, project_id: str) -> str | None:
    """Real host path of this project's own subtree inside the shared projects volume - same
    approach and same caveats as codex_worker.py's _resolve_project_mount (not shared code
    only because that module is Codex-specific and this one intentionally stays independent of
    it); see that function's docstring for why `Mountpoint` is used instead of the newer
    volume-subpath mount feature. Returns None to trigger the whole-volume fallback below
    rather than raising - a degraded-but-working preview beats a hard-broken one."""
    try:
        volume = client.volumes.get(settings.generated_projects_volume_name)
        mountpoint = volume.attrs.get("Mountpoint")
        if not mountpoint:
            return None
        return str(PurePosixPath(mountpoint) / project_id)
    except (NotFound, APIError, DockerException, KeyError) as exc:
        logger.warning(
            "Could not resolve per-project mount for preview (volume %r): %s - falling back "
            "to whole-volume mount for this run.",
            settings.generated_projects_volume_name,
            exc,
        )
        return None


def _prepare_output_mount(
    client: docker.DockerClient, project_id: str, run_id: str
) -> tuple[dict[str, dict[str, str]], str, Path]:
    """Returns (volumes-kwarg-for-containers.run, in-container output dir, worker-local dir to
    read results back from). Prefers bind-mounting just this run's own output subdirectory;
    falls back to mounting the whole named volume (read-write, unavoidable - Docker resolves
    volumes by name regardless of which container asks) with the script told to write into its
    own project/run subdirectory, mirroring codex_worker.py's own shared-mount fallback."""
    relative = str(PurePosixPath(project_id) / ".airuntime" / "preview" / run_id)
    worker_local_dir = Path(settings.generated_projects_dir) / relative
    worker_local_dir.mkdir(parents=True, exist_ok=True)

    host_path = _resolve_project_host_path(client, project_id)
    if host_path:
        host_output_dir = str(PurePosixPath(host_path) / ".airuntime" / "preview" / run_id)
        return (
            {host_output_dir: {"bind": "/output", "mode": "rw"}},
            "/output",
            worker_local_dir,
        )

    volumes = {settings.generated_projects_volume_name: {"bind": _WHOLE_VOLUME_MOUNT, "mode": "rw"}}
    in_container_dir = f"{_WHOLE_VOLUME_MOUNT}/{relative}"
    return volumes, in_container_dir, worker_local_dir


def _empty_result(fatal_error: str) -> dict:
    return {"status": "failed", "pages": [], "fatal_errors": [fatal_error], "warnings": []}


def _container_failure_details(container: Any, wait_result: Any) -> str:
    """Return a short, safe diagnostic when the runner exits without its result contract."""
    status_code = wait_result.get("StatusCode") if isinstance(wait_result, dict) else None
    try:
        raw_logs = container.logs(stdout=True, stderr=True, tail=40)
    except DockerException:
        raw_logs = b""
    if isinstance(raw_logs, bytes):
        logs = raw_logs.decode("utf-8", errors="replace")
    else:
        logs = str(raw_logs or "")
    # Runner logs contain only browser/runtime diagnostics, but keep the API error bounded.
    logs = " ".join(logs.split())[-1500:]
    suffix = f" (exit code {status_code})" if status_code is not None else ""
    return f"{suffix}: {logs}" if logs else suffix


def run_preview(
    project: Project,
    *,
    environment: dict[str, str] | None = None,
    targets: list[dict] | None = None,
    paths: list[str] | None = None,
) -> dict:
    """Returns a dict matching pipeline_models.PreviewResult's shape. Never raises - Docker/
    infra failures come back as a normal "failed" result with a fatal_errors entry, same
    fail-open posture as the rest of docker_control_actions.py's other actions.

    `targets` (preferred - [{"path": str, "viewport": {"width", "height"}}, ...]) lets a
    caller mix desktop/mobile viewports per page in one run. `paths` (back-compat - a plain
    list of strings, all checked at the desktop DEFAULT_VIEWPORT) is what agent/tools.py's
    preview_project tool still sends; both end up sanitized the same way.
    """
    if project.type == "telegram_bot":
        return _empty_result("Preview is only available for website/mixed projects")

    project_id = str(project.id)
    tag = _image_tag(project)
    safe_targets = _sanitize_targets(targets) if targets else _targets_from_paths(paths)
    run_id = uuid.uuid4().hex[:12]
    # Base budget covers one check; each additional target gets its own share of navigation/
    # scroll/screenshot time, so a 5-target preview isn't held to a 1-target timeout.
    effective_timeout = settings.preview_timeout_seconds + max(0, len(safe_targets) - 1) * 20

    try:
        client = docker.from_env()
    except DockerException as exc:
        return _empty_result(f"Could not reach Docker: {exc}")

    try:
        client.images.get(tag)
    except (ImageNotFound, DockerException):
        return _empty_result(f"Image {tag} is not built yet - run build_project first")

    volumes, in_container_output_dir, worker_local_dir = _prepare_output_mount(
        client, project_id, run_id
    )

    adapter = DockerDeploymentAdapter()
    app_container = None
    runner_container = None
    app_name = f"airuntime-preview-app-{project_id[:8]}-{uuid.uuid4().hex[:6]}"
    preview_net = _preview_network_name(project_id)

    try:
        service_net = adapter.ensure_private_network(project_id)

        try:
            client.networks.get(preview_net)
        except NotFound:
            client.networks.create(preview_net, driver="bridge", internal=True)

        app_container = client.containers.run(
            tag,
            detach=True,
            name=app_name,
            network=service_net,
            environment=environment or None,
            mem_limit=settings.deployment_memory_limit,
            nano_cpus=int(float(settings.deployment_cpu_limit) * 1_000_000_000),
            labels={
                "airuntime.project_id": project_id,
                "airuntime.managed": "true",
                "airuntime.role": "preview-app",
            },
        )
        client.networks.get(preview_net).connect(app_container.id)

        runner_container = client.containers.run(
            settings.preview_image,
            detach=True,
            name=f"airuntime-preview-runner-{project_id[:8]}-{uuid.uuid4().hex[:6]}",
            network=preview_net,
            environment={
                "PREVIEW_TARGET_HOST": app_name,
                "PREVIEW_TARGET_PORT": str(_APP_PORT),
                "PREVIEW_TARGETS": json.dumps(safe_targets),
                "PREVIEW_OUTPUT_DIR": in_container_output_dir,
            },
            volumes=volumes,
            mem_limit=settings.preview_memory_limit,
            nano_cpus=int(float(settings.preview_cpu_limit) * 1_000_000_000),
            labels={"airuntime.project_id": project_id, "airuntime.role": "preview-runner"},
        )

        try:
            wait_result = runner_container.wait(timeout=effective_timeout)
        except Exception as exc:  # noqa: BLE001 - docker-py's wait-timeout exception type
            # varies by version/transport; any failure to observe completion in time is
            # treated the same way: stop waiting, report a timeout, clean up below.
            logger.warning(
                "Preview run %s timed out or failed to observe completion: %s", run_id, exc
            )
            return _empty_result(f"Preview timed out after {effective_timeout}s")

        result_path = worker_local_dir / "result.json"
        if not result_path.exists():
            details = _container_failure_details(runner_container, wait_result)
            return _empty_result(f"Preview container exited without producing a result{details}")
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            # run_preview.py only knows the bare filename it wrote (page_x.png) - rewrite to a
            # full workspace-relative path (readable via workspace.root / screenshot_ref from
            # the backend process, which shares this same volume) now that the caller needs to
            # actually locate the file, not just log its name.
            for page in data.get("pages") or []:
                ref = page.get("screenshot_ref")
                if ref:
                    page["screenshot_ref"] = f".airuntime/preview/{run_id}/{ref}"
            return data
        except (OSError, json.JSONDecodeError) as exc:
            return _empty_result(f"Could not read preview result: {exc}")
    except DockerException as exc:
        return _empty_result(f"Docker error during preview: {exc}")
    finally:
        for container in (runner_container, app_container):
            if container is None:
                continue
            try:
                container.remove(force=True)
            except DockerException:
                pass
