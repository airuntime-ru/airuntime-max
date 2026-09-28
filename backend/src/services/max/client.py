"""Thin client for the MAX Bot API (https://platform-api2.max.ru).

Only the handful of calls the product actually makes. Notes that cost time to rediscover:

- the token goes in an ``Authorization`` header; passing it as a query parameter was
  removed by the platform;
- production updates must arrive by webhook (``POST /subscriptions``) over HTTPS with a
  certificate from a trusted CA - long polling is documented as development-only;
- the platform rate-limits to 30 rps, so a call is at most two requests, never a retry
  storm: connecting to platform-api2.max.ru fails intermittently (the name resolves to
  several nodes and the handshake occasionally just hangs), and one retry turns that from
  a silently undelivered lead into a 200ms hiccup.

Every method is fail-soft: a delivery problem must never take down the webhook handler,
because MAX retries deliveries and a 500 from us just amplifies the load.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# Retried only for failures that prove the request never reached MAX. A ReadTimeout is
# deliberately excluded: the platform may already have acted on it, and re-sending would
# duplicate a message in someone's chat.
_CONNECT_FAILURES = (httpx.ConnectError, httpx.ConnectTimeout)
_CONNECT_ATTEMPTS = 2

# Where the Ministry bundle can be found, most specific first. See infra/certs/README.md
# for provenance and fingerprints.
_CA_CANDIDATES = (
    # Copied in by backend/Dockerfile.
    Path("/app/certs/russian-trusted-ca.crt"),
    # Running from a checkout: local uvicorn, scripts/max_setup.py, tests.
    Path(__file__).resolve().parents[4] / "infra" / "certs" / "russian-trusted-ca.crt",
    # Last resort: a system store somebody ran update-ca-certificates against.
    Path("/etc/ssl/certs/ca-certificates.crt"),
)


def resolve_ca_bundle(explicit: str | None = None) -> str | bool:
    """What to hand httpx as ``verify``.

    Note what does *not* work: installing the Ministry CA into the system trust store.
    httpx verifies against certifi's bundle, so ``update-ca-certificates`` reaches curl and
    openssl but never this client - the handshake just times out. The bundle has to be named
    explicitly, which is why the Dockerfile also drops it at a fixed path.

    Trusting only the Ministry chain is deliberate: this client talks to *.max.ru and
    nothing else, so a narrower trust anchor is the tighter choice.
    """
    if explicit and os.path.exists(explicit):
        return explicit
    for candidate in _CA_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return True


class MaxApiError(RuntimeError):
    """A failed call. ``status`` is MAX's HTTP status when it answered at all, and None
    when the request never got an answer - the difference between "MAX refused this" and
    "MAX may or may not have done it", which decides whether a retry is safe."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class MaxBotProfile:
    user_id: int
    name: str
    username: str

    @property
    def public_url(self) -> str:
        return f"https://max.ru/{self.username}" if self.username else ""


def button_callback(text: str, payload: str) -> dict[str, Any]:
    return {"type": "callback", "text": text, "payload": payload}


def button_link(text: str, url: str) -> dict[str, Any]:
    return {"type": "link", "text": text, "url": url}


def button_open_app(
    text: str, bot_username: str, payload: str = "", *, contact_id: int | None = None
) -> dict[str, Any]:
    """Opens the bot's mini app - the one button that makes this a MAX product.

    ``web_app`` names the *bot* the mini app is wired to; it is not an address. The address
    is set once per bot in "MAX для партнёров" (Чат-боты -> ⋮ -> Настройки), and what the
    app should show travels in ``payload``, which arrives as ``start_param`` - the same
    value a ``max.ru/<bot>?startapp=<payload>`` link carries.

    Source: OpenAppButton in the published OpenAPI schema
    (github.com/max-messenger/api-schema): ``web_app`` is required, ``payload`` matches
    ``^[\\w-]*$`` and is at most 512 characters. The API does not validate ``web_app`` when
    the message is sent - a URL there is accepted without complaint and then opens nothing
    when tapped, which is exactly how an earlier version of this button shipped.
    """
    button: dict[str, Any] = {"type": "open_app", "text": text, "web_app": bot_username}
    if contact_id:
        button["contact_id"] = contact_id
    if payload:
        button["payload"] = payload
    return button


def button_request_contact(text: str) -> dict[str, Any]:
    return {"type": "request_contact", "text": text}


def inline_keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


class MaxBotClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://platform-api2.max.ru",
        ca_bundle: str | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._verify = resolve_ca_bundle(ca_bundle)

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._token:
            raise MaxApiError("MAX bot token is not configured")
        last_connect_error: Exception | None = None
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            try:
                response = httpx.request(
                    method,
                    f"{self._base_url}{path}",
                    headers={"Authorization": self._token},
                    timeout=REQUEST_TIMEOUT,
                    verify=self._verify,
                    **kwargs,
                )
                break
            except _CONNECT_FAILURES as exc:
                last_connect_error = exc
                logger.warning(
                    "max_api_connect_failed attempt=%s/%s path=%s %s",
                    attempt,
                    _CONNECT_ATTEMPTS,
                    path,
                    type(exc).__name__,
                )
            except httpx.HTTPError as exc:
                raise MaxApiError(f"MAX API request failed: {exc}") from exc
        else:
            raise MaxApiError(
                f"MAX API unreachable after {_CONNECT_ATTEMPTS} attempts: {last_connect_error}"
            ) from last_connect_error

        if response.status_code >= 400:
            # The body carries the platform's own reason; keep it, it is the only way to
            # tell "bad token" from "chat not found" without guessing.
            raise MaxApiError(
                f"MAX API {response.status_code}: {response.text[:300]}",
                status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def get_me(self) -> MaxBotProfile:
        payload = self._request("GET", "/me")
        return MaxBotProfile(
            user_id=int(payload.get("user_id") or 0),
            name=str(payload.get("name") or ""),
            username=str(payload.get("username") or ""),
        )

    def set_commands(self, commands: list[dict[str, str]]) -> None:
        self._request("PATCH", "/me/commands", json={"commands": commands})

    def subscribe_webhook(self, url: str, *, update_types: list[str] | None = None) -> None:
        body: dict[str, Any] = {"url": url}
        if update_types:
            body["update_types"] = update_types
        self._request("POST", "/subscriptions", json=body)

    def list_subscriptions(self) -> dict[str, Any]:
        return self._request("GET", "/subscriptions")

    def delete_subscription(self, url: str) -> None:
        self._request("DELETE", "/subscriptions", params={"url": url})

    def send_message(
        self,
        *,
        text: str,
        chat_id: int | None = None,
        user_id: int | None = None,
        buttons: list[list[dict[str, Any]]] | None = None,
        image_url: str = "",
        html: bool = False,
    ) -> None:
        """Send to a chat, or straight to a user - exactly one of the two.

        ``user_id`` is how a bot reaches someone whose dialog it has never seen, such as a
        customer who only opened the mini app. It is not interchangeable with ``chat_id``:
        a user id passed as a chat id is answered with ``chat.not.found``.

        ``html`` formats the text as MAX's HTML subset; callers escape anything a user
        typed. HTML rather than markdown because a customer called ``Иван_1`` would
        otherwise turn half the message italic.
        """
        if (chat_id is None) == (user_id is None):
            raise ValueError("send_message needs exactly one of chat_id and user_id")
        attachments: list[dict[str, Any]] = []
        if image_url:
            attachments.append({"type": "image", "payload": {"url": image_url}})
        if buttons:
            attachments.append(inline_keyboard(buttons))
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if html:
            body["format"] = "html"
        # The recipient is a query parameter for POST /messages; the token stays in the header.
        params = {"chat_id": chat_id} if chat_id is not None else {"user_id": user_id}
        self._request("POST", "/messages", params=params, json=body)

    def try_send_message(self, **kwargs: Any) -> bool:
        """Send and swallow failures - used on paths where delivery is best-effort."""
        try:
            self.send_message(**kwargs)
            return True
        except MaxApiError:
            logger.warning(
                "max_send_failed chat_id=%s user_id=%s",
                kwargs.get("chat_id"),
                kwargs.get("user_id"),
                exc_info=True,
            )
            return False

    def answer_callback(self, callback_id: str, *, notification: str = "") -> None:
        body: dict[str, Any] = {}
        if notification:
            body["notification"] = notification
        try:
            self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)
        except MaxApiError:
            # A stale callback id is normal (the user tapped an old message); never surface it.
            logger.debug("max_answer_callback_failed", exc_info=True)

    def send_action(self, chat_id: int, action: str) -> None:
        self._request("POST", f"/chats/{chat_id}/actions", json={"action": action})

    def mark_seen(self, chat_id: int) -> None:
        """Turn the user's ticks from sent to read.

        ``mark_seen`` is a SenderAction on ``POST /chats/{chatId}/actions``. The
        published docs title that method as a group-chat action, but the OpenAPI
        schema lists it and dialogs use it the same way. Fail-soft: a grey tick is
        worse than none, but never worse than a 500 webhook.
        """
        try:
            self.send_action(chat_id, "mark_seen")
        except MaxApiError:
            logger.debug("max_mark_seen_failed chat_id=%s", chat_id, exc_info=True)
