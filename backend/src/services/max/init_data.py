"""Verification of the launch parameters MAX hands a mini app.

This is the only thing standing between "a customer opened our storefront in MAX" and
"anyone with curl can post leads as anyone". The mini app sends ``window.WebApp.initData``
verbatim; nothing downstream may trust a user id that did not come out of here.

The algorithm is MAX's (docs: «Валидация данных»), and it differs from Telegram's in the
key derivation - the string ``WebAppData`` is the HMAC *key* and the bot token is the
*message*, which is the opposite of what muscle memory suggests:

    secret_key   = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)
    launch_params= "\\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")
    expected     = hex(HMAC_SHA256(key=secret_key, msg=launch_params))
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import unquote

logger = logging.getLogger(__name__)

SECRET_KEY_SEED = b"WebAppData"

# MAX documents an hour as the recommended validity window for auth_date.
DEFAULT_MAX_AGE_SECONDS = 3600


class InitDataError(ValueError):
    """The launch parameters are absent, malformed, forged or stale."""


@dataclass(frozen=True)
class MaxLaunchContext:
    user_id: int
    first_name: str
    last_name: str
    username: str
    chat_id: int | None
    chat_type: str
    start_param: str
    auth_date: int

    @property
    def display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.username or f"id{self.user_id}"


def _parse_pairs(init_data: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for chunk in init_data.split("&"):
        if not chunk:
            continue
        # Values arrive percent-encoded, so a literal "=" inside one cannot appear raw;
        # splitting once is both correct and safe against a value that somehow contains it.
        key, _, raw_value = chunk.partition("=")
        pairs.append((key, unquote(raw_value)))
    return pairs


def _expected_hash(launch_params: str, bot_token: str) -> str:
    secret_key = hmac.new(SECRET_KEY_SEED, bot_token.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(secret_key, launch_params.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> MaxLaunchContext:
    """Return the authenticated launch context, or raise ``InitDataError``."""
    if not bot_token:
        raise InitDataError("MAX bot token is not configured")
    if not init_data or not init_data.strip():
        raise InitDataError("Launch parameters are missing")

    pairs = _parse_pairs(init_data.strip())
    hashes = [value for key, value in pairs if key == "hash"]
    if len(hashes) != 1:
        # Exactly once, per the spec: a duplicated hash is how you smuggle a second value past
        # a naive dict-based parser.
        raise InitDataError("Launch parameters must carry exactly one hash")
    received_hash = hashes[0]

    launch_params = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs, key=lambda pair: pair[0]) if key != "hash"
    )
    if not hmac.compare_digest(_expected_hash(launch_params, bot_token), received_hash):
        raise InitDataError("Launch parameters failed signature verification")

    values = dict(pairs)

    try:
        auth_date = int(values.get("auth_date") or 0)
    except ValueError as exc:
        raise InitDataError("Launch parameters carry a malformed auth_date") from exc
    if max_age_seconds > 0 and (auth_date <= 0 or time.time() - auth_date > max_age_seconds):
        raise InitDataError("Launch parameters have expired")

    user = _json_object(values.get("user"))
    user_id = user.get("id")
    if not isinstance(user_id, int) or user_id <= 0:
        raise InitDataError("Launch parameters carry no user id")

    chat = _json_object(values.get("chat"))
    chat_id = chat.get("id") if isinstance(chat.get("id"), int) else None

    return MaxLaunchContext(
        user_id=user_id,
        first_name=_text(user.get("first_name")),
        last_name=_text(user.get("last_name")),
        username=_text(user.get("username")),
        chat_id=chat_id,
        chat_type=_text(chat.get("type")),
        start_param=_text(values.get("start_param")),
        auth_date=auth_date,
    )


def verify_contact_hash(
    *,
    auth_date: str,
    phone: str,
    user_id: int,
    received_hash: str,
    bot_token: str,
) -> bool:
    """Check a phone number obtained via ``WebApp.requestContact()``.

    A different scheme from the launch parameters: the bot token itself is the key, and the
    signed string is the three fields in alphabetical order.
    """
    if not bot_token or not received_hash:
        return False
    payload = "\n".join(
        [
            f"authDate={auth_date}",
            f"phone={phone}",
            f"userId={user_id}",
        ]
    )
    expected = hmac.new(
        bot_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received_hash)


def _json_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
