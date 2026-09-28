"""Cloudflare DNS sync for platform and project deployments."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)


class CloudflareDnsError(RuntimeError):
    pass


def dns_configured() -> bool:
    return bool(settings.cf_zone_id and settings.cf_api_token and settings.server_ip)


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.cf_zone_id or not settings.cf_api_token:
        raise CloudflareDnsError("Cloudflare credentials are not configured")

    url = f"https://api.cloudflare.com/client/v4/zones/{settings.cf_zone_id}{path}"
    headers = {
        "Authorization": f"Bearer {settings.cf_api_token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.request(method, url, headers=headers, json=body)
    payload = response.json()
    if response.status_code >= 400 or not payload.get("success", False):
        errors = payload.get("errors") or response.text
        raise CloudflareDnsError(f"Cloudflare API error: {errors}")
    return payload


def _platform_a_hosts(domain: str) -> tuple[str, ...]:
    """Hosts managed by ensure_platform_dns — never delete these via project cleanup."""
    return (
        domain,
        f"www.{domain}",
        f"api.{domain}",
        f"admin.{domain}",
        f"s3.{domain}",
        f"s3-console.{domain}",
        f"mail.{domain}",
        f"*.{domain}",
    )


def upsert_dns_record(
    record_type: str,
    name: str,
    content: str,
    *,
    priority: int | None = None,
) -> str:
    existing = _request("GET", f"/dns_records?type={record_type}&name={name}")
    records = existing.get("result") or []
    body: dict[str, Any] = {
        "type": record_type,
        "name": name,
        "content": content,
        "proxied": False,
        "ttl": 1,
    }
    if priority is not None:
        body["priority"] = priority

    if records:
        record_id = records[0]["id"]
        _request("PUT", f"/dns_records/{record_id}", body)
        return f"updated {record_type} {name}"

    _request("POST", "/dns_records", body)
    return f"created {record_type} {name}"


def delete_dns_record(record_type: str, name: str) -> str:
    """Delete a single DNS record by type+name. No-op (missing) if it does not exist."""
    existing = _request("GET", f"/dns_records?type={record_type}&name={name}")
    records = existing.get("result") or []
    if not records:
        return f"missing {record_type} {name}"
    record_id = records[0]["id"]
    _request("DELETE", f"/dns_records/{record_id}")
    return f"deleted {record_type} {name}"


def ensure_platform_dns() -> list[str]:
    """Apply the same records as scripts/setup_cloudflare_dns.sh."""
    if not dns_configured():
        raise CloudflareDnsError("SERVER_IP, CF_ZONE_ID and CF_API_TOKEN are required")

    domain = settings.resolved_app_domain
    ip = settings.server_ip
    if not ip:
        raise CloudflareDnsError("SERVER_IP is required")

    messages: list[str] = []
    for host in _platform_a_hosts(domain):
        messages.append(upsert_dns_record("A", host, ip))

    messages.append(upsert_dns_record("MX", domain, f"mail.{domain}", priority=10))
    messages.append(upsert_dns_record("TXT", domain, f"v=spf1 mx a ip4:{ip} -all"))
    messages.append(
        upsert_dns_record(
            "TXT",
            f"_dmarc.{domain}",
            f"v=DMARC1; p=quarantine; rua=mailto:admin@{domain}",
        )
    )
    return messages


def project_public_host(subdomain: str) -> str:
    return f"{subdomain}.{settings.resolved_app_domain}"


def sync_dns_for_website_deploy(subdomain: str) -> str | None:
    """Ensure platform + project DNS exist. Returns the public host on success, else None.

    Platform-wide record churn stays in server logs only — callers should not dump the
    raw upsert list into user-facing project.logs.
    """
    host = project_public_host(subdomain)
    if not dns_configured():
        logger.info("Cloudflare DNS sync skipped for %s: credentials not configured", host)
        return None

    platform_messages = ensure_platform_dns()
    logger.info("Cloudflare platform DNS: %s", "; ".join(platform_messages))
    project_message = upsert_dns_record("A", host, settings.server_ip or "")
    logger.info("Cloudflare DNS synced for %s (%s)", host, project_message)
    return host


def delete_dns_for_website_deploy(subdomain: str) -> str | None:
    """Remove the project-specific A record created by sync_dns_for_website_deploy.

    Does not touch platform-wide records (apex, www, api, wildcard, MX, TXT, …).
    Returns the public host when a delete was attempted, else None when skipped.
    """
    host = project_public_host(subdomain)
    if not dns_configured():
        logger.info("Cloudflare DNS delete skipped for %s: credentials not configured", host)
        return None

    domain = settings.resolved_app_domain
    if host in _platform_a_hosts(domain):
        raise CloudflareDnsError(f"Refusing to delete platform DNS record {host}")

    message = delete_dns_record("A", host)
    logger.info("Cloudflare DNS deleted for %s (%s)", host, message)
    return host


def user_facing_dns_note(
    subdomain: str, *, host: str | None = None, error: str | None = None
) -> str:
    """Short Russian line for project.logs / UI — never the full platform DNS dump."""
    resolved = host or project_public_host(subdomain)
    if error:
        return f"Не удалось настроить поддомен `{resolved}` в Cloudflare: {error}"
    return f"Поддомен `{resolved}` настроен в Cloudflare"
