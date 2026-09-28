"""Periodic sweep for explicitly project-scoped scratch images created by Codex self-tests.

Three independent checks, all required, so this can run unattended on a schedule without risking
a platform-managed or currently-needed image:
- Age gate: never touch anything created within the last _MIN_AGE_SECONDS, so this can't race a
  build that's still in progress.
- Positive ownership allowlist: only touch `airuntime-scratch-*`. Unknown host images are never
  AIRuntime's property; treating "not airuntime-*" as disposable previously deleted unrelated
  local images on worker startup.
- Reference check: never touch an image that any container (running OR stopped) still points at -
  covers every pulled base image (postgres, redis, traefik, minio, the request_service presets,
  ...), since Compose's `restart: unless-stopped` containers for those keep existing as container
  objects across restarts, not just while running.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_MIN_AGE_SECONDS = 2 * 3600
_SCRATCH_PREFIX = "airuntime-scratch-"
_TEMPORARY_ROLES = frozenset({"codex", "preview"})


def _parse_docker_created(value: Any) -> float | None:
    """Docker's image `Created` is an ISO8601 string with up to 9 fractional digits from
    inspect/images.list(), but defensively also accept a bare unix-epoch number. Never raises -
    a failure to parse means "assume not old enough", the safe direction for this sweep."""
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value
    if text.endswith("Z"):
        body, _, frac = text[:-1].partition(".")
        text = f"{body}.{frac[:6]}+00:00" if frac else f"{body}+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _container_is_temporary(container: Any) -> bool:
    labels = getattr(container, "labels", None) or {}
    role = labels.get("airuntime.role")
    if role in _TEMPORARY_ROLES:
        return True
    # `container.image` performs a second Docker image inspect. That raises ImageNotFound for an
    # old container after its image was pruned, aborting the entire container sweep. Config.Image
    # is embedded in the container inspection payload and remains available in that state.
    attrs = getattr(container, "attrs", None) or {}
    image_ref = str((attrs.get("Config") or {}).get("Image") or "")
    if not image_ref.startswith(_SCRATCH_PREFIX):
        return False
    # A generated app always has the deterministic name and Traefik labels. A model's ad-hoc
    # `docker run` uses a random name (the production leak was `youthful_euclid`). Never infer
    # ownership from age alone and never touch the canonical deployed app.
    project_id = labels.get("airuntime.project_id")
    canonical = f"airuntime-{str(project_id)[:8]}" if project_id else None
    name = str(getattr(container, "name", "") or "").lstrip("/")
    return bool(project_id and name != canonical and labels.get("traefik.enable") != "true")


def sweep_temporary_containers(client: Any) -> list[str]:
    """Remove old Codex/preview/scratch containers, including leaked running self-tests.

    The two-hour age gate is four times the normal task silence timeout and longer than the
    default orchestration task budget, so an active generation cannot be collected. Selection
    still requires an explicit AIRuntime role or a project-owned scratch image.
    """
    try:
        containers = client.containers.list(all=True)
    except Exception:  # noqa: BLE001
        return []
    cutoff = time.time() - _MIN_AGE_SECONDS
    removed: list[str] = []
    for container in containers:
        if not _container_is_temporary(container):
            continue
        created = _parse_docker_created((getattr(container, "attrs", None) or {}).get("Created"))
        if created is None or created > cutoff:
            continue
        name = str(getattr(container, "name", "") or getattr(container, "id", "unknown"))
        try:
            if getattr(container, "status", None) == "running":
                container.stop(timeout=10)
            container.remove(force=True)
        except Exception:  # noqa: BLE001 - best effort; next sweep retries
            continue
        removed.append(name)
    if removed:
        logger.info(
            "Container janitor removed %d temporary container(s): %s", len(removed), removed
        )
    return removed


def sweep_unrecognized_images(client: Any) -> list[str]:
    """Remove old, unreferenced AIRuntime scratch images. Returns removed tag(s)."""
    removed: list[str] = []
    try:
        images = client.images.list()
        containers = client.containers.list(all=True)
    except Exception:  # noqa: BLE001 - a listing failure just means "nothing to do this round"
        return removed

    referenced_image_ids: set[str] = set()
    for container in containers:
        try:
            referenced_image_ids.add(container.image.id)
        except Exception:  # noqa: BLE001 - a container in a weird state shouldn't abort the sweep
            continue

    cutoff = time.time() - _MIN_AGE_SECONDS
    for image in images:
        tags = getattr(image, "tags", None) or []
        if not tags:
            continue  # dangling - deploy.sh's `docker image prune` already handles these
        if not any(tag.startswith(_SCRATCH_PREFIX) for tag in tags):
            continue
        if image.id in referenced_image_ids:
            continue
        created = _parse_docker_created((getattr(image, "attrs", None) or {}).get("Created"))
        if created is None or created > cutoff:
            continue
        try:
            client.images.remove(image=image.id, force=True)
        except Exception:  # noqa: BLE001 - best-effort; next sweep tries again
            continue
        removed.extend(tags)

    if removed:
        logger.info("Image janitor removed %d ad-hoc image(s): %s", len(removed), removed)
    return removed
