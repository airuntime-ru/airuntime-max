from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, token: str, timeout: float = 35.0) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.post(f"{self.base}/{method}", json=payload or {})
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error ({method}): {data}")
        return data["result"]

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        message_thread_id: int | None = None,
        parse_mode: str = "HTML",
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        return self._call("sendMessage", payload)
