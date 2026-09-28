"""Turn the storefront's human-readable slots into real times.

The model writes labels a customer can tap («Вт 23 сен, 16:00»). The owner's calendar
and the customer's history need a datetime, so this module is the one place that reads
those labels. «Сегодня» / «Завтра» are resolved against the moment the lead was created,
not against "now" when someone later opens the calendar - otherwise yesterday's booking
would jump a day every midnight.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

_WEEKDAYS = {
    "пн": 0,
    "понедельник": 0,
    "вт": 1,
    "вторник": 1,
    "ср": 2,
    "среда": 2,
    "чт": 3,
    "четверг": 3,
    "пт": 4,
    "пятница": 4,
    "сб": 5,
    "суббота": 5,
    "вс": 6,
    "воскресенье": 6,
}
_WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "мая": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}
_MONTH_SHORT = (
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)

_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})")
_DATE_RE = re.compile(
    r"(\d{1,2})\s*(янв|фев|мар|апр|мая|май|июн|июл|авг|сен|окт|ноя|дек)[а-я]*",
    re.IGNORECASE,
)
_DOT_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})")
_WEEKDAY_RE = re.compile(
    r"\b(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b",
    re.IGNORECASE,
)


def now_msk() -> datetime:
    return datetime.now(MSK)


def russian_today(moment: datetime | None = None) -> str:
    """«вторник, 22 сентября 2026» — injected into the generator so slots are dated."""
    when = _as_msk(moment or now_msk())
    weekdays = (
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
        "воскресенье",
    )
    months = (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )
    return f"{weekdays[when.weekday()]}, {when.day} {months[when.month - 1]} {when.year}"


def format_slot(moment: datetime) -> str:
    when = _as_msk(moment)
    weekday = _WEEKDAY_SHORT[when.weekday()].capitalize()
    month = _MONTH_SHORT[when.month - 1]
    return f"{weekday} {when.day} {month}, {when.hour:02d}:{when.minute:02d}"


def fallback_slots(moment: datetime | None = None, *, count: int = 5) -> list[str]:
    """A few upcoming evening/afternoon times when the model never ran."""
    origin = _as_msk(moment or now_msk())
    found: list[str] = []
    for day in range(0, 8):
        for hour in (12, 16, 18):
            candidate = origin.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(
                days=day
            )
            if candidate <= origin:
                continue
            found.append(format_slot(candidate))
            if len(found) >= count:
                return found
    return found


def parse_slot(label: str, *, relative_to: datetime) -> datetime | None:
    """Best-effort datetime for a slot label, or None if it is just free text."""
    text = (label or "").strip().lower()
    if not text:
        return None
    origin = _as_msk(relative_to)
    hour, minute = _parse_time(text)
    if hour is None:
        return None

    day: date | None = None
    if text.startswith("сегодня"):
        day = origin.date()
    elif text.startswith("завтра"):
        day = (origin + timedelta(days=1)).date()
    else:
        dated = _DATE_RE.search(text)
        if dated:
            day = _calendar_day(int(dated.group(1)), dated.group(2), origin.date())
        else:
            dotted = _DOT_DATE_RE.search(text)
            if dotted:
                day = _dotted_day(int(dotted.group(1)), int(dotted.group(2)), origin.date())

    if day is None:
        weekday = _WEEKDAY_RE.search(text)
        if weekday:
            target = _WEEKDAYS[weekday.group(1).lower()]
            delta = (target - origin.weekday()) % 7
            day = (origin + timedelta(days=delta)).date()
            # A weekday with no date, later today already passed: next week.
            if delta == 0 and (hour, minute) < (origin.hour, origin.minute):
                day = day + timedelta(days=7)

    if day is None:
        return None
    try:
        return datetime.combine(day, time(hour, minute), tzinfo=MSK)
    except ValueError:
        return None


def scheduled_iso(label: str, created_at: datetime | None) -> str | None:
    if created_at is None:
        return None
    parsed = parse_slot(label, relative_to=created_at)
    return parsed.isoformat() if parsed else None


def _as_msk(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=MSK)
    return moment.astimezone(MSK)


def _parse_time(text: str) -> tuple[int | None, int | None]:
    match = _TIME_RE.search(text)
    if not match:
        return None, None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None, None
    return hour, minute


def _month_number(token: str) -> int | None:
    key = token.lower()[:3]
    if key == "май":
        key = "мая"
    return _MONTHS.get(key)


def _calendar_day(day: int, month_token: str, origin: date) -> date | None:
    month = _month_number(month_token)
    if month is None:
        return None
    year = origin.year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    # A December booking created in January of the next year, or the reverse wrap.
    if found < origin - timedelta(days=60):
        try:
            found = date(year + 1, month, day)
        except ValueError:
            return None
    return found


def _dotted_day(day: int, month: int, origin: date) -> date | None:
    year = origin.year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if found < origin - timedelta(days=60):
        try:
            found = date(year + 1, month, day)
        except ValueError:
            return None
    return found
