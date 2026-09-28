from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _required(*names: str) -> str:
    value = _first_env(*names)
    if not value:
        raise RuntimeError(f"Missing required env var (one of): {', '.join(names)}")
    return value


def _optional_int(*names: str) -> int | None:
    raw = _first_env(*names)
    if not raw:
        return None
    return int(raw)


def _csv_strs(default: str, *names: str) -> tuple[str, ...]:
    raw = _first_env(*names) or default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _normalize_chat_id(raw: str) -> str:
    """Telegram web links often show #-4484…; Bot API needs -1004484… for supergroups."""
    value = raw.strip()
    if value.startswith("-100") or not value.lstrip("-").isdigit():
        return value
    if value.startswith("-"):
        return f"-100{value[1:]}"
    return f"-100{value}"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    chat_id: str
    topic_code: int | None
    topic_clients: int | None
    database_url: str
    poll_interval_sec: int
    log_check_interval_sec: int
    health_check_interval_sec: int
    stuck_check_interval_sec: int
    routerai_balance_check_interval_sec: int
    error_alert_threshold: int
    stuck_run_after_hours: int
    monitor_containers: tuple[str, ...]
    health_check_urls: tuple[str, ...]
    routerai_api_key: str
    routerai_base_url: str
    routerai_balance_report_hour: int
    routerai_low_balance_rub: float
    state_path: Path


def load_settings() -> Settings:
    state_dir = Path(os.getenv("STATE_DIR", "/var/lib/ops-bot"))
    database_url = _first_env("DATABASE_URL") or (
        "postgresql://airuntime:airuntime@postgres:5432/airuntime"
    )
    return Settings(
        bot_token=_required("AIRUNTIME_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
        chat_id=_normalize_chat_id(
            _required("AIRUNTIME_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID")
        ),
        topic_code=_optional_int("AIRUNTIME_TELEGRAM_TOPIC_CODE", "TELEGRAM_TOPIC_CODE"),
        topic_clients=_optional_int(
            "AIRUNTIME_TELEGRAM_TOPIC_CLIENTS", "TELEGRAM_TOPIC_CLIENTS"
        ),
        database_url=database_url.replace("postgresql+psycopg://", "postgresql://"),
        poll_interval_sec=int(_first_env("OPS_BOT_POLL_INTERVAL_SEC", "POLL_INTERVAL_SEC") or "30"),
        log_check_interval_sec=int(
            _first_env("LOG_CHECK_INTERVAL_SEC") or "600"
        ),
        health_check_interval_sec=int(
            _first_env("HEALTH_CHECK_INTERVAL_SEC") or "120"
        ),
        stuck_check_interval_sec=int(
            _first_env("STUCK_CHECK_INTERVAL_SEC") or "600"
        ),
        routerai_balance_check_interval_sec=int(
            _first_env("ROUTERAI_BALANCE_CHECK_INTERVAL_SEC") or "3600"
        ),
        error_alert_threshold=int(_first_env("ERROR_ALERT_THRESHOLD") or "20"),
        stuck_run_after_hours=int(_first_env("STUCK_RUN_AFTER_HOURS") or "2"),
        monitor_containers=_csv_strs(
            "airuntime-backend-1,airuntime-worker-1,airuntime-django-admin-1,airuntime-frontend-1",
            "MONITOR_CONTAINERS",
        ),
        health_check_urls=_csv_strs(
            "https://api.airuntime.ru/health,https://admin.airuntime.ru/,https://airuntime.ru/",
            "HEALTH_CHECK_URLS",
        ),
        routerai_api_key=_first_env("ROUTERAI_API_KEY", "OPENAI_API_KEY"),
        routerai_base_url=_first_env("OPENAI_BASE_URL") or "https://routerai.ru/api/v1",
        routerai_balance_report_hour=int(
            _first_env("ROUTERAI_BALANCE_REPORT_HOUR") or "20"
        ),
        routerai_low_balance_rub=float(_first_env("ROUTERAI_LOW_BALANCE_RUB") or "100"),
        state_path=state_dir / "state.json",
    )
