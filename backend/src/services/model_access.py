"""Per-plan gating of models and reasoning effort on the platform API key.

Free plans run the economy model at moderate reasoning: the grant is one-time and small, so a
frontier model at max effort would burn it in a couple of turns. Paid plans get the full
catalog. BYOK bypasses all of this - own key, own money (see Epic C).
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.user import User
from src.services.billing import allowed_platform_models
from src.services.provider.factory import resolve_provider_and_model

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]

# Product decision 2026-08-07: `medium`, not `low`. A first build that fails on low effort burns
# the same 100 ₽ grant on repair turns, so cheap-per-token is not cheap per outcome.
RESTRICTED_REASONING_EFFORT: ReasoningEffort = "medium"


class ModelNotAllowedError(Exception):
    """The user's plan does not include the requested model on the platform key."""


def _slug(model: str) -> str:
    return model.rsplit("/", 1)[-1].strip().lower()


def resolve_model_for_user(
    db: Session,
    user: User,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
    has_own_key: bool = False,
) -> tuple[str, str]:
    """Resolve (provider, model) for a chat turn, honouring the user's plan allowlist.

    `has_own_key` lifts the allowlist entirely: on BYOK the provider bills the user directly,
    so there is nothing for the platform to ration (product decision 2026-08-07).
    """
    allowed = None if has_own_key else allowed_platform_models(db, user)
    if not allowed:
        return resolve_provider_and_model(
            provider_override=provider_override, model_override=model_override
        )

    allowed_slugs = {_slug(item) for item in allowed}

    if model_override and model_override.strip():
        if _slug(model_override) not in allowed_slugs:
            raise ModelNotAllowedError(
                "На бесплатном тарифе доступна только экономичная модель. "
                "Подключите свой API-ключ или смените тариф, чтобы выбрать другую."
            )
        return resolve_provider_and_model(
            provider_override=provider_override, model_override=model_override
        )

    # Auto-select must stay inside the allowlist - the global ranking prefers the frontier model.
    for candidate in allowed:
        try:
            return resolve_provider_and_model(
                provider_override=provider_override, model_override=candidate
            )
        except ValueError:
            continue
    return resolve_provider_and_model(provider_override=provider_override)


def reasoning_effort_for_user(db: Session, user: User, *, has_own_key: bool = False) -> str:
    """Reasoning effort for this user's plan.

    Restricted only when we are the ones paying for the tokens.
    """
    if not has_own_key and allowed_platform_models(db, user):
        return RESTRICTED_REASONING_EFFORT
    return settings.codex_reasoning_effort


def visible_models_for_user(
    db: Session, user: User, options: list[dict], *, provider: str
) -> list[dict]:
    """Filter a provider's public catalog down to what this user may actually run."""
    allowed = allowed_platform_models(db, user)
    if not allowed:
        return options
    allowed_slugs = {_slug(item) for item in allowed}
    return [row for row in options if _slug(str(row.get("id", ""))) in allowed_slugs]
