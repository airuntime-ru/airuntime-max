from __future__ import annotations

import csv
import json

from django.contrib import admin
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse

from core import analytics_reporting as reporting


def _days(request) -> int:
    try:
        return max(7, min(90, int(request.GET.get("days", "30"))))
    except ValueError:
        return 30


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=reporting.decimal_default)


def analytics_dashboard_view(request):
    """Расход на модели, выручка, маржа, воронка и заявки на тарифы."""
    days = _days(request)
    context = {
        **admin.site.each_context(request),
        "title": "Аналитика",
        "days": days,
        "kpis_json": _json(reporting.summary_kpis(days=days)),
        "daily_spend_json": _json(reporting.daily_spend_series(days=days)),
        "by_model_json": _json(reporting.by_model(days=days)),
        "platform_vs_byok_json": _json(reporting.platform_vs_byok(days=days)),
        "top_users_json": _json(reporting.top_users(days=days)),
        "funnel_json": _json(reporting.funnel(days=days)),
        "plan_requests_json": _json(reporting.plan_requests_summary(days=days)),
        "free_burn_json": _json(reporting.free_burn_rate(days=days)),
        "usage_totals_json": _json(reporting.usage_totals(days=days)),
        "product_analytics_url": reverse("admin:product_analytics_dashboard"),
    }
    return TemplateResponse(request, "admin/analytics_dashboard.html", context)


_EXPORT_FIELDS = {
    "daily-spend": ("day", "provider_cost_rub", "charged_rub"),
    "by-model": (
        "provider",
        "model",
        "requests",
        "provider_cost_usd",
        "charged_rub",
        "input_tokens",
        "output_tokens",
    ),
    "top-users": ("email", "requests", "provider_cost_usd", "charged_rub"),
    "funnel": ("step", "count", "pct"),
}


def _export_rows(report: str, *, days: int) -> list[dict] | None:
    """Compute only the requested report - the others are never touched."""
    if report == "daily-spend":
        return reporting.daily_spend_series(days=days)
    if report == "by-model":
        return reporting.by_model(days=days, limit=1000)
    if report == "top-users":
        return reporting.top_users(days=days, limit=1000)
    if report == "funnel":
        return reporting.funnel(days=days)
    return None


def analytics_export_csv_view(request, report: str):
    days = _days(request)
    if report not in _EXPORT_FIELDS:
        return HttpResponse("Unknown report", status=404)

    rows = _export_rows(report, days=days) or []
    fields = _EXPORT_FIELDS[report]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{report}-{days}d.csv"'
    writer = csv.DictWriter(response, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return response
