"""Docker-side execution of a Codex run.

Only ever imported from the `worker` process - the one place in this platform allowed to touch
the Docker socket (see docker_control_queue.py). Runs a fresh, single-purpose Codex container per
turn (`docker run`, not `docker exec` into a shared always-on one) and yields its `codex exec
--json` output, one parsed event at a time, as it's produced.

Isolation model (replaces the old "one long-lived `codex` container, shared project volume,
raw host socket" setup - see git history for that version):
- Filesystem: each run's container gets ONLY the current project's own subtree bind-mounted (at
  /workspace), resolved from the shared named volume's real host path via _resolve_project_mount.
  A turn for project A can no longer `cd ../other-project` - that path doesn't exist in its mount
  namespace. If the volume's host path can't be resolved (older Docker, renamed volume, ...) this
  falls back to the old shared-mount behavior rather than breaking every turn outright - see
  _resolve_project_mount's docstring. That fallback is intentionally loud (printed, not silent).
- Docker access: if settings.codex_docker_host is configured (a docker-socket-proxy address),
  the container gets DOCKER_HOST pointed at the proxy instead of the raw host socket, so its own
  `docker build`/`docker run` calls (Codex still needs these - see prompt.py's bridge
  instructions) go through a narrowed API surface instead of the unscoped daemon. Unset by
  default so this ships non-breaking; see docker-compose.yml's docker-socket-proxy service and
  docs/architecture.md for the opt-in deploy step.

Two callers, matching the two ways docker_control_queue.py already lets worker-side Docker
actions run:
- execute_codex_run(): out-of-process case. The backend API process (which never touches
  Docker) queued a job; a background thread in deployment_worker.py calls this to relay events
  into Redis for agent/codex_runtime.py's async consumer.
- iter_codex_events(): in-process case, called directly by agent/codex_runtime.py when a repair
  flow is already running inside the worker (docker_control_queue.in_worker_inline_docker()) -
  going through Redis there would have the single worker process waiting on a queue only it
  services, which is the same self-deadlock worker_inline_docker() already exists to avoid for
  the plain stop/cleanup/logs/build_check actions.
"""

from __future__ import annotations

import json
import logging
import shlex
import uuid
from collections.abc import Iterator
from pathlib import PurePosixPath

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from redis import Redis

from src.core.config import settings
from src.services.system_settings import resolve_platform_api_key

logger = logging.getLogger(__name__)

_EVENTS_KEY_PREFIX = "codex:events:"
_DONE_MARKER = "__codex_run_done__"
_EVENTS_TTL_SECONDS = 600

_WORKSPACE_MOUNT = "/workspace"
# Sentinel exit code for "the login step itself failed" vs. any other container exit - chosen to
# not collide with common shell/exec codes (1, 2, 126, 127) so it's unambiguous in logs.
_LOGIN_FAILED_EXIT = 17


def _redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
    )


def _events_key(run_id: str) -> str:
    return f"{_EVENTS_KEY_PREFIX}{run_id}"


def _resolve_workspace_mount(client: docker.DockerClient, workspace_cwd: str) -> str | None:
    """Resolve the exact acquired workspace (shared checkout or isolated git worktree).

    Uses the volume's own `Mountpoint` (a long-standing, stable part of the Docker volume
    inspect API - not the newer, version-gated volume-subpath mount feature, which would need
    Docker Engine 25+ and wasn't safe to assume here) rather than switching the shared volume
    itself to a host bind-mount, which would've meant a manual data-migration step for anyone
    upgrading. Returns None (triggering the shared-mount fallback in the caller) if the volume
    name doesn't match what's actually on this host - e.g. a non-default COMPOSE_PROJECT_NAME -
    rather than raising, since a degraded-but-working turn beats a hard-broken one.
    """
    try:
        volume = client.volumes.get(settings.generated_projects_volume_name)
        mountpoint = volume.attrs.get("Mountpoint")
        if not mountpoint:
            return None
        volume_root = PurePosixPath(settings.generated_projects_dir)
        relative = PurePosixPath(workspace_cwd).relative_to(volume_root)
        if not relative.parts:
            return None
        # PurePosixPath, not Path: this always targets the Linux Docker daemon, including from
        # Docker Desktop on a Windows development host.
        return str(PurePosixPath(mountpoint) / relative)
    except (NotFound, APIError, DockerException, KeyError, ValueError) as exc:
        logger.warning(
            "Could not resolve workspace %r in volume %r (%s) - falling back to full "
            "shared-volume access for this run. Check GENERATED_PROJECTS_VOLUME_NAME / "
            "`docker volume ls` on this host.",
            workspace_cwd,
            settings.generated_projects_volume_name,
            exc,
        )
        return None


def _remove_failed_start_container(client: docker.DockerClient, container_name: str) -> None:
    """Remove the container Docker may leave behind when ``containers.run`` fails to start it."""
    try:
        client.containers.get(container_name).remove(force=True)
    except DockerException:
        pass


_UNSET = object()


def _job_api_key(job: dict) -> str | None:
    raw = job.get("api_key")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return resolve_platform_api_key("openai") or settings.openai_api_key


def _job_base_url(job: dict) -> str | None:
    """Per-job proxy URL. Missing key = platform default. JSON null / empty = official OpenAI."""
    if "openai_base_url" not in job:
        return (settings.openai_base_url or "").strip().rstrip("/") or None
    raw = job.get("openai_base_url")
    if not isinstance(raw, str):
        return None
    return raw.strip().rstrip("/") or None


def _codex_model_id(model: str, base_url: str | None | object = _UNSET) -> str:
    """RouterAI (and other OpenAI-compatible proxies) identify OpenAI models as ``openai/<id>``."""
    effective = settings.openai_base_url if base_url is _UNSET else base_url
    if not (effective or "").strip():
        return model
    if "/" in model:
        return model
    return f"openai/{model}"


def _codex_config_toml(base_url: str | None | object = _UNSET) -> str | None:
    raw = settings.openai_base_url if base_url is _UNSET else base_url
    base = (raw or "").strip().rstrip("/")
    if not base:
        return None
    return (
        'model_provider = "routerai"\n'
        "\n"
        "[model_providers.routerai]\n"
        'name = "RouterAI"\n'
        f'base_url = "{base}"\n'
        'env_key = "OPENAI_API_KEY"\n'
        'wire_api = "responses"\n'
    )


def _build_argv(job: dict, *, model: str, remapped_cwd: str | None) -> list[str]:
    # NOTE: flag names (--skip-git-repo-check, --dangerously-bypass-approvals-and-sandbox,
    # --cd, --image) match the Codex CLI docs at the time this was written. `exec` has no TTY
    # to prompt for approval, so it must run with its own sandbox/approval gate fully open - the
    # per-run Docker container (this project's own subtree + Docker access only, never the host
    # filesystem) is the sandbox here, not Codex's internal one. Re-check `codex exec --help` in
    # the built image after a CLI upgrade if runs start failing to parse args.
    argv = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        model,
        "-c",
        # Per-job so a restricted plan can run a cheaper effort than the global default.
        f'model_reasoning_effort="{job.get("reasoning_effort") or settings.codex_reasoning_effort}"',
    ]
    if remapped_cwd:
        argv += ["--cd", remapped_cwd]
    for image_path in job.get("image_paths") or []:
        argv += ["--image", str(image_path)]
    argv.append(str(job.get("prompt") or ""))
    return argv


def _remap_under_workspace(path_str: str, old_root: str) -> str:
    """--image paths arrive as absolute paths under the old shared-tree root (see
    file_context.py / codex_runtime.py's _write_temp_images, which writes under the project's own
    directory) - rewrite them to sit under this run's /workspace mount instead."""
    try:
        relative = PurePosixPath(path_str).relative_to(old_root)
    except ValueError:
        return path_str
    return str(PurePosixPath(_WORKSPACE_MOUNT) / relative)


def _login_and_exec_command(
    argv: list[str], *, base_url: str | None | object = _UNSET
) -> list[str]:
    """`codex exec` does not read OPENAI_API_KEY itself - confirmed against a real run, which
    401'd until this was added. Auth is a separate step that persists to ~/.codex/auth.json
    inside the container (`codex login --with-api-key`, fed the key over stdin); once logged in,
    `codex exec` picks it up with no per-call key needed. Chained into one shell command (rather
    than a separate exec_create call, as the old shared-container version did) because this run's
    container no longer stays alive independently for a follow-up exec - login-then-run is now
    the container's one and only job. Re-logs in on every run (cheap, local, no network
    round-trip) rather than trying to persist/cache auth across runs - simplest way to stay
    correct if the admin rotates the key.

    When a proxy ``base_url`` is set the key is a third-party token (RouterAI), not an OpenAI
    key: ``codex login`` talks to OpenAI and would 401. Write a user-level ``config.toml``
    instead and skip login; Codex sends the env key as Bearer to the proxy.
    """
    exec_cmd = shlex.join(argv)
    config = _codex_config_toml(base_url)
    if config is not None:
        script = (
            "mkdir -p /root/.codex && cat > /root/.codex/config.toml << 'AIRUNTIME_CODEX_EOF'\n"
            f"{config}"
            "AIRUNTIME_CODEX_EOF\n"
            f"exec {exec_cmd}"
        )
        return ["sh", "-c", script]
    login = 'printf "%s" "$OPENAI_API_KEY" | codex login --with-api-key'
    script = (
        f"if ! {login} >/tmp/codex-login.log 2>&1; then "
        f"cat /tmp/codex-login.log >&2; exit {_LOGIN_FAILED_EXIT}; "
        f"fi; exec {exec_cmd}"
    )
    return ["sh", "-c", script]


def _container_env(
    api_key: str, project_id: str | None, *, allow_docker: bool = True
) -> dict[str, str]:
    env = {"OPENAI_API_KEY": api_key}
    if project_id:
        env["AIRUNTIME_PROJECT_ID"] = project_id
        env["AIRUNTIME_SCRATCH_IMAGE_PREFIX"] = f"airuntime-scratch-{project_id[:12]}"
    if allow_docker and settings.codex_docker_host:
        env["DOCKER_HOST"] = settings.codex_docker_host
        # BuildKit's session/upgrade endpoints are intentionally not exposed by the narrowed
        # Docker socket proxy. The classic builder uses the allowed build API and avoids
        # repeated "no active session / context deadline exceeded" failures.
        env["DOCKER_BUILDKIT"] = "0"
    return env


def _container_volumes(
    project_host_path: str | None, *, allow_docker: bool = True
) -> dict[str, dict[str, str]]:
    volumes: dict[str, dict[str, str]] = {}
    if project_host_path:
        volumes[project_host_path] = {"bind": _WORKSPACE_MOUNT, "mode": "rw"}
    if allow_docker and not settings.codex_docker_host:
        # No proxy configured - fall back to the raw host socket (the pre-isolation trust level)
        # so Codex can still build/run project images. Deliberately opt-in-to-narrow rather than
        # opt-in-to-broad: safer default is "at least as isolated as before", not "silently open".
        volumes["/var/run/docker.sock"] = {"bind": "/var/run/docker.sock", "mode": "rw"}
    return volumes


def iter_codex_events(job: dict) -> Iterator[dict]:
    """Blocking generator: runs a fresh per-turn Codex container and yields each parsed JSON
    event as it's produced. Safe to call directly in-process (see module docstring) - it never
    touches Redis itself, so it has no opinion on whether its caller is the worker's main loop or
    an inline repair flow."""
    container = None
    client: docker.DockerClient | None = None
    try:
        api_key = _job_api_key(job)
        if not api_key:
            yield {"type": "infra_error", "message": "OpenAI API key is not configured"}
            return
        base_url = _job_base_url(job)

        old_cwd = job.get("cwd")
        logger.info(
            "Codex container starting image=%s network=%s model=%s cwd=%s",
            settings.codex_image,
            settings.codex_network,
            job.get("model") or settings.default_model_openai,
            old_cwd,
        )
        client = docker.from_env()
        try:
            client.images.get(settings.codex_image)
        except ImageNotFound:
            yield {
                "type": "infra_error",
                "message": (
                    f"Codex image {settings.codex_image!r} is not built - run "
                    "`docker compose build codex`"
                ),
            }
            return

        project_id = str(job.get("project_id") or "") or None
        workspace_host_path = _resolve_workspace_mount(client, str(old_cwd)) if old_cwd else None

        allow_docker = bool(job.get("allow_docker", True))
        volumes = _container_volumes(workspace_host_path, allow_docker=allow_docker)
        if old_cwd and workspace_host_path:
            # Scoped mount resolved - remap --cd and any --image paths onto it.
            effective_cwd = _WORKSPACE_MOUNT
            effective_images = [
                _remap_under_workspace(p, old_cwd) for p in (job.get("image_paths") or [])
            ]
        elif old_cwd:
            # Could not resolve a scoped mount (see _resolve_project_mount) - fall back to the
            # whole shared tree at its original absolute path, exactly as before isolation
            # existed, so this turn still succeeds instead of hard-failing.
            #
            # Mount by the *named volume*, not by settings.generated_projects_dir's path string:
            # this dict is handed to client.containers.run() on a client that reached the host
            # daemon over the mounted socket (see module docstring), so any plain path here is
            # resolved against the HOST filesystem, not this worker process's own mount
            # namespace - generated_projects_dir ("/data/airruntime-projects") is only where the
            # named volume happens to be mounted *inside this container*, not a real host path.
            # Docker silently creates a missing bind-mount host directory rather than erroring,
            # so this previously ran real Codex turns against an empty, throwaway directory with
            # no error anywhere - the agent would report files written/tests run truthfully from
            # its own point of view, none of it ever reaching the actual project. A volume name
            # is resolved by the daemon itself, so it works no matter which container asks.
            effective_cwd = old_cwd
            effective_images = list(job.get("image_paths") or [])
            volumes[settings.generated_projects_volume_name] = {
                "bind": settings.generated_projects_dir,
                "mode": "rw",
            }
        else:
            # codex_simple_complete: no project context, nothing to mount.
            effective_cwd = None
            effective_images = []

        model = _codex_model_id(job.get("model") or settings.default_model_openai, base_url)
        argv = _build_argv(
            {**job, "image_paths": effective_images}, model=model, remapped_cwd=effective_cwd
        )
        command = _login_and_exec_command(argv, base_url=base_url)

        container_name = f"airuntime-codex-{job.get('job_id') or uuid.uuid4().hex[:12]}"
        try:
            container = client.containers.run(
                settings.codex_image,
                command=command,
                detach=True,
                # Named after the job's own job_id (== the orchestration AgentTask id, when
                # this run came from executors.py - see codex_runtime.py's correlation_id) so
                # docker_control_actions.cancel_codex_run can compute this exact name from a
                # task id alone and issue a real `docker stop`, with no separate name registry.
                # codex_simple_complete's one-shot planning/review calls and non-orchestrated
                # turns have no meaningful job_id, so they still fall back to a fresh random
                # name (unchanged behavior - nothing can or needs to cancel those).
                name=container_name,
                labels={
                    "airuntime.managed": "true",
                    "airuntime.role": "codex",
                    **({"airuntime.project_id": project_id} if project_id else {}),
                    **(
                        {"airuntime.codex_correlation_id": str(job["correlation_id"])}
                        if job.get("correlation_id")
                        else {}
                    ),
                },
                environment=_container_env(api_key, project_id, allow_docker=allow_docker),
                volumes=volumes,
                network=settings.codex_network,
                mem_limit=settings.codex_memory_limit,
                nano_cpus=int(float(settings.codex_cpu_limit) * 1_000_000_000),
                working_dir=effective_cwd,
            )
            logger.info("Codex container started name=%s id=%s", container_name, container.id[:12])
        except DockerException as exc:
            # Docker creates the container before attaching its network. A missing network (or
            # another start-stage failure) can therefore raise from `run()` while leaving an
            # unreachable container in `Created`; no object was returned for the outer finally.
            _remove_failed_start_container(client, container_name)
            yield {"type": "infra_error", "message": f"Could not start Codex container: {exc}"}
            return

        buffer = b""
        for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=False):
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                yield payload

        tail = buffer.decode("utf-8", errors="replace").strip()
        if tail:
            try:
                yield json.loads(tail)
            except json.JSONDecodeError:
                pass

        result = container.wait()
        exit_code = result.get("StatusCode")
        if exit_code == _LOGIN_FAILED_EXIT:
            detail = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            yield {"type": "infra_error", "message": f"Codex login failed: {detail.strip()[:500]}"}
        elif exit_code not in (0, None):
            yield {"type": "infra_error", "message": f"codex exec exited with code {exit_code}"}
    except (DockerException, APIError) as exc:
        logger.warning("Codex run: Docker error: %s", exc)
        yield {"type": "infra_error", "message": f"Docker error: {exc}"}
    except Exception as exc:  # noqa: BLE001 - report rather than crash the caller
        logger.exception("Codex run failed unexpectedly")
        yield {"type": "infra_error", "message": f"Codex run failed: {exc}"}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                pass


def execute_codex_run(job: dict) -> None:
    """Out-of-process entry point: relay iter_codex_events() into Redis so
    agent/codex_runtime.py's async stream_codex_events() can consume it live. Used when the
    backend API process (no Docker access) initiated the turn - see
    deployment_worker.py's _spawn_codex_run."""
    run_id = job["job_id"]
    r = _redis()
    key = _events_key(run_id)
    try:
        for payload in iter_codex_events(job):
            r.rpush(key, json.dumps(payload))
            r.expire(key, _EVENTS_TTL_SECONDS)
    finally:
        try:
            r.rpush(key, _DONE_MARKER)
            r.expire(key, _EVENTS_TTL_SECONDS)
        except Exception:  # noqa: BLE001 - nothing more we can do if Redis itself is down
            pass
