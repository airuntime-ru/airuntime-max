import base64
import hashlib
import re

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.project import Project
from src.db.models.secret import Secret


def _get_fernet() -> Fernet:
    if settings.app_encryption_key:
        key_bytes = settings.app_encryption_key.encode("utf-8")
        try:
            return Fernet(key_bytes)
        except ValueError:
            # Keep existing deployments working even when APP_ENCRYPTION_KEY
            # was provided in a non-Fernet format.
            derived = hashlib.sha256(key_bytes).digest()
            fallback_key = base64.urlsafe_b64encode(derived)
            return Fernet(fallback_key)
    derived = hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()
    fallback_key = base64.urlsafe_b64encode(derived)
    return Fernet(fallback_key)


def encrypt_secret(raw: str) -> str:
    token = _get_fernet().encrypt(raw.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(encrypted: str) -> str:
    raw = _get_fernet().decrypt(encrypted.encode("utf-8"))
    return raw.decode("utf-8")


TELEGRAM_BOT_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"

# Keyword-based matching (not just alias sets) so loosely/differently named keys - including
# Cyrillic ones like "токен" or "мой бот токен" - still resolve to the canonical env var name
# an agent-generated bot actually reads (os.environ["TELEGRAM_BOT_TOKEN"]).
_TELEGRAM_MARKERS = ("telegram", "телеграм", "tg")
_TOKEN_MARKERS = ("token", "токен", "ключ", "key")
_BOT_MARKERS = ("bot", "бот")


def normalize_secret_key(value: str, *, project_type: str | None = None) -> str:
    raw = value.strip()
    lowered = raw.lower()
    has_telegram = any(marker in lowered for marker in _TELEGRAM_MARKERS)
    has_token = any(marker in lowered for marker in _TOKEN_MARKERS)
    has_bot = any(marker in lowered for marker in _BOT_MARKERS)
    looks_like_telegram_token = has_telegram and has_token
    looks_like_bot_token = has_bot and has_token
    # A bare "token"/"токен"/"ключ" only means the Telegram token when the project itself is a
    # Telegram bot (otherwise it's ambiguous - e.g. a website's payment provider key).
    bare_token = lowered in {"token", "токен", "ключ", "key"}
    if (
        looks_like_telegram_token
        or looks_like_bot_token
        or (bare_token and project_type in ("telegram_bot", "mixed"))
    ):
        return TELEGRAM_BOT_TOKEN_KEY
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", raw.upper()).strip("_")
    return normalized or "SECRET"


def looks_like_telegram_token(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", value.strip()))


_TELEGRAM_TOKEN_IN_TEXT = re.compile(r"\b(\d{6,}:[A-Za-z0-9_-]{20,})\b")


def extract_telegram_bot_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(_TELEGRAM_TOKEN_IN_TEXT.findall(text or "")))


def redact_telegram_bot_tokens(text: str) -> str:
    return _TELEGRAM_TOKEN_IN_TEXT.sub("[TELEGRAM_BOT_TOKEN]", text or "")


def store_telegram_bot_token(
    db: Session,
    project: Project,
    token: str,
    *,
    reason: str = "Токен из чата для запуска Telegram-бота",
) -> Secret | None:
    """Persist a BotFather token pasted into chat. Returns None if the value is not a token."""
    cleaned = (token or "").strip()
    if not looks_like_telegram_token(cleaned):
        return None
    secret, _created = ensure_secret_placeholder(db, project, TELEGRAM_BOT_TOKEN_KEY, reason)
    secret.encrypted_value = encrypt_secret(cleaned)
    if not secret.reason:
        secret.reason = reason
    db.add(secret)
    # Best-effort: refresh public bot URL when Telegram accepts the token.
    try:
        from src.services.telegram_profile import fetch_bot_profile

        profile = fetch_bot_profile(cleaned)
        if project.type in ("telegram_bot", "mixed"):
            project.deployment_url = profile.public_url
            db.add(project)
    except Exception:
        pass
    db.commit()
    db.refresh(secret)
    return secret


def capture_telegram_tokens_from_text(db: Session, project: Project, text: str) -> str:
    """Save any BotFather tokens found in text and return the same text with tokens redacted."""
    tokens = extract_telegram_bot_tokens(text)
    if not tokens:
        return text
    for token in tokens:
        store_telegram_bot_token(db, project, token)
    return redact_telegram_bot_tokens(text)


def all_secrets_filled(db: Session, project: Project) -> bool:
    missing = (
        db.query(Secret)
        .filter(Secret.project_id == project.id, Secret.encrypted_value.is_(None))
        .first()
    )
    return missing is None


def ensure_secret_placeholder(
    db: Session, project: Project, key: str, reason: str = ""
) -> tuple[Secret, bool]:
    """Create an empty (value-less) secret slot if one doesn't already exist for this key.

    Returns (secret, created). Never overwrites an existing value or an existing reason with
    an empty one - safe to call repeatedly (e.g. once per agent turn).
    """
    normalized_key = normalize_secret_key(key, project_type=project.type)
    existing = (
        db.query(Secret)
        .filter(Secret.project_id == project.id, Secret.key == normalized_key)
        .first()
    )
    if existing:
        if reason and not existing.reason:
            existing.reason = reason
            db.add(existing)
            db.commit()
        return existing, False
    secret = Secret(project_id=project.id, key=normalized_key, reason=reason or None)
    db.add(secret)
    db.commit()
    db.refresh(secret)
    return secret, True
