"""Provisioning for real sidecar services attached to a generated project.

The agent can request any Docker image via the request_service tool (src/services/agent/tools.py)
- kind is a free-form label (e.g. "rabbitmq", "queue", "search"), and image/env/data_path can be
given explicitly for anything not in the small convenience-preset list below. Regardless of
image, every sidecar stays hardened the same way: no published host ports (reachable only from
the project's own app container over a private per-project network), project-scoped persistent
data under `settings.deployment_volumes_dir` (host bind mounts that survive image rebuild and
container recreate), and the same resource caps as any other sidecar - so an unusual image can
misbehave within its own box, but can't reach the host filesystem outside its volume, the public
internet-facing side, or exceed its resource budget.

Bookkeeping (this module's DB-only functions) happens in the API request path; the functions
that actually talk to Docker take an already-constructed client and are only ever called from
the worker (src/workers/deployment_worker.py), which is the one process with a Docker socket -
see src/services/docker_control_queue.py for why the API process itself never touches Docker.
"""

from __future__ import annotations

import json
import re
import secrets as secrets_module
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.project import Project
from src.db.models.project_service import ProjectService
from src.services.secrets import decrypt_secret, encrypt_secret

# Convenience presets: if the agent uses one of these kinds without specifying image/env/
# data_path itself, the platform fills in a sensible default image, generates credentials, and
# injects a ready-made connection-string env var. Any other kind works too - it just requires
# the agent to specify `image` (and, if it wants config beyond the image's own defaults, `env`).
_PRESETS: dict[str, dict[str, str]] = {
    "postgres": {"image": "postgres:16-alpine", "data_path": "/var/lib/postgresql/data"},
    "redis": {"image": "redis:7-alpine", "data_path": "/data"},
    "mysql": {"image": "mysql:8", "data_path": "/var/lib/mysql"},
    "mongo": {"image": "mongo:7", "data_path": "/data/db"},
    "rabbitmq": {"image": "rabbitmq:3-management", "data_path": "/var/lib/rabbitmq"},
}

KNOWN_PRESET_KINDS = frozenset(_PRESETS)

_CREDENTIAL_KEY_MARKERS = (
    "PASSWORD",
    "USERNAME",
    "USER",
    "HOST",
    "PORT",
    "DATABASE",
    "_DB",
    "URL",
    "URI",
    "CONNECTION",
)

# Generic DB connection names the platform injects after request_service(postgres/mysql/mongo).
# "POSTGRES" is not a substring of DATABASE_URL, so the kind-in-key check misses these.
_GENERIC_DB_SECRET_KEYS = frozenset(
    {
        "DATABASE_URL",
        "DATABASE_URI",
        "DB_URL",
        "DB_URI",
        "DB_HOST",
        "DB_PORT",
        "DB_USER",
        "DB_USERNAME",
        "DB_PASSWORD",
        "DB_NAME",
    }
)

_PLATFORM_MANAGED_SECRET_KEYS = _GENERIC_DB_SECRET_KEYS | frozenset(
    {
        "POSTGRES_URL",
        "POSTGRES_URI",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "REDIS_URL",
        "REDIS_URI",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "MYSQL_URL",
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_DATABASE",
        "MONGO_URL",
        "MONGO_URI",
        "MONGODB_URI",
        "MONGO_HOST",
        "MONGO_PASSWORD",
        "RABBITMQ_URL",
        "RABBITMQ_URI",
        "RABBITMQ_HOST",
        "RABBITMQ_DEFAULT_USER",
        "RABBITMQ_DEFAULT_PASS",
        "RABBITMQ_PASSWORD",
    }
)


def is_likely_service_credential_key(key: str, service_kinds: set[str]) -> bool:
    """True if `key` looks like a credential/connection-detail for a service kind the project
    already has (e.g. "POSTGRES_PASSWORD" when a "postgres" ProjectService exists) - a strong
    signal the agent asked the user for something request_service already generated and wired
    up automatically, and the secret request should be suppressed rather than shown to the user.
    """
    upper_key = key.upper()
    db_kinds = service_kinds & {"postgres", "mysql", "mongo"}
    if db_kinds and upper_key in _GENERIC_DB_SECRET_KEYS:
        return True
    for kind in service_kinds:
        kind_upper = re.sub(r"[^A-Z0-9]+", "_", kind.upper()).strip("_")
        if kind_upper and kind_upper in upper_key:
            if any(marker in upper_key for marker in _CREDENTIAL_KEY_MARKERS):
                return True
    return False


def is_platform_managed_secret_key(key: str) -> bool:
    """True for credentials the platform generates via request_service — never ask the user.

    Unlike is_likely_service_credential_key, this does not require a ProjectService row:
    agents often request DATABASE_URL / POSTGRES_PASSWORD *before* calling request_service,
    which parked runs on empty secret placeholders ("просит несуществующие секреты").
    """
    upper_key = key.strip().upper()
    if upper_key in _PLATFORM_MANAGED_SECRET_KEYS:
        return True
    return is_likely_service_credential_key(upper_key, set(KNOWN_PRESET_KINDS))


def user_facing_secret_keys(keys: list[str], *, service_kinds: set[str] | None = None) -> list[str]:
    kinds = service_kinds or set()
    return [
        key
        for key in keys
        if not (is_platform_managed_secret_key(key) or is_likely_service_credential_key(key, kinds))
    ]


def is_known_preset(kind: str) -> bool:
    return kind in _PRESETS


class ProjectServiceError(RuntimeError):
    pass


def normalize_service_kind(kind: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", kind.strip().lower()).strip("-")
    return slug or "service"


def network_name(project_id: str) -> str:
    return f"airuntime-svc-{project_id[:8]}"


def container_name_for(project_id: str, kind: str) -> str:
    return f"airuntime-{project_id[:8]}-{normalize_service_kind(kind)}"


def volume_name_for(project_id: str, kind: str) -> str:
    """Stable logical volume id stored on ProjectService (legacy named-volume label key)."""
    return f"{container_name_for(project_id, kind)}-data"


def host_volume_path(project_id: str, kind: str) -> str:
    """Absolute host path for a service's durable data directory.

    Layout: `{deployment_volumes_dir}/{project_id}/{kind}` — same path on every redeploy so
    Postgres/Redis keep their files across image rebuilds and container recreate.
    """
    base = settings.deployment_volumes_dir.rstrip("/\\")
    return str(Path(base) / str(project_id) / normalize_service_kind(kind))


def project_volumes_root(project_id: str) -> str:
    base = settings.deployment_volumes_dir.rstrip("/\\")
    return str(Path(base) / str(project_id))


def _ensure_host_volume_dir(host_path: str) -> None:
    """Create the bind-mount directory when the worker can see the host path.

    In production the worker bind-mounts `deployment_volumes_dir` from the host at the same
    path so mkdir works. If the path is not visible (API-only process, odd test env), Docker
    Engine still creates the host directory when the container starts on Linux.
    """
    try:
        Path(host_path).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _volume_binds(row: ProjectService) -> dict[str, dict[str, str]] | None:
    if not row.data_path:
        return None
    host_path = host_volume_path(str(row.project_id), row.kind)
    _ensure_host_volume_dir(host_path)
    return {host_path: {"bind": row.data_path, "mode": "rw"}}


def _service_labels(project_id: str) -> dict[str, str]:
    # Distinct from the app container's labels (docker_adapter.py) via "airuntime.role" -
    # teardown_service_containers must never touch the app container, which shares
    # "airuntime.project_id" but not "airuntime.role=service".
    return {
        "airuntime.project_id": project_id,
        "airuntime.managed": "true",
        "airuntime.role": "service",
    }


def _generate_preset_credentials(kind: str) -> dict[str, str]:
    token = secrets_module.token_urlsafe(24)
    if kind == "postgres":
        return {"user": "app", "password": token, "database": "app"}
    if kind == "redis":
        return {"password": token}
    if kind == "mysql":
        return {
            "user": "app",
            "password": token,
            "database": "app",
            "root_password": secrets_module.token_urlsafe(24),
        }
    if kind == "mongo":
        return {"user": "app", "password": token, "database": "app"}
    if kind == "rabbitmq":
        return {"user": "app", "password": token}
    return {}


def _preset_container_env(kind: str, creds: dict[str, str]) -> dict[str, str]:
    if kind == "postgres":
        return {
            "POSTGRES_USER": creds["user"],
            "POSTGRES_PASSWORD": creds["password"],
            "POSTGRES_DB": creds["database"],
        }
    if kind == "mysql":
        return {
            "MYSQL_USER": creds["user"],
            "MYSQL_PASSWORD": creds["password"],
            "MYSQL_DATABASE": creds["database"],
            "MYSQL_ROOT_PASSWORD": creds["root_password"],
        }
    if kind == "mongo":
        return {
            "MONGO_INITDB_ROOT_USERNAME": creds["user"],
            "MONGO_INITDB_ROOT_PASSWORD": creds["password"],
            "MONGO_INITDB_DATABASE": creds["database"],
        }
    if kind == "rabbitmq":
        return {"RABBITMQ_DEFAULT_USER": creds["user"], "RABBITMQ_DEFAULT_PASS": creds["password"]}
    return {}


def _preset_command(kind: str, creds: dict[str, str]) -> list[str] | None:
    if kind == "redis":
        return ["redis-server", "--requirepass", creds["password"]]
    return None


def _preset_connection_env(kind: str, creds: dict[str, str], host: str) -> dict[str, str]:
    if kind == "postgres":
        user = quote_plus(creds["user"])
        password = quote_plus(creds["password"])
        database = quote_plus(creds["database"])
        return {"DATABASE_URL": f"postgresql://{user}:{password}@{host}:5432/{database}"}
    if kind == "redis":
        password = quote_plus(creds["password"])
        return {"REDIS_URL": f"redis://:{password}@{host}:6379/0"}
    if kind == "mysql":
        user = quote_plus(creds["user"])
        password = quote_plus(creds["password"])
        database = quote_plus(creds["database"])
        return {"DATABASE_URL": f"mysql://{user}:{password}@{host}:3306/{database}"}
    if kind == "mongo":
        user = quote_plus(creds["user"])
        password = quote_plus(creds["password"])
        database = quote_plus(creds["database"])
        return {
            "MONGO_URL": f"mongodb://{user}:{password}@{host}:27017/{database}?authSource=admin"
        }
    if kind == "rabbitmq":
        user = quote_plus(creds["user"])
        password = quote_plus(creds["password"])
        return {"RABBITMQ_URL": f"amqp://{user}:{password}@{host}:5672/"}
    return {}


def ensure_service_request(
    db: Session,
    project: Project,
    kind: str,
    reason: str = "",
    *,
    image: str | None = None,
    env: dict[str, str] | None = None,
    data_path: str | None = None,
) -> tuple[ProjectService, bool]:
    """Create a ProjectService row for this (project, kind) if one doesn't already exist.

    DB-only - no Docker calls. Returns (row, created). Safe to call repeatedly (e.g. once per
    agent turn) - never overwrites an existing row's image/env/credentials, only backfills a
    missing reason.
    """
    kind = normalize_service_kind(kind)

    existing = (
        db.query(ProjectService)
        .filter(ProjectService.project_id == project.id, ProjectService.kind == kind)
        .first()
    )
    if existing:
        if reason and not existing.reason:
            existing.reason = reason
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing, False

    current_count = db.query(ProjectService).filter(ProjectService.project_id == project.id).count()
    if current_count >= settings.max_services_per_project:
        raise ProjectServiceError(
            f"Project already has the maximum of {settings.max_services_per_project} services"
        )

    preset = _PRESETS.get(kind)
    resolved_image = image or (preset["image"] if preset else None)
    if not resolved_image:
        raise ProjectServiceError(
            f"Unknown service kind '{kind}' - call request_service again with an explicit "
            f"image, e.g. image='some/image:tag'"
        )
    resolved_data_path = data_path or (preset["data_path"] if preset else None)

    credentials = _generate_preset_credentials(kind) if preset else {}
    stored = {"credentials": credentials, "env": env or {}}

    row = ProjectService(
        project_id=project.id,
        kind=kind,
        image=resolved_image,
        data_path=resolved_data_path,
        container_name=container_name_for(str(project.id), kind),
        volume_name=volume_name_for(str(project.id), kind),
        reason=reason,
        encrypted_credentials=encrypt_secret(json.dumps(stored)),
        status="requested",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True


def _stored(row: ProjectService) -> dict[str, Any]:
    if not row.encrypted_credentials:
        return {"credentials": {}, "env": {}}
    return json.loads(decrypt_secret(row.encrypted_credentials))


def build_connection_env(db: Session, project: Project) -> dict[str, str]:
    """DB-only - builds the connection-string env vars the app container should read.

    Uses each service's container_name as the hostname, which resolves because the app
    container and the sidecar share the private per-project network (see docker_adapter.py's
    ensure_private_network/attach_to_network). Only known presets get an automatic *_URL var -
    for a custom image the agent supplied its own `env` and already knows the hostname/port
    convention for whatever it picked (the request_service tool result told it the hostname).
    """
    rows = db.query(ProjectService).filter(ProjectService.project_id == project.id).all()
    env: dict[str, str] = {}
    for row in rows:
        stored = _stored(row)
        creds = stored.get("credentials") or {}
        if row.kind in _PRESETS and creds:
            env.update(_preset_connection_env(row.kind, creds, row.container_name))
    return env


def _wait_postgres_ready(client: Any, container_name: str, *, timeout: float = 45.0) -> None:
    """Best-effort wait until Postgres accepts connections inside its container."""
    try:
        container = client.containers.get(container_name)
    except Exception:  # noqa: BLE001
        return
    if not hasattr(container, "exec_run"):
        # Unit-test fakes often omit exec_run; skip rather than spinning for `timeout`.
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = container.exec_run(["pg_isready", "-U", "app", "-d", "app"])
            exit_code = result[0] if isinstance(result, tuple) else getattr(result, "exit_code", 1)
            if exit_code == 0:
                return
        except Exception:  # noqa: BLE001 - readiness is best-effort; deploy may still retry
            pass
        time.sleep(0.5)


def ensure_service_containers(client: Any, project_id: str, services: list[ProjectService]) -> None:
    """Idempotent: ensures the private network + each sidecar container exist and are running.

    Takes an already-constructed Docker client rather than calling docker.from_env() itself, so
    this is unit-testable with a fake client (see tests/test_deployment_adapter.py's pattern).
    """
    from docker.errors import NotFound

    net_name = network_name(project_id)
    try:
        client.networks.get(net_name)
    except NotFound:
        client.networks.create(net_name, driver="bridge")

    for row in services:
        try:
            existing = client.containers.get(row.container_name)
            if existing.status != "running":
                existing.start()
            # Ensure long-lived sidecars stay on the private project network (idempotent reconnect).
            try:
                client.networks.get(net_name).connect(getattr(existing, "id", row.container_name))
            except Exception:  # noqa: BLE001 - already connected is fine
                pass
        except NotFound:
            stored = _stored(row)
            credentials = stored.get("credentials") or {}
            environment = dict(_preset_container_env(row.kind, credentials)) if credentials else {}
            environment.update(stored.get("env") or {})
            command = _preset_command(row.kind, credentials) if credentials else None

            client.containers.run(
                row.image,
                detach=True,
                name=row.container_name,
                command=command,
                environment=environment or None,
                volumes=_volume_binds(row),
                network=net_name,
                labels=_service_labels(project_id),
                mem_limit=settings.deployment_service_memory_limit,
                nano_cpus=int(float(settings.deployment_service_cpu_limit) * 1_000_000_000),
                restart_policy={"Name": "unless-stopped"},
            )

        if row.kind == "postgres":
            _wait_postgres_ready(client, row.container_name)


def teardown_service_containers(client: Any, project_id: str, *, remove_volumes: bool) -> None:
    """Stops+removes sidecar containers for this project.

    If remove_volumes, also deletes host bind-mount data under deployment_volumes_dir,
    any legacy Docker named volumes labeled for this project, and the private network —
    called on project deletion/cleanup, not on a plain stop or redeploy (which must keep data).
    """
    from docker.errors import NotFound

    net_name = network_name(project_id)
    role_filter = {"label": [f"airuntime.project_id={project_id}", "airuntime.role=service"]}
    containers = client.containers.list(all=True, filters=role_filter)
    for container in containers:
        try:
            if container.status == "running":
                container.stop(timeout=10)
        except Exception:
            pass
        try:
            container.remove(force=True)
        except Exception:
            pass

    if not remove_volumes:
        return

    # Durable host bind mounts (current design).
    shutil.rmtree(project_volumes_root(project_id), ignore_errors=True)

    # Legacy Docker named volumes from earlier releases — remove if still present.
    for volume in client.volumes.list(filters=role_filter):
        try:
            volume.remove(force=True)
        except NotFound:
            pass
        except Exception:
            pass

    try:
        client.networks.get(net_name).remove()
    except NotFound:
        pass
    except Exception:
        pass
