from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.core.config import CURATED_TOP_MODELS, SUPPORTED_LLM_PROVIDERS, settings
from src.db.models.user import User
from src.db.session import get_db
from src.services.byok import has_valid_key
from src.services.model_access import resolve_model_for_user, visible_models_for_user
from src.services.model_pricing import public_model_options
from src.services.system_settings import resolve_platform_api_key

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderConfigResponse(BaseModel):
    active: str
    supported: list[str]
    configured: dict[str, bool]
    auto_provider: str
    auto_model: str
    defaults: dict[str, str]
    top_models: dict[str, list[str]]
    models: dict[str, list[dict]]
    credits_per_rub: int


def _provider_defaults() -> dict[str, str]:
    return {
        "openai": settings.default_model_openai,
        "anthropic": settings.default_model_anthropic,
        "gemini": settings.default_model_gemini,
        "openrouter": settings.default_model_openrouter,
        "routerai": settings.default_model_routerai,
    }


@router.get("", response_model=ProviderConfigResponse)
def list_providers(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProviderConfigResponse:
    # Plan-aware: a free account must not be offered (or auto-routed to) the frontier model.
    auto_provider, auto_model = resolve_model_for_user(db, current_user)
    configured = {
        provider: bool(
            resolve_platform_api_key(provider) or has_valid_key(db, current_user, provider)
        )
        for provider in SUPPORTED_LLM_PROVIDERS
    }
    return ProviderConfigResponse(
        active=settings.provider_name,
        supported=list(SUPPORTED_LLM_PROVIDERS),
        configured=configured,
        auto_provider=auto_provider,
        auto_model=auto_model,
        defaults=_provider_defaults(),
        top_models={
            provider: [
                str(row["id"])
                for row in visible_models_for_user(
                    db, current_user, public_model_options(provider), provider=provider
                )
            ]
            or CURATED_TOP_MODELS.get(provider, [])
            for provider in SUPPORTED_LLM_PROVIDERS
        },
        models={
            provider: visible_models_for_user(
                db, current_user, public_model_options(provider), provider=provider
            )
            for provider in SUPPORTED_LLM_PROVIDERS
        },
        credits_per_rub=settings.billing_credits_per_rub,
    )
