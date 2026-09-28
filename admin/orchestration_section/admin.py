from __future__ import annotations

import json

from django.contrib import admin
from django.http import Http404, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.models import Project
from domain.product_links import collect_product_links, link_display_text
from orchestration_section import reporting
from orchestration_section.models import DomainOrchestrationRun


def _days(request) -> int:
    try:
        return max(7, min(90, int(request.GET.get("days", "30"))))
    except ValueError:
        return 30


def _window_hours(request) -> int:
    try:
        return max(1, min(168, int(request.GET.get("hours", "24"))))
    except ValueError:
        return 24


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=reporting.decimal_default)


def _product_link_rows(project) -> list[tuple[str, str, str]]:
    if project is None:
        return []
    return [
        (label, href, link_display_text(href)) for label, href in collect_product_links(project)
    ]


def project_dashboard_view(request, project_id):
    """Дашборд оркестрации для одного проекта."""
    project = Project.objects.filter(id=project_id).first()
    if project is None:
        raise Http404("Проект не найден")

    days = _days(request)
    hours = _window_hours(request)
    summary = reporting.project_orchestration_summary(project_id, days)
    metrics = reporting.project_metrics(project_id, hours)

    context = {
        **admin.site.each_context(request),
        "title": f"Оркестрация — {project.name}",
        "project": project,
        "product_links": _product_link_rows(project),
        "days": days,
        "hours": hours,
        "summary_json": _json(summary),
        "metrics_json": _json(metrics),
        "summary": summary,
        "metrics": metrics,
    }
    return TemplateResponse(request, "admin/orchestration/project_dashboard.html", context)


def run_detail_view(request, run_id):
    """Детали запуска: метаданные, задачи, таймлайн событий."""
    data = reporting.run_timeline(run_id)
    if data["run"] is None:
        raise Http404("Запуск не найден")

    run = data["run"]
    project = Project.objects.filter(id=run["project_id"]).first()

    context = {
        **admin.site.each_context(request),
        "title": f"Запуск {run_id}",
        "run": run,
        "project": project,
        "product_links": _product_link_rows(project),
        "tasks": data["tasks"],
        "events": data["events"],
        "run_json": _json(data),
    }
    return TemplateResponse(request, "admin/orchestration/run_detail.html", context)


def fleet_analytics_dashboard_view(request):
    days = _days(request)
    hours = _window_hours(request)
    context = {
        **admin.site.each_context(request),
        "title": "Аналитика оркестратора",
        "days": days,
        "hours": hours,
        "summary_json": _json(reporting.fleet_summary(days=days)),
        "daily_runs_json": _json(reporting.daily_runs_series(days=days)),
        "status_json": _json(reporting.runs_by_status(days=days)),
        "tasks_by_role_json": _json(reporting.tasks_by_role_fleet(days=days)),
        "health_json": _json(reporting.fleet_health_metrics(window_hours=hours)),
        "active_runs_json": _json(reporting.active_runs()),
        "runs_changelist_url": reverse(
            "admin:orchestration_section_domainorchestrationrun_changelist"
        ),
    }
    return TemplateResponse(request, "admin/orchestration/fleet_analytics.html", context)


def fleet_analytics_live_view(request):
    return JsonResponse(
        {
            "active_runs": reporting.active_runs(),
            "generated_at": timezone.now().isoformat(),
        }
    )


@admin.register(DomainOrchestrationRun)
class DomainOrchestrationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "status",
        "credits_used",
        "plan_version",
        "provider",
        "model",
        "created_at",
        "finished_at",
        "dashboard_link",
    )
    list_filter = ("status", "provider")
    search_fields = ("id", "goal", "original_request", "project__name")
    readonly_fields = (
        "id",
        "project",
        "chat_id",
        "message_id",
        "user_id",
        "status",
        "goal",
        "original_request",
        "complexity",
        "plan_version",
        "current_task_id",
        "context_summary",
        "base_commit_sha",
        "final_commit_sha",
        "provider",
        "model",
        "credit_budget",
        "credits_used",
        "token_usage_json",
        "cancel_requested",
        "created_at",
        "started_at",
        "updated_at",
        "finished_at",
        "error_code",
        "error_message",
        "metadata_json",
        "dashboard_link",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="Дашборд")
    def dashboard_link(self, obj) -> str:
        url = reverse("admin_orchestration_run_detail", args=[obj.id])
        return format_html('<a href="{}">Открыть</a>', url)
