"""Public website / Telegram-bot URLs for a platform project (admin display)."""

from __future__ import annotations

import os
import re
from typing import Any

_TME_RE = re.compile(r"https://t\.me/[A-Za-z0-9_]+", re.IGNORECASE)
_DEFAULT_APP_DOMAIN = "airuntime.ru"


def app_domain() -> str:
    return (os.environ.get("APP_DOMAIN") or _DEFAULT_APP_DOMAIN).strip() or _DEFAULT_APP_DOMAIN


def _absolute_url(url: str) -> str:
    href = url.strip()
    if not href:
        return ""
    if href.startswith(("http://", "https://", "tg://")):
        return href
    return f"https://{href.lstrip('/')}"


def collect_product_links(obj: Any, *, domain: str | None = None) -> list[tuple[str, str]]:
    """Return ``(label, href)`` pairs for the live site and/or Telegram bot.

    ``deployment_url`` is the primary public link (website for site/mixed, t.me for bots).
    Fallbacks: verified custom domain, ``{subdomain}.{app_domain}``, ``t.me`` parsed from logs
    (mixed projects store the bot URL there).
    """
    domain = (domain or app_domain()).strip() or _DEFAULT_APP_DOMAIN
    seen: set[str] = set()
    links: list[tuple[str, str]] = []

    def add(label: str, raw: str) -> None:
        href = _absolute_url(raw)
        if not href:
            return
        key = href.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        links.append((label, href))

    deployment_url = (getattr(obj, "deployment_url", None) or "").strip()
    if deployment_url:
        add("Бот" if "t.me/" in deployment_url.lower() else "Сайт", deployment_url)

    custom = (getattr(obj, "custom_domain", None) or "").strip()
    custom_status = (getattr(obj, "custom_domain_status", None) or "").strip()
    if custom and custom_status == "verified":
        add("Домен", f"https://{custom}")

    project_type = (getattr(obj, "type", None) or "").strip()
    subdomain = (getattr(obj, "deploy_subdomain", None) or "").strip()
    has_site = any(label == "Сайт" for label, _ in links)
    if subdomain and project_type in ("website", "mixed") and not has_site:
        add("Сайт", f"https://{subdomain}.{domain}")

    has_bot = any("t.me/" in href.lower() for _, href in links)
    if project_type in ("telegram_bot", "mixed") and not has_bot:
        match = _TME_RE.search(getattr(obj, "logs", None) or "")
        if match:
            add("Бот", match.group(0))

    return links


def link_display_text(href: str) -> str:
    return href.replace("https://", "").replace("http://", "").rstrip("/")
