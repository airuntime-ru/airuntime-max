from __future__ import annotations

import hashlib
import html
import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import docker
import httpx
import psycopg
from docker.errors import DockerException, NotFound

from ops_bot.config import Settings
from ops_bot.db import SupportEvent, UserEvent, fetch_support_messages_since, fetch_users_since
from ops_bot.routerai_balance import fetch_balance_rub, format_rub
from ops_bot.state import StateStore
from ops_bot.telegram_api import TelegramClient

logger = logging.getLogger(__name__)

ERROR_RE = re.compile(
    r"(?:^|[\s\[])(?:ERROR|CRITICAL|FATAL|PANIC)(?:[\s\]:,]|$)|"
    r"Traceback \(most recent call last\)|"
    r"\b(?:[A-Za-z_][\w.]*)?(?:Error|Exception):\s"
)
JSON_LEVEL_ERROR_RE = re.compile(
    r'(?:"level"|level)\s*[=:]\s*"?(?:error|critical|fatal)"?',
    re.IGNORECASE,
)
MALFORMED_NEXT_ACTION_ID_RE = re.compile(
    r"^\s*Error: The Server Reference ID did not match the expected format\. "
    r'Received "(?:\\.|[^"\\])*"\.\s*$'
)
FRONTEND_CONTAINER_RE = re.compile(r"(?:^|-)frontend-\d+$")

RUN_TERMINAL = frozenset({"completed", "failed", "cancelled"})
# Deliberately idle — not "stuck" (same exclusions as engine.recover_stranded_runs).
RUN_NOT_STUCK = frozenset({"created", "waiting_for_user"})


def is_error_log_line(line: str, container_name: str = "") -> bool:
    # Next.js logs malformed client-supplied Server Action IDs as warnings. The
    # frontend has no Server Actions, so these are rejected requests, not app errors.
    if FRONTEND_CONTAINER_RE.search(container_name) and MALFORMED_NEXT_ACTION_ID_RE.fullmatch(line):
        return False
    return bool(ERROR_RE.search(line) or JSON_LEVEL_ERROR_RE.search(line))


def esc(value: object | None) -> str:
    return html.escape(str(value if value is not None else "—"))


class OpsMonitor:
    def __init__(
        self,
        settings: Settings,
        tg: TelegramClient,
        state: StateStore,
    ) -> None:
        self.settings = settings
        self.tg = tg
        self.state = state
        self._docker = None
        self._http = httpx.Client(timeout=15.0, follow_redirects=True)
        try:
            self._docker = docker.from_env()
        except DockerException:
            logger.warning("Docker socket unavailable — log monitoring disabled")

    def close(self) -> None:
        self._http.close()

    def send_code(self, text: str) -> None:
        self.tg.send_message(
            self.settings.chat_id,
            text,
            message_thread_id=self.settings.topic_code,
        )

    def send_clients(self, text: str) -> None:
        self.tg.send_message(
            self.settings.chat_id,
            text,
            message_thread_id=self.settings.topic_clients,
        )

    def bootstrap_cursors(self) -> None:
        now = datetime.now(UTC).isoformat()
        changed = False
        for key in ("users_since", "support_since"):
            if self.state.get(key) is None:
                self.state.set(key, now)
                changed = True
        if changed:
            logger.info("Initialized business poll cursors at %s", now)

    def poll_business_events(self) -> None:
        self._poll_new_users()
        self._poll_support_messages()

    def _parse_since(self, key: str) -> datetime:
        raw = self.state.get(key)
        if not raw:
            return datetime.now(UTC)
        return datetime.fromisoformat(raw)

    def _poll_new_users(self) -> None:
        since = self._parse_since("users_since")
        try:
            events = fetch_users_since(self.settings.database_url, since)
        except Exception:
            logger.exception("Failed to poll new users")
            return
        for event in events:
            self.send_clients(self._format_new_user(event))
            self.state.set("users_since", event.created_at.isoformat())

    def _poll_support_messages(self) -> None:
        since = self._parse_since("support_since")
        try:
            events = fetch_support_messages_since(self.settings.database_url, since)
        except Exception:
            logger.exception("Failed to poll support messages")
            return
        for event in events:
            self.send_clients(self._format_support_message(event))
            self.state.set("support_since", event.created_at.isoformat())

    @staticmethod
    def _format_new_user(event: UserEvent) -> str:
        return (
            "<b>Новый пользователь AIRuntime</b>\n"
            f"<code>{esc(event.email)}</code>\n"
            f"<i>{esc(event.created_at.isoformat())}</i>"
        )

    @staticmethod
    def _format_support_message(event: SupportEvent) -> str:
        body = (event.body or "").strip()
        if len(body) > 400:
            body = body[:400] + "…"
        if not body:
            body = "(пустое сообщение)"
        return f"<b>Сообщение в поддержку</b>\n<code>{esc(event.user_email)}</code>\n{esc(body)}"

    def _in_deploy_grace(self) -> bool:
        raw = self.state.get("deploy_grace_until")
        if not raw:
            return False
        try:
            return datetime.now(UTC) < datetime.fromisoformat(raw)
        except ValueError:
            return False

    def check_container_errors(self) -> None:
        if self._in_deploy_grace():
            return
        if self._docker is None:
            return
        since_seconds = self.settings.log_check_interval_sec
        since_ts = int(time.time()) - since_seconds
        totals: dict[str, int] = defaultdict(int)
        samples: dict[str, list[str]] = defaultdict(list)

        for name in self.settings.monitor_containers:
            try:
                container = self._docker.containers.get(name)
            except NotFound:
                continue
            except DockerException:
                logger.exception("Failed to get container %s", name)
                continue
            try:
                raw = container.logs(since=since_ts, timestamps=False, tail=2000)
            except DockerException:
                logger.exception("Failed to read logs for %s", name)
                continue
            text = raw.decode("utf-8", errors="replace")
            for line in text.splitlines():
                if is_error_log_line(line, name):
                    totals[name] += 1
                    if len(samples[name]) < 3:
                        samples[name].append(line.strip()[:180])

        total_errors = sum(totals.values())
        if total_errors < self.settings.error_alert_threshold:
            return

        fingerprint = hashlib.sha1(
            "|".join(
                f"{name}:{totals[name]}:" + ";".join(samples[name]) for name in sorted(totals)
            ).encode("utf-8")
        ).hexdigest()[:16]

        now = datetime.now(UTC)
        last = self.state.get("last_error_alert_at")
        last_fp = self.state.get("last_error_alert_fp")
        if last_fp == fingerprint:
            if last:
                last_dt = datetime.fromisoformat(last)
                if now - last_dt < timedelta(hours=6):
                    return
        elif last:
            last_dt = datetime.fromisoformat(last)
            if now - last_dt < timedelta(seconds=max(60, since_seconds - 30)):
                return

        lines = [
            "<b>Алерт: много ошибок на проде</b>",
            f"За последние ~{since_seconds // 60} мин: <b>{total_errors}</b> "
            f"(порог {self.settings.error_alert_threshold})",
            "",
        ]
        for name, cnt in sorted(totals.items(), key=lambda x: -x[1]):
            lines.append(f"• <code>{esc(name)}</code>: {cnt}")
            for sample in samples[name]:
                lines.append(f"  <i>{esc(sample)}</i>")
        self.send_code("\n".join(lines))
        self.state.set("last_error_alert_at", now.isoformat())
        self.state.set("last_error_alert_fp", fingerprint)

    def check_health_urls(self) -> None:
        if self._in_deploy_grace():
            return
        if not self.settings.health_check_urls:
            return
        failures: list[tuple[str, str]] = []
        for url in self.settings.health_check_urls:
            try:
                response = self._http.get(url)
                if response.status_code >= 400:
                    failures.append((url, f"HTTP {response.status_code}"))
            except httpx.HTTPError as exc:
                failures.append((url, str(exc)[:120]))

        if not failures:
            self.state.set("last_health_ok", datetime.now(UTC).isoformat())
            return

        fingerprint = hashlib.sha1(
            "|".join(f"{url}:{reason}" for url, reason in failures).encode("utf-8")
        ).hexdigest()[:16]
        now = datetime.now(UTC)
        if self.state.get("last_health_alert_fp") == fingerprint:
            last = self.state.get("last_health_alert_at")
            if last and now - datetime.fromisoformat(last) < timedelta(minutes=30):
                return

        lines = ["<b>Алерт: health-check не прошёл</b>", ""]
        for url, reason in failures:
            lines.append(f"• <code>{esc(url)}</code> — {esc(reason)}")
        self.send_code("\n".join(lines))
        self.state.set("last_health_alert_at", now.isoformat())
        self.state.set("last_health_alert_fp", fingerprint)

    def check_stuck_orchestration_runs(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=self.settings.stuck_run_after_hours)
        excluded = sorted(RUN_TERMINAL | RUN_NOT_STUCK)
        placeholders = ", ".join(f"'{status}'" for status in excluded)
        query = f"""
            SELECT id::text, status, goal, updated_at
            FROM orchestration_runs
            WHERE status NOT IN ({placeholders})
              AND started_at IS NOT NULL
              AND updated_at < %s
            ORDER BY updated_at ASC
            LIMIT 20
        """
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (cutoff,))
                    rows = cur.fetchall()
        except Exception:
            logger.exception("Failed to query stuck orchestration runs")
            return

        if not rows:
            return

        run_ids = [row[0] for row in rows]
        fingerprint = hashlib.sha1(",".join(sorted(run_ids)).encode("utf-8")).hexdigest()[:16]
        now = datetime.now(UTC)
        if self.state.get("last_stuck_alert_fp") == fingerprint:
            last = self.state.get("last_stuck_alert_at")
            if last and now - datetime.fromisoformat(last) < timedelta(hours=6):
                return

        lines = [
            "<b>Алерт: зависшие orchestration runs</b>",
            f"Без обновления &gt; {self.settings.stuck_run_after_hours} ч: <b>{len(rows)}</b>",
            "",
        ]
        for run_id, status, goal, updated_at in rows[:10]:
            goal_short = (goal or "—")[:80]
            lines.append(
                f"• <code>{esc(run_id[:8])}…</code> · {esc(status)} · "
                f"{esc(updated_at.isoformat() if updated_at else None)}\n"
                f"  {esc(goal_short)}"
            )
        if len(rows) > 10:
            lines.append(f"… и ещё {len(rows) - 10}")
        self.send_code("\n".join(lines))
        self.state.set("last_stuck_alert_at", now.isoformat())
        self.state.set("last_stuck_alert_fp", fingerprint)

    def check_orchestration_internal_errors(self) -> None:
        """Alert on recent runs that failed with internal_error (engine bugs)."""
        query = """
            SELECT r.id::text, p.name, r.error_message, r.finished_at
            FROM orchestration_runs r
            JOIN projects p ON p.id = r.project_id
            WHERE r.status = 'failed'
              AND r.error_code = 'internal_error'
              AND r.finished_at > now() - interval '6 hours'
            ORDER BY r.finished_at DESC
            LIMIT 10
        """
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    rows = cur.fetchall()
        except Exception:
            logger.exception("Failed to query internal_error orchestration runs")
            return

        if not rows:
            return

        run_ids = [row[0] for row in rows]
        fingerprint = hashlib.sha1(",".join(sorted(run_ids)).encode("utf-8")).hexdigest()[:16]
        now = datetime.now(UTC)
        if self.state.get("last_internal_error_alert_fp") == fingerprint:
            last = self.state.get("last_internal_error_alert_at")
            if last and now - datetime.fromisoformat(last) < timedelta(hours=6):
                return

        lines = [
            "<b>Алерт: orchestration internal_error</b>",
            f"За последние 6 ч: <b>{len(rows)}</b> run(s)",
            "",
        ]
        for run_id, project_name, err, finished_at in rows[:8]:
            err_short = (err or "—")[:100]
            lines.append(
                f"• <code>{esc(run_id[:8])}…</code> · {esc(project_name)}\n"
                f"  {esc(err_short)} · {esc(finished_at.isoformat() if finished_at else None)}"
            )
        self.send_code("\n".join(lines))
        self.state.set("last_internal_error_alert_at", now.isoformat())
        self.state.set("last_internal_error_alert_fp", fingerprint)

    def check_routerai_balance(self) -> None:
        if not self.settings.routerai_api_key:
            return

        # This runs hourly; a fresh connection avoids reusing the long-lived
        # health-check client's transport after it has been idle to RouterAI.
        with httpx.Client(timeout=15.0, follow_redirects=True) as balance_client:
            balance = fetch_balance_rub(
                balance_client,
                api_key=self.settings.routerai_api_key,
                base_url=self.settings.routerai_base_url,
            )
        if balance is None:
            failures = int(self.state.get("routerai_balance_consecutive_failures", 0) or 0) + 1
            self.state.set("routerai_balance_consecutive_failures", failures)
            if failures >= 3 and not self.state.get("routerai_balance_unavailable_alerted"):
                self.send_code(
                    "<b>Алерт: не удаётся проверить баланс RouterAI</b>\n"
                    f"Проверка не прошла {failures} раза подряд. "
                    "Последний успешный баланс может быть устаревшим."
                )
                self.state.set("routerai_balance_unavailable_alerted", True)
            return

        self.state.set("routerai_balance_consecutive_failures", 0)
        if self.state.get("routerai_balance_unavailable_alerted"):
            self.send_code("<b>RouterAI: проверка баланса восстановилась</b>")
            self.state.set("routerai_balance_unavailable_alerted", False)

        now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
        today = now_msk.date().isoformat()
        if (
            now_msk.hour >= self.settings.routerai_balance_report_hour
            and self.state.get("routerai_last_balance_report_date") != today
        ):
            self.send_code(
                "<b>RouterAI: баланс</b>\n"
                f"Остаток: <b>{esc(format_rub(balance))}</b>\n"
                f"<i>{esc(now_msk.strftime('%Y-%m-%d %H:%M MSK'))}</i>"
            )
            self.state.set("routerai_last_balance_report_date", today)

        threshold = self.settings.routerai_low_balance_rub
        low_alerted = bool(self.state.get("routerai_low_balance_alerted"))
        if balance < threshold:
            if not low_alerted:
                self.send_code(
                    "<b>Алерт: низкий баланс RouterAI</b>\n"
                    f"Остаток <b>{esc(format_rub(balance))}</b> "
                    f"(порог {esc(format_rub(threshold))})\n"
                    "Пополните аккаунт, иначе LLM-запросы начнут падать."
                )
                self.state.set("routerai_low_balance_alerted", True)
        elif low_alerted:
            self.state.set("routerai_low_balance_alerted", False)

        self.state.set("routerai_last_balance_rub", round(balance, 2))
