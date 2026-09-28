from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from src.db.models.system_setting import SystemSetting
from src.db.session import SessionLocal

_TTL_SECONDS = 60
_cache: dict[str, tuple[Any, float]] = {}


def _extract_api_key(setting: SystemSetting) -> str | None:
    if setting.value_text and str(setting.value_text).strip():
        return str(setting.value_text).strip()

    if setting.value_json:
        # Accept a few common shapes: {"value": "..."} / {"api_key": "..."} / {"key": "..."}
        for candidate in ("value", "api_key", "key"):
            val = setting.value_json.get(candidate)
            if val and str(val).strip():
                return str(val).strip()

    return None


def get_system_setting_value(setting_key: str) -> str | None:
    now = time.monotonic()
    cached = _cache.get(setting_key)
    if cached:
        value, expires_at = cached
        if now < expires_at:
            return value

    db = SessionLocal()
    try:
        stmt = select(SystemSetting).where(SystemSetting.key == setting_key)
        setting = db.execute(stmt).scalar_one_or_none()
        value = _extract_api_key(setting) if setting else None
        _cache[setting_key] = (value, now + _TTL_SECONDS)
        return value
    finally:
        db.close()


def get_system_setting_number(setting_key: str) -> int | None:
    now = time.monotonic()
    cache_key = f"number:{setting_key}"
    cached = _cache.get(cache_key)
    if cached:
        value, expires_at = cached
        if now < expires_at:
            return value

    db = SessionLocal()
    try:
        stmt = select(SystemSetting).where(SystemSetting.key == setting_key)
        setting = db.execute(stmt).scalar_one_or_none()
        value = setting.value_number if setting and setting.is_enabled else None
        _cache[cache_key] = (value, now + _TTL_SECONDS)
        return value
    finally:
        db.close()


def get_system_setting_json(setting_key: str) -> Any | None:
    """Return ``value_json`` for an enabled setting, or None."""
    now = time.monotonic()
    cache_key = f"json:{setting_key}"
    cached = _cache.get(cache_key)
    if cached:
        value, expires_at = cached
        if now < expires_at:
            return value

    db = SessionLocal()
    try:
        stmt = select(SystemSetting).where(SystemSetting.key == setting_key)
        setting = db.execute(stmt).scalar_one_or_none()
        value = setting.value_json if setting and setting.is_enabled else None
        _cache[cache_key] = (value, now + _TTL_SECONDS)
        return value
    except Exception:
        # Prefer curated defaults over failing chat when admin DB is unreachable.
        _cache[cache_key] = (None, now + _TTL_SECONDS)
        return None
    finally:
        db.close()


def resolve_api_key_for_provider(provider_name: str) -> str | None:
    # Priority list: keys that are likely to be set in admin.
    # User mentioned key "gpt", we also support the earlier seed "openai_api_key".
    if provider_name == "openai":
        candidates = ["openai_api_key", "OPENAI_API_KEY", "gpt", "GPT"]
    elif provider_name == "openrouter":
        candidates = ["openrouter_api_key", "OPENROUTER_API_KEY", "openrouter", "OPENROUTER"]
    elif provider_name == "anthropic":
        candidates = ["anthropic_api_key", "ANTHROPIC_API_KEY", "anthropic", "ANTHROPIC"]
    elif provider_name == "gemini":
        candidates = ["gemini_api_key", "GEMINI_API_KEY", "gemini", "GEMINI"]
    elif provider_name == "routerai":
        candidates = ["routerai_api_key", "ROUTERAI_API_KEY", "routerai", "ROUTERAI"]
    else:
        candidates = [provider_name]

    for key in candidates:
        value = get_system_setting_value(key)
        if value:
            return value
    return None


def resolve_platform_api_key(provider_name: str) -> str | None:
    """Admin setting, then env. RouterAI falls back to the OpenAI/proxy token."""
    from src.core.config import settings

    name = (provider_name or "").strip().lower()
    value = resolve_api_key_for_provider(name) or getattr(settings, f"{name}_api_key", None)
    if value and str(value).strip():
        return str(value).strip()
    if name == "routerai":
        fallback = resolve_api_key_for_provider("openai") or settings.openai_api_key
        if fallback and str(fallback).strip():
            return str(fallback).strip()
    return None
