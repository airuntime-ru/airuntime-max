"""Custom domains for projects (Epic D).

A user points their own hostname at the platform, we verify the DNS actually resolves to us,
and only then treat it as active. Available on every plan, including free.

Verification is deliberately DNS-only and read-only: we never need access to the user's
registrar, and a wrong record produces an explainable status instead of a silent failure.
"""

from __future__ import annotations

import logging
import re
import socket
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.project import Project

logger = logging.getLogger(__name__)

STATUS_NONE = "none"
STATUS_PENDING = "pending_dns"
STATUS_VERIFIED = "verified"
STATUS_ERROR = "error"

# Hostname per RFC 1123: labels of alphanumerics and hyphens, no leading/trailing hyphen.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")


class CustomDomainError(Exception):
    """User-facing problem with a custom domain."""


def normalize_domain(raw: str | None) -> str | None:
    """Accept what users actually paste (URLs, trailing dots, upper case) -> bare hostname."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0].split(":", 1)[0]
    value = value.rstrip(".")
    if not value:
        return None
    if len(value) > 253:
        raise CustomDomainError("Домен слишком длинный")
    if not _HOSTNAME_RE.match(value):
        raise CustomDomainError("Похоже, это не доменное имя. Пример: shop.example.com")
    platform = settings.resolved_app_domain.lower()
    if value == platform or value.endswith(f".{platform}"):
        raise CustomDomainError(
            f"Это домен платформы. Поддомен на {platform} настраивается выше, "
            "а здесь подключается собственный домен."
        )
    return value


def dns_target() -> dict[str, str | None]:
    """What the user must put in their DNS."""
    return {
        "cname_target": settings.resolved_app_domain,
        "a_record_ip": settings.server_ip,
    }


def _resolve_a_records(hostname: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return set()
    return {info[4][0] for info in infos}


def _platform_addresses() -> set[str]:
    addresses: set[str] = set()
    if settings.server_ip:
        addresses.add(settings.server_ip.strip())
    addresses |= _resolve_a_records(settings.resolved_app_domain)
    return addresses


def check_domain_dns(hostname: str) -> tuple[bool, str | None]:
    """Does `hostname` resolve to this platform? Returns (ok, human-readable error)."""
    resolved = _resolve_a_records(hostname)
    if not resolved:
        return False, "Домен пока не резолвится. DNS может обновляться до нескольких часов."
    expected = _platform_addresses()
    if not expected:
        # Misconfigured platform, not a user error - do not blame their DNS.
        logger.warning("Cannot verify custom domains: no SERVER_IP and app domain does not resolve")
        return False, "Проверка временно недоступна, попробуйте позже"
    if resolved & expected:
        return True, None
    return (
        False,
        "Домен ведёт на другой адрес ({}). Ожидали {}.".format(
            ", ".join(sorted(resolved)), ", ".join(sorted(expected))
        ),
    )


def assert_domain_available(db: Session, hostname: str, *, exclude_project_id: str | None) -> None:
    query = db.query(Project).filter(Project.custom_domain == hostname)
    if exclude_project_id:
        query = query.filter(Project.id != exclude_project_id)
    if query.first() is not None:
        raise CustomDomainError("Этот домен уже подключён к другому проекту")


def set_custom_domain(db: Session, project: Project, raw: str | None) -> Project:
    """Attach (or clear) a custom domain. Always lands in pending until DNS is verified."""
    hostname = normalize_domain(raw)
    if hostname is None:
        project.custom_domain = None
        project.custom_domain_status = STATUS_NONE
        project.custom_domain_verified_at = None
        project.custom_domain_error = None
        project.custom_domain_checked_at = None
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

    assert_domain_available(db, hostname, exclude_project_id=str(project.id))
    project.custom_domain = hostname
    project.custom_domain_status = STATUS_PENDING
    project.custom_domain_verified_at = None
    project.custom_domain_error = None
    project.custom_domain_checked_at = None
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def verify_custom_domain(db: Session, project: Project) -> Project:
    """Re-check DNS now and persist the outcome."""
    if not project.custom_domain:
        raise CustomDomainError("Домен не задан")
    ok, error = check_domain_dns(project.custom_domain)
    project.custom_domain_checked_at = datetime.now(UTC)
    if ok:
        project.custom_domain_status = STATUS_VERIFIED
        project.custom_domain_verified_at = datetime.now(UTC)
        project.custom_domain_error = None
    else:
        # Keep it pending rather than "error" while DNS is simply not there yet - propagation is
        # normal and an alarming status would just generate support noise.
        project.custom_domain_status = STATUS_PENDING
        project.custom_domain_error = error
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def recheck_pending_domains(db: Session, *, limit: int = 50) -> int:
    """Background sweep: re-verify domains still waiting on DNS. Returns how many turned live."""
    rows = (
        db.query(Project)
        .filter(
            Project.custom_domain.isnot(None),
            Project.custom_domain_status == STATUS_PENDING,
        )
        .limit(limit)
        .all()
    )
    verified = 0
    for project in rows:
        try:
            verify_custom_domain(db, project)
            if project.custom_domain_status == STATUS_VERIFIED:
                verified += 1
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            logger.exception("Custom domain recheck failed for project %s", project.id)
            db.rollback()
    return verified


def domain_response(project: Project) -> dict:
    return {
        "custom_domain": project.custom_domain,
        "status": project.custom_domain_status,
        "verified_at": project.custom_domain_verified_at,
        "error": project.custom_domain_error,
        "checked_at": project.custom_domain_checked_at,
        "target": dns_target(),
    }
