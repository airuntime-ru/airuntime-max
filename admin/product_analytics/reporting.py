"""Read-only product analytics aggregates for Django admin."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count, F, QuerySet
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from product_analytics.models import AnalyticsEvent, AnalyticsSession

IDENTITY = Coalesce(F("user_id"), F("anonymous_id"))


def _since(days: int):
    return timezone.now() - timedelta(days=days)


def _window(days: int) -> tuple:
    now = timezone.now()
    return now - timedelta(days=days), now


def _apply_platform(qs: QuerySet, platform: str | None) -> QuerySet:
    if platform and platform != "all":
        return qs.filter(platform=platform)
    return qs


def available_platforms() -> list[str]:
    rows = AnalyticsEvent.objects.order_by().values_list("platform", flat=True).distinct()
    return sorted(p for p in rows if p)


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round(100 * (current - previous) / previous, 1)


def summary_kpis(*, days: int = 30, platform: str | None = None) -> dict:
    current_start, now = _window(days)
    previous_start = current_start - timedelta(days=days)

    events = _apply_platform(AnalyticsEvent.objects.all(), platform)
    sessions = _apply_platform(AnalyticsSession.objects.all(), platform)

    def _period_stats(start, end) -> dict:
        period_events = events.filter(occurred_at__gte=start, occurred_at__lt=end)
        period_sessions = sessions.filter(started_at__gte=start, started_at__lt=end)
        unique_users = period_events.aggregate(n=Count(IDENTITY, distinct=True))["n"] or 0
        total_events = period_events.count()
        total_sessions = period_sessions.count()
        return {
            "unique_users": unique_users,
            "total_events": total_events,
            "total_sessions": total_sessions,
        }

    current = _period_stats(current_start, now)
    previous = _period_stats(previous_start, current_start)

    tiles = [
        {
            "key": key,
            "label": label,
            "value": current[key],
            "delta_pct": _pct_change(current[key], previous[key]),
        }
        for key, label in (
            ("unique_users", "Уникальные пользователи"),
            ("total_sessions", "Сессии"),
            ("total_events", "События"),
        )
    ]
    return {"tiles": tiles}


def dau_series(*, days: int = 30, platform: str | None = None) -> list[dict]:
    since = _since(days - 1)
    qs = _apply_platform(AnalyticsEvent.objects.filter(occurred_at__gte=since), platform)
    rows = (
        qs.annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(dau=Count(IDENTITY, distinct=True))
        .order_by("day")
    )
    by_day = {row["day"].isoformat(): row["dau"] for row in rows}
    start = timezone.localdate() - timedelta(days=days - 1)
    end = timezone.localdate()
    out: list[dict] = []
    day = start
    while day <= end:
        iso = day.isoformat()
        out.append({"day": iso, "dau": by_day.get(iso, 0)})
        day += timedelta(days=1)
    return out


def top_screens(*, days: int = 30, limit: int = 15, platform: str | None = None) -> list[dict]:
    since = _since(days)
    base = _apply_platform(AnalyticsEvent.objects.filter(occurred_at__gte=since), platform)
    view_rows = (
        base.filter(name="screen_view", screen__isnull=False)
        .values("screen")
        .annotate(views=Count("id"))
        .order_by("-views")[:limit]
    )
    duration_rows = {
        row["screen"]: row["avg_duration_ms"]
        for row in base.filter(name="screen_leave", screen__isnull=False, duration_ms__isnull=False)
        .values("screen")
        .annotate(avg_duration_ms=Avg("duration_ms"))
    }
    return [
        {
            "screen": row["screen"],
            "views": row["views"],
            "avg_duration_ms": int(duration_rows.get(row["screen"]) or 0),
        }
        for row in view_rows
    ]


def platform_breakdown(*, days: int = 30) -> list[dict]:
    since = _since(days)
    rows = (
        AnalyticsEvent.objects.filter(occurred_at__gte=since)
        .values("platform")
        .annotate(users=Count(IDENTITY, distinct=True), events=Count("id"))
        .order_by("-events")
    )
    return [
        {"platform": row["platform"], "users": row["users"], "events": row["events"]}
        for row in rows
    ]


def retention_summary(*, days: int = 30, platform: str | None = None) -> dict:
    """Approximate D1/D7/D30 retention from first-seen identity cohorts."""
    since = timezone.localdate() - timedelta(days=days)
    cohorts: dict[str, object] = {}
    activity: dict[str, set[str]] = {}

    qs = _apply_platform(AnalyticsEvent.objects.filter(occurred_at__date__gte=since), platform)
    qs = qs.annotate(identity=IDENTITY).values_list("identity", "occurred_at__date").distinct()
    for identity, active_day in qs.iterator(chunk_size=2000):
        if not identity or not active_day:
            continue
        activity.setdefault(active_day.isoformat(), set()).add(identity)
        existing = cohorts.get(identity)
        if existing is None or active_day < existing:
            cohorts[identity] = active_day

    buckets = []
    for offset in range(14):
        cohort_day = timezone.localdate() - timedelta(days=offset + 1)
        if cohort_day < since:
            break
        members = {identity for identity, first_day in cohorts.items() if first_day == cohort_day}
        size = len(members)
        if not size:
            continue

        def retained(delta: int, day=cohort_day, m=members) -> int:
            target = (day + timedelta(days=delta)).isoformat()
            return len(m & activity.get(target, set()))

        buckets.append(
            {
                "cohort_day": cohort_day.isoformat(),
                "cohort_size": size,
                "d1_pct": round(100 * retained(1) / size, 1),
                "d7_pct": round(100 * retained(7) / size, 1),
                "d30_pct": round(100 * retained(30) / size, 1),
            }
        )
    return {"cohorts": buckets}
