import csv
import json

from django.contrib import admin
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse

from product_analytics import reporting
from product_analytics.models import AnalyticsSession


@admin.register(AnalyticsSession)
class ProductAnalyticsDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/product_analytics/analytics_dashboard.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        return [
            path(
                "dashboard/",
                self.admin_site.admin_view(self.dashboard_view),
                name="product_analytics_dashboard",
            ),
            path(
                "export/<str:report>.csv",
                self.admin_site.admin_view(self.export_csv_view),
                name="product_analytics_export_csv",
            ),
        ] + urls

    def changelist_view(self, request, extra_context=None):
        return self.dashboard_view(request)

    def _params(self, request) -> tuple[int, str]:
        try:
            days = max(7, min(90, int(request.GET.get("days", "30"))))
        except ValueError:
            days = 30
        platform = (request.GET.get("platform") or "all").strip()
        return days, platform

    def dashboard_view(self, request):
        days, platform = self._params(request)
        available_platforms = reporting.available_platforms()
        if platform != "all" and platform not in available_platforms:
            platform = "all"

        context = {
            **self.admin_site.each_context(request),
            "title": "Продуктовая аналитика",
            "opts": self.model._meta,
            "days": days,
            "platform": platform,
            "available_platforms": available_platforms,
            "billing_analytics_url": reverse("admin_analytics_dashboard"),
            "kpis_json": json.dumps(
                reporting.summary_kpis(days=days, platform=platform), ensure_ascii=False
            ),
            "dau_series_json": json.dumps(
                reporting.dau_series(days=days, platform=platform), ensure_ascii=False
            ),
            "top_screens_json": json.dumps(
                reporting.top_screens(days=days, platform=platform), ensure_ascii=False
            ),
            "platforms_json": json.dumps(
                reporting.platform_breakdown(days=days), ensure_ascii=False
            ),
            "retention_json": json.dumps(
                reporting.retention_summary(days=days, platform=platform), ensure_ascii=False
            ),
        }
        return TemplateResponse(
            request, "admin/product_analytics/analytics_dashboard.html", context
        )

    _EXPORT_FIELDS = {
        "top-screens": ("screen", "views", "avg_duration_ms"),
        "retention": ("cohort_day", "cohort_size", "d1_pct", "d7_pct", "d30_pct"),
        "dau": ("day", "dau"),
    }

    def export_csv_view(self, request, report: str):
        days, platform = self._params(request)
        if report not in self._EXPORT_FIELDS:
            return HttpResponse("Unknown report", status=404)

        if report == "top-screens":
            rows = reporting.top_screens(days=days, limit=1000, platform=platform)
        elif report == "retention":
            rows = reporting.retention_summary(days=days, platform=platform)["cohorts"]
        elif report == "dau":
            rows = reporting.dau_series(days=days, platform=platform)
        else:
            rows = []

        fields = self._EXPORT_FIELDS[report]
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="product-{report}-{days}d.csv"'
        writer = csv.DictWriter(response, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return response
