"""Bring-your-own-key: per-user provider credentials.

Resolution order for a chat turn is: the user's own valid key for that provider, else the
platform key. On a user key nothing is deducted from the credit balance - the provider bills
them directly - but usage is still recorded (amount 0) so analytics can separate BYOK traffic
from traffic we actually pay for.
"""

from __future__ import annotations

import logging
import uuid

import httpx
from sqlalchemy.orm import Session

from src.core.config import ROUTERAI_DEFAULT_BASE_URL, SUPPORTED_LLM_PROVIDERS
from src.db.models.user import User
from src.db.models.user_provider_credential import UserProviderCredential
from src.services.secrets import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = SUPPORTED_LLM_PROVIDERS

# Cheap, read-only endpoints used purely to prove a key works.
_VALIDATION = {
    "openai": ("GET", "https://api.openai.com/v1/models", "Authorization", "Bearer {key}"),
    "openrouter": ("GET", "https://openrouter.ai/api/v1/models", "Authorization", "Bearer {key}"),
    "routerai": (
        "GET",
        f"{ROUTERAI_DEFAULT_BASE_URL}/models",
        "Authorization",
        "Bearer {key}",
    ),
    "anthropic": ("GET", "https://api.anthropic.com/v1/models", "x-api-key", "{key}"),
    "gemini": (
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/models",
        "x-goog-api-key",
        "{key}",
    ),
}


class ByokError(Exception):
    """User-facing problem with a BYOK credential."""


def normalize_provider(provider: str) -> str:
    name = (provider or "").strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise ByokError(f"Провайдер {provider!r} не поддерживается")
    return name


def _last4(raw: str) -> str:
    cleaned = raw.strip()
    return cleaned[-4:] if len(cleaned) >= 4 else cleaned


def validate_key(provider: str, raw_key: str, *, timeout: float = 10.0) -> tuple[bool, str | None]:
    """Ask the provider whether the key works. Returns (is_valid, error_message)."""
    name = normalize_provider(provider)
    spec = _VALIDATION.get(name)
    if spec is None:
        return False, "Проверка для этого провайдера недоступна"
    method, url, header, template = spec
    try:
        response = httpx.request(
            method, url, headers={header: template.format(key=raw_key)}, timeout=timeout
        )
    except httpx.HTTPError as exc:
        # Never log the key itself - only the transport failure.
        logger.warning("BYOK validation transport error for %s: %s", name, type(exc).__name__)
        return False, "Не удалось связаться с провайдером"
    if response.status_code == 200:
        return True, None
    if response.status_code in (401, 403):
        return False, "Ключ отклонён провайдером"
    return False, f"Провайдер ответил {response.status_code}"


def get_credential(db: Session, user: User, provider: str) -> UserProviderCredential | None:
    name = normalize_provider(provider)
    return (
        db.query(UserProviderCredential)
        .filter(
            UserProviderCredential.user_id == user.id,
            UserProviderCredential.provider == name,
        )
        .first()
    )


def list_credentials(db: Session, user: User) -> list[UserProviderCredential]:
    return (
        db.query(UserProviderCredential)
        .filter(UserProviderCredential.user_id == user.id)
        .order_by(UserProviderCredential.provider)
        .all()
    )


def upsert_credential(
    db: Session, user: User, provider: str, raw_key: str
) -> UserProviderCredential:
    """Store (or replace) a user's key for a provider, validating it first."""
    name = normalize_provider(provider)
    cleaned = (raw_key or "").strip()
    if not cleaned:
        raise ByokError("Ключ не может быть пустым")

    is_valid, error = validate_key(name, cleaned)

    row = get_credential(db, user, name)
    if row is None:
        row = UserProviderCredential(user_id=user.id, provider=name)
    row.encrypted_key = encrypt_secret(cleaned)
    row.last4 = _last4(cleaned)
    row.is_valid = is_valid
    row.last_error = error
    if is_valid:
        from datetime import UTC, datetime

        row.validated_at = datetime.now(UTC)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_credential(db: Session, user: User, provider: str) -> bool:
    row = get_credential(db, user, provider)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def revalidate_credential(db: Session, user: User, provider: str) -> UserProviderCredential:
    row = get_credential(db, user, provider)
    if row is None:
        raise ByokError("Ключ не найден")
    raw = decrypt_secret(row.encrypted_key)
    is_valid, error = validate_key(row.provider, raw)
    row.is_valid = is_valid
    row.last_error = error
    if is_valid:
        from datetime import UTC, datetime

        row.validated_at = datetime.now(UTC)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_turn_api_key(db: Session, user: User, provider: str) -> str | None:
    """BYOK first, then the platform key for this provider."""
    from src.services.system_settings import resolve_platform_api_key

    return resolve_user_api_key(db, user, provider) or resolve_platform_api_key(provider)


def resolve_user_api_key(db: Session, user: User, provider: str) -> str | None:
    """The user's own key for `provider`, or None to fall back to the platform key."""
    try:
        row = get_credential(db, user, provider)
    except ByokError:
        return None
    if row is None or not row.is_valid:
        return None
    try:
        return decrypt_secret(row.encrypted_key)
    except Exception:
        # A key encrypted under a rotated APP_ENCRYPTION_KEY must not break chat - fall back to
        # the platform key and let the user re-enter theirs.
        logger.warning("Could not decrypt BYOK credential %s for provider %s", row.id, provider)
        return None


def has_valid_key(db: Session, user: User, provider: str) -> bool:
    try:
        row = get_credential(db, user, provider)
    except ByokError:
        return False
    return bool(row and row.is_valid)


def credential_response(row: UserProviderCredential) -> dict:
    """Public shape. Deliberately never includes the key itself."""
    return {
        "id": str(row.id),
        "provider": row.provider,
        "last4": row.last4,
        "is_valid": row.is_valid,
        "validated_at": row.validated_at,
        "last_error": row.last_error,
        "created_at": row.created_at,
    }


def credential_id(row: UserProviderCredential) -> uuid.UUID:
    return row.id
