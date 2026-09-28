from __future__ import annotations

import logging
import signal
import sys
import time

from ops_bot.config import load_settings
from ops_bot.monitors import OpsMonitor
from ops_bot.state import StateStore
from ops_bot.telegram_api import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("ops-bot")


def main() -> None:
    settings = load_settings()
    tg = TelegramClient(settings.bot_token)
    state = StateStore(settings.state_path)
    monitor = OpsMonitor(settings, tg, state)
    monitor.bootstrap_cursors()

    stop = False

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    logger.info(
        "Started (chat=%s topic=%s containers=%d urls=%d)",
        settings.chat_id,
        settings.topic_code,
        len(settings.monitor_containers),
        len(settings.health_check_urls),
    )

    now = time.monotonic()
    last_log_check = now
    last_health_check = now
    last_stuck_check = now
    last_business_poll = now
    last_routerai_balance_check = now

    try:
        while not stop:
            now = time.monotonic()

            if now - last_business_poll >= settings.poll_interval_sec:
                try:
                    monitor.poll_business_events()
                except Exception:
                    logger.exception("Business poll failed")
                last_business_poll = now

            if now - last_log_check >= settings.log_check_interval_sec:
                try:
                    monitor.check_container_errors()
                except Exception:
                    logger.exception("Log check failed")
                last_log_check = now

            if now - last_health_check >= settings.health_check_interval_sec:
                try:
                    monitor.check_health_urls()
                except Exception:
                    logger.exception("Health check failed")
                last_health_check = now

            if now - last_stuck_check >= settings.stuck_check_interval_sec:
                try:
                    monitor.check_stuck_orchestration_runs()
                    monitor.check_orchestration_internal_errors()
                except Exception:
                    logger.exception("Stuck runs check failed")
                last_stuck_check = now

            if (
                settings.routerai_api_key
                and now - last_routerai_balance_check
                >= settings.routerai_balance_check_interval_sec
            ):
                try:
                    monitor.check_routerai_balance()
                except Exception:
                    logger.exception("RouterAI balance check failed")
                last_routerai_balance_check = now

            time.sleep(min(settings.poll_interval_sec, 5))
    finally:
        monitor.close()
        tg.close()
        logger.info("Stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Fatal startup error")
        sys.exit(1)
