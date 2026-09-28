from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import docker
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from core.models import Project


@dataclass(frozen=True)
class DockerPageError:
    message: str


def _client():
    return docker.from_env()


def _short_id(value: str | None) -> str:
    if not value:
        return ""
    return value[:12]


def _format_bytes(value: int | float | None) -> str:
    if value is None:
        return "-"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _parse_created(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _attach_project_info(rows: list[dict[str, Any]]) -> None:
    project_ids = [row["project_id"] for row in rows if row.get("project_id")]
    if not project_ids:
        return
    projects = {
        str(project.id): project
        for project in Project.objects.filter(id__in=project_ids).select_related("user")
    }
    for row in rows:
        project = projects.get(row.get("project_id"))
        row["project_name"] = project.name if project else ""
        row["owner_email"] = project.user.email if project else ""


def _container_rows() -> tuple[list[dict[str, Any]], DockerPageError | None]:
    try:
        client = _client()
        rows = []
        for container in client.containers.list(all=True):
            attrs = container.attrs
            labels = attrs.get("Config", {}).get("Labels") or {}
            ports = attrs.get("NetworkSettings", {}).get("Ports") or {}
            rows.append(
                {
                    "id": container.id,
                    "short_id": _short_id(container.id),
                    "name": container.name,
                    "image": attrs.get("Config", {}).get("Image") or "-",
                    "status": container.status,
                    "created": _parse_created(attrs.get("Created")),
                    "project_id": labels.get("airuntime.project_id", ""),
                    "managed": labels.get("airuntime.managed") == "true",
                    "ports": ports,
                    "project_name": "",
                    "owner_email": "",
                }
            )
        _attach_project_info(rows)
        rows.sort(key=lambda item: (not item["managed"], item["name"]))
        return rows, None
    except DockerException as exc:
        return [], DockerPageError(str(exc))


def _image_rows() -> tuple[list[dict[str, Any]], DockerPageError | None]:
    try:
        client = _client()
        rows = []
        for image in client.images.list():
            attrs = image.attrs
            tags = image.tags or ["<none>:<none>"]
            rows.append(
                {
                    "id": image.id,
                    "short_id": image.short_id.replace("sha256:", ""),
                    "tags": tags,
                    "size": _format_bytes(attrs.get("Size")),
                    "created": _parse_created(attrs.get("Created")),
                }
            )
        rows.sort(key=lambda item: ", ".join(item["tags"]))
        return rows, None
    except DockerException as exc:
        return [], DockerPageError(str(exc))


def _sum_network(stats: dict[str, Any]) -> tuple[int, int]:
    rx = 0
    tx = 0
    for data in (stats.get("networks") or {}).values():
        rx += int(data.get("rx_bytes") or 0)
        tx += int(data.get("tx_bytes") or 0)
    return rx, tx


def _sum_block_io(stats: dict[str, Any]) -> tuple[int, int]:
    read = 0
    write = 0
    for item in stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []:
        operation = str(item.get("op") or "").lower()
        value = int(item.get("value") or 0)
        if operation == "read":
            read += value
        elif operation == "write":
            write += value
    return read, write


def _cpu_percent(stats: dict[str, Any]) -> float:
    cpu_stats = stats.get("cpu_stats") or {}
    precpu_stats = stats.get("precpu_stats") or {}
    cpu_delta = ((cpu_stats.get("cpu_usage") or {}).get("total_usage") or 0) - (
        (precpu_stats.get("cpu_usage") or {}).get("total_usage") or 0
    )
    system_delta = (cpu_stats.get("system_cpu_usage") or 0) - (
        precpu_stats.get("system_cpu_usage") or 0
    )
    online_cpus = cpu_stats.get("online_cpus") or len(
        (cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or []
    )
    if cpu_delta > 0 and system_delta > 0 and online_cpus > 0:
        return (cpu_delta / system_delta) * online_cpus * 100
    return 0.0


def _resource_rows() -> tuple[list[dict[str, Any]], DockerPageError | None]:
    try:
        client = _client()
        rows = []
        for container in client.containers.list(all=False):
            stats = container.stats(stream=False)
            labels = container.attrs.get("Config", {}).get("Labels") or {}
            memory = stats.get("memory_stats") or {}
            memory_usage = int(memory.get("usage") or 0)
            memory_cache = int((memory.get("stats") or {}).get("cache") or 0)
            memory_limit = int(memory.get("limit") or 0)
            memory_actual = max(memory_usage - memory_cache, 0)
            rx, tx = _sum_network(stats)
            block_read, block_write = _sum_block_io(stats)
            rows.append(
                {
                    "id": container.id,
                    "short_id": _short_id(container.id),
                    "name": container.name,
                    "image": container.attrs.get("Config", {}).get("Image") or "-",
                    "project_id": labels.get("airuntime.project_id", ""),
                    "project_name": "",
                    "owner_email": "",
                    "cpu": _cpu_percent(stats),
                    "memory": _format_bytes(memory_actual),
                    "memory_limit": _format_bytes(memory_limit) if memory_limit else "-",
                    "memory_percent": min(
                        (memory_actual / memory_limit * 100) if memory_limit else 0,
                        100,
                    ),
                    "network_rx": _format_bytes(rx),
                    "network_tx": _format_bytes(tx),
                    "block_read": _format_bytes(block_read),
                    "block_write": _format_bytes(block_write),
                    "pids": (stats.get("pids_stats") or {}).get("current") or 0,
                }
            )
        _attach_project_info(rows)
        rows.sort(key=lambda item: item["cpu"], reverse=True)
        return rows, None
    except DockerException as exc:
        return [], DockerPageError(str(exc))


def docker_containers(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        return _handle_container_action(request)
    rows, error = _container_rows()
    return render(
        request,
        "admin/docker_containers.html",
        {
            "title": "Docker контейнеры",
            "containers": rows,
            "docker_error": error,
        },
    )


def docker_images(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        return _handle_image_action(request)
    rows, error = _image_rows()
    return render(
        request,
        "admin/docker_images.html",
        {
            "title": "Docker образы",
            "images": rows,
            "docker_error": error,
        },
    )


def docker_resources(request: HttpRequest) -> HttpResponse:
    rows, error = _resource_rows()
    return render(
        request,
        "admin/docker_resources.html",
        {
            "title": "Ресурсы Docker",
            "resources": rows,
            "docker_error": error,
        },
    )


def _handle_container_action(request: HttpRequest) -> HttpResponseRedirect:
    action = request.POST.get("action")
    container_id = request.POST.get("container_id")
    if action not in {"stop", "restart", "remove"} or not container_id:
        messages.error(request, "Некорректное действие Docker.")
        return redirect(reverse("admin_docker_containers"))
    try:
        container = _client().containers.get(container_id)
        if action == "stop":
            container.stop(timeout=10)
            messages.success(request, f"Контейнер {container.name} остановлен.")
        elif action == "restart":
            container.restart(timeout=10)
            messages.success(request, f"Контейнер {container.name} перезапущен.")
        elif action == "remove":
            name = container.name
            container.remove(force=True)
            messages.success(request, f"Контейнер {name} удален.")
    except NotFound:
        messages.error(request, "Контейнер не найден.")
    except APIError as exc:
        messages.error(request, f"Docker API error: {exc.explanation or exc}")
    except DockerException as exc:
        messages.error(request, f"Docker error: {exc}")
    return redirect(reverse("admin_docker_containers"))


def _handle_image_action(request: HttpRequest) -> HttpResponseRedirect:
    action = request.POST.get("action")
    image_id = request.POST.get("image_id")
    if action != "remove" or not image_id:
        messages.error(request, "Некорректное действие Docker.")
        return redirect(reverse("admin_docker_images"))
    try:
        _client().images.remove(image=image_id, force=True)
        messages.success(request, "Образ удален.")
    except ImageNotFound:
        messages.error(request, "Образ не найден.")
    except APIError as exc:
        messages.error(request, f"Docker API error: {exc.explanation or exc}")
    except DockerException as exc:
        messages.error(request, f"Docker error: {exc}")
    return redirect(reverse("admin_docker_images"))
