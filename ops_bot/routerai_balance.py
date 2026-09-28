from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ROUTERAI_DEFAULT_BASE_URL = "https://routerai.ru/api/v1"


def parse_balance_rub(payload: dict[str, Any]) -> float | None:
    """Parse RouterAI / OpenRouter-compatible credits response into remaining rubles."""
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("balance", "remaining_balance", "remaining_credits", "credits"):
            if key in data and data[key] is not None:
                return float(data[key])
        total_credits = data.get("total_credits")
        total_usage = data.get("total_usage")
        if total_credits is not None and total_usage is not None:
            return float(total_credits) - float(total_usage)

    for key in ("balance", "remaining_balance", "remaining_credits", "credits"):
        if key in payload and payload[key] is not None:
            return float(payload[key])

    return None


def fetch_balance_rub(
    client: httpx.Client,
    *,
    api_key: str,
    base_url: str,
    timeout: float = 15.0,
) -> float | None:
    url = f"{base_url.rstrip('/')}/credits"
    try:
        response = client.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        logger.exception("RouterAI balance request failed")
        return None

    if response.status_code == 401:
        logger.warning("RouterAI balance: API key rejected (401)")
        return None
    if response.status_code >= 400:
        logger.warning(
            "RouterAI balance: HTTP %s from %s",
            response.status_code,
            url,
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("RouterAI balance: invalid JSON from %s", url)
        return None

    if not isinstance(payload, dict):
        logger.warning("RouterAI balance: unexpected payload type %s", type(payload).__name__)
        return None

    balance = parse_balance_rub(payload)
    if balance is None:
        logger.warning("RouterAI balance: could not parse response keys %s", sorted(payload))
    return balance


def format_rub(amount: float) -> str:
    rounded = round(amount, 2)
    if rounded == int(rounded):
        text = f"{int(rounded):,}".replace(",", "\u00a0")
        return f"{text} ₽"
    whole, frac = f"{rounded:.2f}".split(".")
    whole = f"{int(whole):,}".replace(",", "\u00a0")
    return f"{whole},{frac} ₽"
