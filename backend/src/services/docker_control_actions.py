"""Execute docker-control actions (same logic the worker uses for Redis RPCs).

Kept separate so the worker can run these inline while already handling a deploy —
otherwise repair agents that call build_project / logs would deadlock waiting for the
same single worker to pop its own control job.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from docker.errors import APIError, NotFound

from src.db.models.project import Project
from src.db.session import SessionLocal
from src.services.artifacts import _telegram_token, try_build_project_image
from src.services.deployment.docker_adapter import DockerDeploymentAdapter, app_container_name
from src.services.preview_runner import _preview_network_name, run_preview
from src.services.project_services import (
    build_connection_env,
    network_name,
    project_volumes_root,
    teardown_service_containers,
)

logger = logging.getLogger(__name__)


def _removal_already_in_progress(exc: APIError) -> bool:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    explanation = str(getattr(exc, "explanation", "") or exc).lower()
    return status_code == 409 and "removal" in explanation and "progress" in explanation


def _remove_owned_containers(client: Any, label_filter: dict[str, str]) -> None:
    """Converge when Docker reports an asynchronous removal already in progress."""
    deadline = time.monotonic() + 5
    while True:
        containers = client.containers.list(all=True, filters=label_filter)
        if not containers:
            return
        for container in containers:
            try:
                if container.status == "running":
                    container.stop(timeout=10)
            except Exception:  # noqa: BLE001 - force remove below is authoritative
                pass
            try:
                container.remove(force=True)
            except NotFound:
                pass
            except APIError as exc:
                if not _removal_already_in_progress(exc):
                    raise
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def _run_runtime_health_check(project_id: str) -> dict[str, Any]:
    """Real runtime verification, closing the gap left by `verify_still_running`'s flat 5s
    sleep+status-check (docker_adapter.py) and deployment_check.py's pure log-regex scanning -
    neither of those does a restart-loop check or confirms the app is actually listening.

    `port_80_listening` is read from the container's own `/proc/net/tcp` via `exec_run` (state
    `0A` = TCP_LISTEN, local port `:0050` hex = 80 decimal) rather than an HTTP client
    (curl/wget) - deliberately, since a generated project's base image is never guaranteed to
    have either installed, but every Linux container has a `/proc` filesystem and a shell.
    This confirms *something* is listening on the platform's documented app port, not that it
    returns a healthy response - a real HTTP probe is a reasonable future enhancement, not
    something this change claims to already do.
    """
    adapter = DockerDeploymentAdapter()
    exact = app_container_name(project_id)
    matches = [
        c
        for c in adapter.client.containers.list(all=True, filters={"name": exact})
        if c.name == exact
    ]
    if not matches:
        return {"ok": False, "container_found": False, "reason": "no app container found"}

    container = matches[0]
    container.reload()
    state = container.attrs.get("State", {})
    restart_count = int(container.attrs.get("RestartCount", 0) or 0)
    restarting = bool(state.get("Restarting"))
    status = container.status
    # A single-digit restart count in isolation is normal (a slow-starting app crashing once
    # before its DB dependency is ready); >=3 within the container's current lifetime is the
    # conventional "probably crash-looping" threshold this platform uses.
    restart_loop_suspected = restart_count >= 3

    port_80_listening = False
    if status == "running":
        try:
            exec_result = container.exec_run(["sh", "-c", "cat /proc/net/tcp 2>/dev/null"])
            output = (exec_result.output or b"").decode("utf-8", errors="replace")
            for line in output.splitlines()[1:]:
                columns = line.split()
                if len(columns) >= 4 and columns[1].endswith(":0050") and columns[3] == "0A":
                    port_80_listening = True
                    break
        except Exception:  # noqa: BLE001 - a failed probe just means "not confirmed", not a crash
            logger.warning(
                "runtime_health_check: port probe failed for %s", project_id, exc_info=True
            )

    return {
        "ok": status == "running" and not restart_loop_suspected and port_80_listening,
        "container_found": True,
        "container_status": status,
        "restart_count": restart_count,
        "restarting": restarting,
        "restart_loop_suspected": restart_loop_suspected,
        "port_80_listening": port_80_listening,
    }


def _cancel_codex_run(correlation_id: str) -> dict[str, Any]:
    """Stop every in-flight attempt belonging to one orchestration task."""
    client = DockerDeploymentAdapter().client
    matches = []
    try:
        matches = client.containers.list(
            all=True,
            filters={"label": f"airuntime.codex_correlation_id={correlation_id}"},
        )
    except Exception:  # noqa: BLE001 - keep compatibility with a container from before labels
        matches = []
    if not matches:
        try:
            matches = [client.containers.get(f"airuntime-codex-{correlation_id}")]
        except Exception:  # noqa: BLE001 - already finished/removed is a successful no-op
            return {"ok": True, "found": False}
    for container in matches:
        try:
            container.stop(timeout=5)
        except Exception:  # noqa: BLE001 - a concurrent natural exit is fine
            pass
    return {"ok": True, "found": bool(matches)}


def _cleanup_project_resources(project_id: str) -> dict[str, Any]:
    """Delete and then verify every Docker/host-volume resource owned by a project."""
    from pathlib import Path

    adapter = DockerDeploymentAdapter()
    client = adapter.client

    adapter.stop_project(project_id)
    # Always run sidecar teardown, even if a DB bookkeeping row was already lost: ownership
    # labels and the deterministic volume root are enough to find the actual resources.
    teardown_service_containers(client, project_id, remove_volumes=True)

    # Codex/preview/app containers all carry the same ownership label. A project deleted while
    # generation is still winding down must not leave the task container behind.
    label_filter = {"label": f"airuntime.project_id={project_id}"}
    _remove_owned_containers(client, label_filter)

    adapter.remove_project_images(project_id)

    for name in (network_name(project_id), _preview_network_name(project_id)):
        try:
            client.networks.get(name).remove()
        except Exception:  # noqa: BLE001 - verified below
            pass

    remaining_containers = [c.name for c in client.containers.list(all=True, filters=label_filter)]
    id_fragment = project_id[:12]
    remaining_images: list[str] = []
    for image in client.images.list():
        tags = getattr(image, "tags", None) or []
        labels = ((getattr(image, "attrs", None) or {}).get("Config") or {}).get("Labels") or {}
        if labels.get("airuntime.project_id") == project_id or any(
            id_fragment in tag
            and (tag.startswith("airuntime-generated-") or tag.startswith("airuntime-scratch-"))
            for tag in tags
        ):
            remaining_images.extend(tags or [image.id])

    remaining_networks: list[str] = []
    for name in (network_name(project_id), _preview_network_name(project_id)):
        try:
            client.networks.get(name)
            remaining_networks.append(name)
        except Exception:
            pass

    volume_root = Path(project_volumes_root(project_id))
    remaining = {
        "containers": remaining_containers,
        "images": remaining_images,
        "networks": remaining_networks,
        "volume_root": str(volume_root) if volume_root.exists() else None,
    }
    ok = not any(remaining.values())
    return {"ok": ok, "remaining": remaining}


def run_control_action(
    *, action: str | None, project_id: str | None, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run one control action and return a result dict (ok=True/False, …)."""
    extra = extra or {}
    try:
        if action == "cancel_codex_run":
            correlation_id = extra.get("correlation_id")
            if not correlation_id:
                return {"ok": False, "error": "missing correlation_id"}
            return _cancel_codex_run(str(correlation_id))
        if action == "stop" and project_id:
            # App container only - sidecars stay up for a fast restart.
            DockerDeploymentAdapter().stop_project(str(project_id))
            return {"ok": True}
        if action == "cleanup" and project_id:
            return _cleanup_project_resources(str(project_id))
        if action == "logs":
            container_id = extra.get("container_id")
            tail = int(extra.get("tail", 400) or 400)
            logs = DockerDeploymentAdapter().fetch_container_logs(str(container_id), tail=tail)
            return {"ok": True, "logs": logs}
        if action == "runtime_health_check" and project_id:
            return _run_runtime_health_check(str(project_id))
        if action == "build_check" and project_id:
            db = SessionLocal()
            try:
                project = db.get(Project, project_id)
                if not project:
                    return {"ok": False, "log": "Project not found"}
                return try_build_project_image(project)
            finally:
                db.close()
        if action == "preview" and project_id:
            db = SessionLocal()
            try:
                project = db.get(Project, project_id)
                if not project:
                    return {
                        "status": "failed",
                        "pages": [],
                        "fatal_errors": ["Project not found"],
                        "warnings": [],
                    }
                # Best-effort real-looking env (DB/cache connection strings, bot token if
                # already configured) so the previewed container renders like production
                # would. Never raises on a missing token - a container that can't boot
                # without it will simply surface as a failed page load, which is honest
                # signal on its own.
                environment = build_connection_env(db, project)
                if project.type in ("telegram_bot", "mixed"):
                    token = _telegram_token(db, project)
                    if token:
                        environment["TELEGRAM_BOT_TOKEN"] = token
                targets = extra.get("targets")
                paths = extra.get("paths")
                return run_preview(
                    project,
                    environment=environment,
                    targets=targets if isinstance(targets, list) else None,
                    paths=paths if isinstance(paths, list) else None,
                )
            finally:
                db.close()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - always report back
        logger.exception("Control action %r failed for project %s", action, project_id)
        return {"ok": False, "error": str(exc)}
