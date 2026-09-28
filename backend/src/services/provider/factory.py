"""Provider client factory and model auto-selection.

Strategy (when no per-chat ``model`` override is set):
1. Read a ranked list from admin ``system_settings`` key ``preferred_models``
   (or ``top_models``) if present.
2. Otherwise use the curated allowlists in ``config`` (coding-oriented,
   newest / strongest first, with quality taking priority over cost).
3. Pick the first entry whose provider has a working API key
   (admin setting or env).
4. OpenAI floor: never auto-select anything weaker than the current frontier
   ``gpt-5.6-sol`` tier.
5. Explicit user ``model`` (and optional ``provider``) overrides always win.
"""

from __future__ import annotations

from typing import Any

from src.core.config import (
    CURATED_TOP_MODELS,
    OPENAI_MODEL_FLOOR,
    SUPPORTED_LLM_PROVIDERS,
    settings,
)
from src.services.model_pricing import is_selectable_model
from src.services.provider.base import ProviderClient
from src.services.provider.external import ExternalProviderClient
from src.services.system_settings import (
    get_system_setting_json,
    resolve_platform_api_key,
)

_SUPPORTED = SUPPORTED_LLM_PROVIDERS

_OPENAI_BLOCKED_SUBSTRINGS = (
    "mini",
    "nano",
    "terra",
    "luna",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-",
    "gpt-3.5",
    "gpt-3-",
)


def _slug(model: str) -> str:
    return model.rsplit("/", 1)[-1].strip().lower()


def meets_openai_floor(model: str) -> bool:
    """Return True for the current OpenAI frontier tier (or a newer full frontier model)."""
    slug = _slug(model)
    if not slug:
        return False
    if any(blocked in slug for blocked in _OPENAI_BLOCKED_SUBSTRINGS):
        return False
    if slug == OPENAI_MODEL_FLOOR or slug.startswith(f"{OPENAI_MODEL_FLOOR}-"):
        return True
    # The unsuffixed alias currently routes to Sol.
    if slug == "gpt-5.6":
        return True
    # Accept future full frontier releases without silently admitting older 5.x models.
    if slug.startswith("gpt-5."):
        version = slug.removeprefix("gpt-5.").split("-", 1)[0]
        if version.isdigit() and int(version) > 6:
            return True
    return False


def _provider_api_key(provider_name: str) -> str | None:
    try:
        return resolve_platform_api_key(provider_name)
    except Exception:
        key = getattr(settings, f"{provider_name}_api_key", None)
        if not key and provider_name == "routerai":
            return settings.openai_api_key
        return key


def _provider_configured(provider_name: str) -> bool:
    key = _provider_api_key(provider_name)
    return bool(key and str(key).strip())


def _default_model_for_provider(provider_name: str) -> str:
    mapping = {
        "openai": settings.default_model_openai,
        "anthropic": settings.default_model_anthropic,
        "gemini": settings.default_model_gemini,
        "openrouter": settings.default_model_openrouter,
        "routerai": settings.default_model_routerai,
    }
    return mapping.get(provider_name, settings.default_model_openai)


def _passes_floor(provider: str, model: str) -> bool:
    if provider == "openai":
        return meets_openai_floor(model)
    if provider in {"openrouter", "routerai"}:
        slug = _slug(model)
        # Only enforce the floor on OpenAI-family routes.
        if slug.startswith("gpt-") or slug.startswith("o3") or slug.startswith("o4"):
            return meets_openai_floor(model)
    return True


def _parse_preferred_models(raw: Any) -> list[tuple[str, str]]:
    """Normalize admin JSON into a ranked ``(provider, model)`` list."""
    if raw is None:
        return []

    entries: list[tuple[str, str]] = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                provider = str(item.get("provider") or "").strip().lower()
                model = str(item.get("model") or "").strip()
                if provider in _SUPPORTED and model:
                    entries.append((provider, model))
            elif isinstance(item, str) and item.strip():
                text = item.strip()
                # "openai:gpt-5.6-sol" — unambiguous provider/model split.
                if ":" in text and "/" not in text.split(":", 1)[0]:
                    provider, _, model = text.partition(":")
                    provider = provider.strip().lower()
                    model = model.strip()
                    if provider in _SUPPORTED and model:
                        entries.append((provider, model))
                    continue
                # "openai/gpt-5.6-sol" → openai + gpt-5.6-sol
                # For openrouter-native ids use the dict form or per-provider map.
                provider, _, rest = text.partition("/")
                provider = provider.strip().lower()
                rest = rest.strip()
                if provider in {"openai", "anthropic", "gemini"} and rest:
                    entries.append((provider, rest))
                elif provider in {"openrouter", "routerai"} and rest:
                    entries.append((provider, rest))
                elif text.count("/") >= 1:
                    entries.append(("openrouter", text))
        return entries

    if isinstance(raw, dict):
        ranked = raw.get("ranked") or raw.get("models")
        if isinstance(ranked, list):
            return _parse_preferred_models(ranked)

        provider_order = [p for p in _SUPPORTED if p in raw]
        for extra in raw:
            if extra in _SUPPORTED and extra not in provider_order:
                provider_order.append(extra)
        for provider in provider_order:
            models = raw.get(provider)
            if isinstance(models, str) and models.strip():
                entries.append((provider, models.strip()))
            elif isinstance(models, list):
                for model in models:
                    if isinstance(model, str) and model.strip():
                        entries.append((provider, model.strip()))
        return entries

    return []


def _curated_ranked_list() -> list[tuple[str, str]]:
    """Interleave curated per-provider lists in a coding-friendly provider order."""
    entries: list[tuple[str, str]] = []
    preference = ("openai", "anthropic", "gemini", "openrouter", "routerai")
    max_depth = max((len(CURATED_TOP_MODELS.get(p, [])) for p in preference), default=0)
    for depth in range(max_depth):
        for provider in preference:
            models = CURATED_TOP_MODELS.get(provider, [])
            if depth < len(models):
                entries.append((provider, models[depth]))
    return entries


def _admin_ranked_list() -> list[tuple[str, str]]:
    for key in ("preferred_models", "top_models"):
        try:
            raw = get_system_setting_json(key)
        except Exception:
            # DB unavailable during local tooling / boot — fall back to curated lists.
            return []
        parsed = _parse_preferred_models(raw)
        if parsed:
            return parsed
    return []


def ranked_models(*, provider_name: str | None = None) -> list[tuple[str, str]]:
    """Return ranked ``(provider, model)`` candidates, optionally scoped to one provider."""
    admin = _admin_ranked_list()
    curated = _curated_ranked_list()
    seen: set[tuple[str, str]] = set()
    merged: list[tuple[str, str]] = []
    for pair in admin + curated:
        if pair in seen:
            continue
        seen.add(pair)
        merged.append(pair)

    if provider_name:
        scoped = [(p, m) for p, m in merged if p == provider_name]
        if scoped:
            return scoped
        return [(provider_name, _default_model_for_provider(provider_name))]
    return merged


def resolve_model(provider_name: str) -> str:
    """Pick the best allowlisted model for ``provider_name``."""
    name = provider_name if provider_name in _SUPPORTED else settings.provider_name
    for provider, model in ranked_models(provider_name=name):
        if _passes_floor(provider, model):
            return model
    fallback = _default_model_for_provider(name)
    if name == "openai" and not meets_openai_floor(fallback):
        return OPENAI_MODEL_FLOOR
    return fallback


def resolve_provider_and_model(
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    """Resolve provider + model for a chat turn.

    - Explicit ``model_override`` wins (provider from override or settings).
    - Explicit ``provider_override`` → top model for that provider.
    - Otherwise auto-select the first ranked ``(provider, model)`` with an API key.
    """
    if model_override and model_override.strip():
        provider = (provider_override or settings.provider_name).strip().lower()
        if provider not in _SUPPORTED:
            provider = settings.provider_name
        model = model_override.strip()
        if not is_selectable_model(provider, model):
            raise ValueError(f"Model {model!r} is not available for provider {provider!r}")
        return provider, model

    if provider_override and provider_override.strip():
        provider = provider_override.strip().lower()
        if provider not in _SUPPORTED:
            provider = settings.provider_name
        return provider, resolve_model(provider)

    for provider, model in ranked_models():
        if not _passes_floor(provider, model):
            continue
        if _provider_configured(provider):
            return provider, model

    provider = settings.provider_name if settings.provider_name in _SUPPORTED else "openai"
    return provider, resolve_model(provider)


def get_provider(provider_name: str | None = None) -> ProviderClient:
    selected = provider_name or settings.provider_name
    if selected in _SUPPORTED:
        return ExternalProviderClient(provider_name=selected)
    return ExternalProviderClient(provider_name="openai")
