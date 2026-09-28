from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from ops_bot.config import Settings
from ops_bot.monitors import OpsMonitor
from ops_bot.routerai_balance import fetch_balance_rub, format_rub, parse_balance_rub
from ops_bot.state import StateStore


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"data": {"total_credits": 500.0, "total_usage": 123.5}}, 376.5),
        ({"data": {"balance": 42.0}}, 42.0),
        ({"balance": 99.99}, 99.99),
        ({"data": {"remaining_balance": 10}}, 10.0),
        ({}, None),
    ],
)
def test_parse_balance_rub(payload: dict, expected: float | None) -> None:
    assert parse_balance_rub(payload) == expected


def test_format_rub() -> None:
    assert format_rub(100) == "100 ₽"
    assert format_rub(1234.5) == "1\u00a0234,50 ₽"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="token",
        chat_id="-100123",
        topic_code=3,
        topic_clients=4,
        database_url="postgresql://x",
        poll_interval_sec=30,
        log_check_interval_sec=600,
        health_check_interval_sec=120,
        stuck_check_interval_sec=600,
        routerai_balance_check_interval_sec=3600,
        error_alert_threshold=20,
        stuck_run_after_hours=2,
        monitor_containers=(),
        health_check_urls=(),
        routerai_api_key="test-key",
        routerai_base_url="https://routerai.ru/api/v1",
        routerai_balance_report_hour=20,
        routerai_low_balance_rub=100.0,
        state_path=tmp_path / "state.json",
    )


def test_check_routerai_balance_daily_and_low_alert_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ops_bot.monitors.docker.from_env", lambda: None)
    settings = _settings(tmp_path)
    tg = MagicMock()
    state = StateStore(settings.state_path)
    monitor = OpsMonitor(settings, tg, state)

    monkeypatch.setattr(
        "ops_bot.monitors.fetch_balance_rub",
        lambda *_args, **_kwargs: 87.0,
    )
    evening = datetime(2026, 8, 30, 21, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("ops_bot.monitors.datetime", MagicMock(now=lambda *_a, **_k: evening))

    monitor.check_routerai_balance()
    assert tg.send_message.call_count == 2
    assert state.get("routerai_last_balance_report_date") == "2026-08-30"
    assert state.get("routerai_low_balance_alerted") is True

    tg.send_message.reset_mock()
    monitor.check_routerai_balance()
    tg.send_message.assert_not_called()

    monkeypatch.setattr(
        "ops_bot.monitors.fetch_balance_rub",
        lambda *_args, **_kwargs: 150.0,
    )
    monitor.check_routerai_balance()
    assert state.get("routerai_low_balance_alerted") is False

    monkeypatch.setattr(
        "ops_bot.monitors.fetch_balance_rub",
        lambda *_args, **_kwargs: 50.0,
    )
    tg.send_message.reset_mock()
    monitor.check_routerai_balance()
    assert tg.send_message.call_count == 1
    assert state.get("routerai_low_balance_alerted") is True


def test_check_routerai_balance_skips_daily_before_report_hour(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ops_bot.monitors.docker.from_env", lambda: None)
    settings = _settings(tmp_path)
    tg = MagicMock()
    monitor = OpsMonitor(settings, tg, StateStore(settings.state_path))

    monkeypatch.setattr(
        "ops_bot.monitors.fetch_balance_rub",
        lambda *_args, **_kwargs: 500.0,
    )
    morning = datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("ops_bot.monitors.datetime", MagicMock(now=lambda *_a, **_k: morning))

    monitor.check_routerai_balance()
    tg.send_message.assert_not_called()


def test_check_routerai_balance_alerts_after_repeated_failures_and_recovers(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("ops_bot.monitors.docker.from_env", lambda: None)
    settings = _settings(tmp_path)
    tg = MagicMock()
    state = StateStore(settings.state_path)
    monitor = OpsMonitor(settings, tg, state)
    morning = datetime(2026, 9, 16, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("ops_bot.monitors.datetime", MagicMock(now=lambda *_a, **_k: morning))
    balances = iter([None, None, None, None, 500.0])
    monkeypatch.setattr(
        "ops_bot.monitors.fetch_balance_rub",
        lambda *_args, **_kwargs: next(balances),
    )

    for _ in range(2):
        monitor.check_routerai_balance()
    tg.send_message.assert_not_called()

    monitor.check_routerai_balance()
    assert tg.send_message.call_count == 1
    assert "не удаётся проверить баланс" in tg.send_message.call_args.args[1]
    assert state.get("routerai_balance_unavailable_alerted") is True

    monitor.check_routerai_balance()
    assert tg.send_message.call_count == 1

    monitor.check_routerai_balance()
    assert tg.send_message.call_count == 2
    assert "восстановилась" in tg.send_message.call_args.args[1]
    assert state.get("routerai_balance_consecutive_failures") == 0
    assert state.get("routerai_balance_unavailable_alerted") is False


def test_fetch_balance_rub_parses_openrouter_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/credits"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={"data": {"total_credits": 200.0, "total_usage": 50.25}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        balance = fetch_balance_rub(
            client,
            api_key="secret",
            base_url="https://routerai.ru/api/v1",
        )
    assert balance == pytest.approx(149.75)
