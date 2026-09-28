from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx


class TelegramProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramBotProfile:
    username: str
    name: str = ""
    description: str = ""
    short_description: str = ""

    @property
    def public_url(self) -> str:
        return f"https://t.me/{quote(self.username)}"


def _request(token: str, method: str, **kwargs) -> dict:
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}",
            timeout=20.0,
            **kwargs,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramProfileError("Telegram bot API request failed") from exc

    if not payload.get("ok"):
        description = payload.get("description")
        raise TelegramProfileError(
            str(description) if description else "Telegram bot API request was rejected"
        )
    return payload


def fetch_bot_profile(token: str) -> TelegramBotProfile:
    try:
        payload = _request(token, "getMe")
    except TelegramProfileError as exc:
        raise TelegramProfileError("Telegram bot token could not be verified") from exc

    result = payload.get("result")
    username = result.get("username") if isinstance(result, dict) else None
    if not isinstance(username, str) or not username.strip():
        raise TelegramProfileError("Telegram bot username is missing")

    return TelegramBotProfile(username=username.strip())


def fetch_bot_settings(token: str) -> TelegramBotProfile:
    profile = fetch_bot_profile(token)

    name_payload = _request(token, "getMyName")
    description_payload = _request(token, "getMyDescription")
    short_description_payload = _request(token, "getMyShortDescription")

    name_result = name_payload.get("result") if isinstance(name_payload, dict) else {}
    description_result = (
        description_payload.get("result") if isinstance(description_payload, dict) else {}
    )
    short_description_result = (
        short_description_payload.get("result")
        if isinstance(short_description_payload, dict)
        else {}
    )

    return TelegramBotProfile(
        username=profile.username,
        name=(name_result.get("name") or "") if isinstance(name_result, dict) else "",
        description=(description_result.get("description") or "")
        if isinstance(description_result, dict)
        else "",
        short_description=(short_description_result.get("short_description") or "")
        if isinstance(short_description_result, dict)
        else "",
    )


def update_bot_settings(
    token: str,
    *,
    name: str | None = None,
    description: str | None = None,
    short_description: str | None = None,
) -> TelegramBotProfile:
    if name is not None:
        _request(token, "setMyName", json={"name": name})
    if description is not None:
        _request(token, "setMyDescription", json={"description": description})
    if short_description is not None:
        _request(
            token,
            "setMyShortDescription",
            json={"short_description": short_description},
        )
    return fetch_bot_settings(token)


def update_bot_profile_photo(
    token: str,
    *,
    filename: str,
    content: bytes,
    content_type: str,
) -> TelegramBotProfile:
    if not content:
        raise TelegramProfileError("Telegram bot avatar file is empty")

    photo = {"type": "static", "photo": "attach://photo"}
    _request(
        token,
        "setMyProfilePhoto",
        data={"photo": json.dumps(photo)},
        files={"photo": (filename, content, content_type)},
    )
    return fetch_bot_settings(token)
