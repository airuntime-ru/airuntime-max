"""Read-only aggregates for the billing/AI analytics dashboard.

Pure functions (no `request`), so they can be unit-tested and reused by the CSV export.

Money model recap, because every number here depends on it:
  * `CreditLedgerEntry.provider_cost_usd_micros` is what the provider charged us, raw.
  * `amount` is what the user was charged, in credits, markup already applied.
  * `reason="byok_usage"` rows have amount 0 - the user paid their own provider, so that
    traffic is volume for us but neither cost nor revenue.
Margin is therefore `charged - provider_cost` over platform-key rows only.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.models import AppUser, CreditLedgerEntry, CreditTopUp, PlanChangeRequest, Project

# Must match backend settings.billing_credits_per_rub / billing_usd_to_rub.
CREDITS_PER_RUB = 100
USD_TO_RUB = 100

BYOK_REASON = "byok_usage"
USAGE_REASONS = ("chat_message", BYOK_REASON)


def _window(days: int) -> tuple:
    now = timezone.now()
    return now - timedelta(days=days), now


def _credits_to_rub(credits: int | None) -> float:
    return round(abs(credits or 0) / CREDITS_PER_RUB, 2)


def _micros_to_usd(micros: int | None) -> float:
    return round((micros or 0) / 1_000_000, 4)


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round(100 * (current - previous) / previous, 1)


def _spend_stats(start, end) -> dict:
    """Provider cost, charged revenue and BYOK volume for one window."""
    usage = CreditLedgerEntry.objects.filter(
        created_at__gte=start, created_at__lt=end, reason__in=USAGE_REASONS
    )
    platform = usage.exclude(reason=BYOK_REASON)
    byok = usage.filter(reason=BYOK_REASON)

    platform_cost_micros = platform.aggregate(v=Sum("provider_cost_usd_micros"))["v"] or 0
    byok_cost_micros = byok.aggregate(v=Sum("provider_cost_usd_micros"))["v"] or 0
    charged_credits = platform.aggregate(v=Sum("amount"))["v"] or 0

    provider_cost_usd = _micros_to_usd(platform_cost_micros)
    provider_cost_rub = round(provider_cost_usd * USD_TO_RUB, 2)
    charged_rub = _credits_to_rub(charged_credits)

    topups_rub = (
        CreditTopUp.objects.filter(status="paid", paid_at__gte=start, paid_at__lt=end).aggregate(
            v=Sum("amount_rub")
        )["v"]
        or 0
    )

    return {
        "provider_cost_usd": provider_cost_usd,
        "provider_cost_rub": provider_cost_rub,
        "charged_rub": charged_rub,
        "margin_rub": round(charged_rub - provider_cost_rub, 2),
        "byok_cost_usd": _micros_to_usd(byok_cost_micros),
        "topups_paid_rub": float(topups_rub),
        "active_users": usage.values("user_id").distinct().count(),
        "requests": usage.count(),
    }


def summary_kpis(*, days: int = 30) -> dict:
    """Headline tiles: this period against the equal-length one before it."""
    start, now = _window(days)
    previous_start = start - timedelta(days=days)

    current = _spend_stats(start, now)
    previous = _spend_stats(previous_start, start)

    paying_users = (
        CreditTopUp.objects.filter(status="paid", paid_at__gte=start)
        .values("user_id")
        .distinct()
        .count()
    )

    tiles = []
    for key, label, unit in (
        ("provider_cost_rub", "Расход на провайдера", "₽"),
        ("charged_rub", "Списано с пользователей", "₽"),
        ("margin_rub", "Маржа", "₽"),
        ("topups_paid_rub", "Оплачено пополнений", "₽"),
        ("active_users", "Активных пользователей", ""),
        ("requests", "Запросов к моделям", ""),
    ):
        tiles.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "value": current[key],
                "delta_pct": _pct_change(current[key], previous[key]),
            }
        )

    tiles.append(
        {
            "key": "byok_cost_usd",
            "label": "BYOK (не наш расход)",
            "unit": "$",
            "value": current["byok_cost_usd"],
            "delta_pct": _pct_change(current["byok_cost_usd"], previous["byok_cost_usd"]),
        }
    )
    tiles.append(
        {
            "key": "paying_users",
            "label": "Платящих",
            "unit": "",
            "value": paying_users,
            "delta_pct": None,
        }
    )
    return {"tiles": tiles, "current": current, "previous": previous}


def daily_spend_series(*, days: int = 30) -> list[dict]:
    """Per-day provider cost vs. what users were charged, both in rubles."""
    start, now = _window(days)
    rows = (
        CreditLedgerEntry.objects.filter(
            created_at__gte=start, created_at__lt=now, reason__in=USAGE_REASONS
        )
        .exclude(reason=BYOK_REASON)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(cost=Sum("provider_cost_usd_micros"), charged=Sum("amount"))
        .order_by("day")
    )
    return [
        {
            "day": row["day"].isoformat() if row["day"] else "",
            "provider_cost_rub": round(_micros_to_usd(row["cost"]) * USD_TO_RUB, 2),
            "charged_rub": _credits_to_rub(row["charged"]),
        }
        for row in rows
    ]


def by_model(*, days: int = 30, limit: int = 20) -> list[dict]:
    start, now = _window(days)
    rows = (
        CreditLedgerEntry.objects.filter(
            created_at__gte=start, created_at__lt=now, reason__in=USAGE_REASONS
        )
        .values("provider", "model")
        .annotate(
            requests=Count("id"),
            cost=Sum("provider_cost_usd_micros"),
            charged=Sum("amount"),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
        )
        .order_by("-cost")[:limit]
    )
    return [
        {
            "provider": row["provider"] or "—",
            "model": row["model"] or "—",
            "requests": row["requests"],
            "provider_cost_usd": _micros_to_usd(row["cost"]),
            "charged_rub": _credits_to_rub(row["charged"]),
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
        }
        for row in rows
    ]


def platform_vs_byok(*, days: int = 30) -> list[dict]:
    start, now = _window(days)
    usage = CreditLedgerEntry.objects.filter(
        created_at__gte=start, created_at__lt=now, reason__in=USAGE_REASONS
    )
    platform = usage.exclude(reason=BYOK_REASON).count()
    byok = usage.filter(reason=BYOK_REASON).count()
    return [
        {"label": "Ключ платформы", "requests": platform},
        {"label": "Свой ключ (BYOK)", "requests": byok},
    ]


def top_users(*, days: int = 30, limit: int = 20) -> list[dict]:
    start, now = _window(days)
    rows = (
        CreditLedgerEntry.objects.filter(
            created_at__gte=start, created_at__lt=now, reason__in=USAGE_REASONS
        )
        .values("user_id")
        .annotate(
            requests=Count("id"),
            cost=Sum("provider_cost_usd_micros"),
            charged=Sum("amount"),
        )
        .order_by("-cost")[:limit]
    )
    emails = dict(
        AppUser.objects.filter(id__in=[row["user_id"] for row in rows]).values_list("id", "email")
    )
    return [
        {
            "email": emails.get(row["user_id"], str(row["user_id"])),
            "requests": row["requests"],
            "provider_cost_usd": _micros_to_usd(row["cost"]),
            "charged_rub": _credits_to_rub(row["charged"]),
        }
        for row in rows
    ]


def funnel(*, days: int = 30) -> list[dict]:
    """signup -> first project -> first deploy -> first spend -> paid."""
    start, now = _window(days)
    cohort = AppUser.objects.filter(created_at__gte=start, created_at__lt=now)
    cohort_ids = list(cohort.values_list("id", flat=True))
    signups = len(cohort_ids)

    if not signups:
        return [
            {"step": label, "count": 0, "pct": 0.0}
            for label in ("Регистрация", "Создал проект", "Задеплоил", "Потратил", "Оплатил")
        ]

    with_project = (
        Project.objects.filter(user_id__in=cohort_ids).values("user_id").distinct().count()
    )
    deployed = (
        Project.objects.filter(user_id__in=cohort_ids, deployment_url__isnull=False)
        .values("user_id")
        .distinct()
        .count()
    )
    spent = (
        CreditLedgerEntry.objects.filter(user_id__in=cohort_ids, reason__in=USAGE_REASONS)
        .values("user_id")
        .distinct()
        .count()
    )
    paid = (
        CreditTopUp.objects.filter(user_id__in=cohort_ids, status="paid")
        .values("user_id")
        .distinct()
        .count()
    )

    steps = [
        ("Регистрация", signups),
        ("Создал проект", with_project),
        ("Задеплоил", deployed),
        ("Потратил", spent),
        ("Оплатил", paid),
    ]
    return [
        {"step": label, "count": count, "pct": round(100 * count / signups, 1)}
        for label, count in steps
    ]


def plan_requests_summary(*, days: int = 30) -> dict:
    start, now = _window(days)
    rows = PlanChangeRequest.objects.filter(created_at__gte=start, created_at__lt=now)
    counts = rows.aggregate(
        pending=Count("id", filter=Q(status="pending")),
        approved=Count("id", filter=Q(status="approved")),
        rejected=Count("id", filter=Q(status="rejected")),
        cancelled=Count("id", filter=Q(status="cancelled")),
    )
    # Pending is a live queue, not a windowed metric - an old unanswered request still matters.
    counts["pending"] = PlanChangeRequest.objects.filter(status="pending").count()
    return counts


def free_burn_rate(*, days: int = 30) -> dict:
    """What the free tier costs us per day - the number that decides invite volume."""
    start, now = _window(days)
    free_users = AppUser.objects.filter(plan__key="free").values_list("id", flat=True)
    cost_micros = (
        CreditLedgerEntry.objects.filter(
            created_at__gte=start,
            created_at__lt=now,
            reason="chat_message",
            user_id__in=list(free_users),
        ).aggregate(v=Sum("provider_cost_usd_micros"))["v"]
        or 0
    )
    total_rub = round(_micros_to_usd(cost_micros) * USD_TO_RUB, 2)
    return {
        "total_rub": total_rub,
        "per_day_rub": round(total_rub / max(1, days), 2),
        "free_users": len(free_users),
    }


def usage_totals(*, days: int = 30) -> dict:
    start, now = _window(days)
    rows = CreditLedgerEntry.objects.filter(
        created_at__gte=start, created_at__lt=now, reason__in=USAGE_REASONS
    ).aggregate(
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        requests=Count("id"),
    )
    return {
        "input_tokens": rows["input_tokens"] or 0,
        "output_tokens": rows["output_tokens"] or 0,
        "requests": rows["requests"] or 0,
    }


def decimal_default(value):
    """json.dumps helper - Decimal shows up in Sum() results on some backends."""
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Not JSON serializable: {type(value)}")
