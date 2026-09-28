from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from core.models import ModerationEvent
from domain.models import (
    BlockedProject,
    DomainAppUser,
    DomainCreditLedgerEntry,
    DomainCreditTopUp,
    DomainDeployment,
    DomainModerationEvent,
    DomainPlan,
    DomainPlanChangeRequest,
    DomainProject,
    DomainSecret,
    DomainUserProviderCredential,
)
from domain.product_links import collect_product_links, link_display_text


@admin.register(DomainAppUser)
class DomainAppUserAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "role",
        "plan",
        "credits_balance",
        "billing_period_end",
        "is_verified",
        "is_banned",
        "support_chat_link",
        "flagged_count",
        "deleted_count",
        "created_at",
    )
    search_fields = ("email",)
    list_filter = ("role", "plan", "is_verified", "is_banned")
    readonly_fields = ("id", "password_hash", "created_at", "updated_at", "support_open_link")
    actions = ["ban_users", "unban_users"]

    def flagged_count(self, obj) -> int:
        return ModerationEvent.objects.filter(user_id=obj.id, action="flagged").count()

    flagged_count.short_description = "Раз помечено"

    def deleted_count(self, obj) -> int:
        return ModerationEvent.objects.filter(user_id=obj.id, action="deleted").count()

    deleted_count.short_description = "Раз удалено"

    def support_chat_link(self, obj) -> str:
        url = reverse("admin_support_open_user", args=[obj.id])
        return format_html('<a href="{}">Поддержка</a>', url)

    support_chat_link.short_description = "Чат"

    def support_open_link(self, obj) -> str:
        if not obj or not obj.id:
            return "—"
        url = reverse("admin_support_open_user", args=[obj.id])
        return format_html('<a class="button" href="{}">Открыть чат поддержки</a>', url)

    support_open_link.short_description = "Поддержка"

    @admin.action(description="Забанить выбранных пользователей")
    def ban_users(self, request, queryset):
        updated = queryset.update(is_banned=True, banned_reason="Заблокирован администратором")
        self.message_user(request, f"Заблокировано пользователей: {updated}", messages.SUCCESS)

    @admin.action(description="Разбанить выбранных пользователей")
    def unban_users(self, request, queryset):
        updated = queryset.update(is_banned=False, banned_reason=None)
        self.message_user(request, f"Разблокировано пользователей: {updated}", messages.SUCCESS)


def _product_links_html(obj) -> str:
    pairs = collect_product_links(obj)
    if not pairs:
        return "—"
    return format_html_join(
        " · ",
        '<a href="{}" target="_blank" rel="noopener noreferrer">{} · {}</a>',
        ((href, label, link_display_text(href)) for label, href in pairs),
    )


@admin.register(DomainProject)
class DomainProjectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "type",
        "status",
        "product_links",
        "user",
        "orchestration_link",
        "created_at",
    )
    search_fields = ("name", "description", "deployment_url", "deploy_subdomain")
    list_filter = ("type", "status")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "product_links",
        "orchestration_dashboard_link",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "type",
                    "status",
                    "user",
                    "product_links",
                    "orchestration_dashboard_link",
                )
            },
        ),
        ("Описание", {"fields": ("description",)}),
        ("Деплой", {"fields": ("deployment_url", "deploy_subdomain")}),
        (
            "Служебное",
            {
                "fields": (
                    "id",
                    "logs",
                    "git_history",
                    "blocked_reason",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).exclude(status="blocked")

    @admin.display(description="Сайт / бот")
    def product_links(self, obj) -> str:
        if obj is None:
            return "—"
        return _product_links_html(obj)

    @admin.display(description="Оркестрация")
    def orchestration_link(self, obj) -> str:
        url = reverse("admin_orchestration_project_dashboard", args=[obj.id])
        return format_html('<a href="{}">Дашборд</a>', url)

    @admin.display(description="Дашборд оркестрации")
    def orchestration_dashboard_link(self, obj) -> str:
        if obj is None:
            return "—"
        url = reverse("admin_orchestration_project_dashboard", args=[obj.id])
        return format_html('<a class="button" href="{}">Открыть дашборд оркестрации</a>', url)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["show_orchestration_link"] = True
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


@admin.register(BlockedProject)
class BlockedProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "product_links", "owner_email", "short_reason", "updated_at")
    search_fields = ("name", "description", "blocked_reason", "deployment_url")
    readonly_fields = (
        "id",
        "name",
        "type",
        "user",
        "product_links",
        "blocked_reason",
        "created_at",
        "updated_at",
    )
    actions = ["unblock_projects"]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(status="blocked")

    def has_add_permission(self, request):
        return False

    def owner_email(self, obj) -> str:
        return obj.user.email

    owner_email.short_description = "Владелец"

    @admin.display(description="Сайт / бот")
    def product_links(self, obj) -> str:
        if obj is None:
            return "—"
        return _product_links_html(obj)

    def short_reason(self, obj) -> str:
        return (obj.blocked_reason or "")[:120]

    short_reason.short_description = "Причина"

    @admin.action(description="Разблокировать выбранные проекты")
    def unblock_projects(self, request, queryset):
        count = 0
        for project in queryset:
            ModerationEvent.objects.create(
                project_id=project.id,
                project_name=project.name,
                user_id=project.user_id,
                action="unblocked",
                reason="Разблокировано администратором",
            )
            project.status = "ready"
            project.blocked_reason = None
            project.save(update_fields=["status", "blocked_reason"])
            count += 1
        self.message_user(request, f"Разблокировано проектов: {count}", messages.SUCCESS)


@admin.register(DomainModerationEvent)
class DomainModerationEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "action_badge",
        "project_name",
        "user",
        "category",
        "short_reason",
    )
    list_filter = ("action", "category")
    search_fields = ("project_name", "reason", "user__email")
    readonly_fields = (
        "id",
        "project",
        "project_name",
        "user",
        "action",
        "category",
        "reason",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def short_reason(self, obj) -> str:
        return (obj.reason or "")[:160]

    short_reason.short_description = "Причина"

    def action_badge(self, obj) -> str:
        colors = {"flagged": "#dc2626", "unblocked": "#16a34a", "deleted": "#78716c"}
        color = colors.get(obj.action, "#374151")
        return format_html(
            '<span style="color:{}; font-weight:600">{}</span>', color, obj.get_action_display()
        )

    action_badge.short_description = "Действие"


@admin.register(DomainSecret)
class DomainSecretAdmin(admin.ModelAdmin):
    list_display = ("project", "key", "created_at")
    search_fields = ("key",)
    readonly_fields = ("encrypted_value",)


@admin.register(DomainDeployment)
class DomainDeploymentAdmin(admin.ModelAdmin):
    list_display = ("project", "status", "started_at", "finished_at")
    list_filter = ("status",)


@admin.register(DomainPlan)
class DomainPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "key",
        "price_rub",
        "monthly_budget_rub",
        "max_projects",
        "max_concurrent_projects",
        "grant_renews",
        "is_default",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "is_default")
    search_fields = ("name", "key")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("sort_order",)


@admin.register(DomainCreditTopUp)
class DomainCreditTopUpAdmin(admin.ModelAdmin):
    list_display = ("user", "inv_id", "credits", "amount_rub", "status", "created_at", "paid_at")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = ("id", "inv_id", "user", "credits", "amount_rub", "created_at", "credited_at")
    actions = ["mark_paid", "mark_cancelled"]

    @admin.action(description="Отметить как оплаченные")
    def mark_paid(self, request, queryset):
        from django.utils import timezone

        updated = 0
        for invoice in queryset.filter(status="pending"):
            invoice.status = "paid"
            invoice.paid_at = timezone.now()
            invoice.save(update_fields=["status", "paid_at"])
            updated += 1
        self.message_user(
            request,
            f"Отмечено оплаченными: {updated}. Кредиты будут начислены и письмо отправлено "
            "при следующем цикле обработки биллинга (в течение нескольких минут).",
            messages.SUCCESS,
        )

    @admin.action(description="Отменить выбранные счета")
    def mark_cancelled(self, request, queryset):
        updated = queryset.filter(status="pending").update(status="cancelled")
        self.message_user(request, f"Отменено счетов: {updated}", messages.SUCCESS)


@admin.register(DomainPlanChangeRequest)
class DomainPlanChangeRequestAdmin(admin.ModelAdmin):
    """Approve/reject only flips the status.

    The actual grant (balance, ledger entry, period reset, email) is done by the FastAPI
    billing sweep, which this app cannot import - same split as CreditTopUp.mark_paid.
    """

    list_display = ("user", "to_plan", "from_plan", "status", "created_at", "resolved_at")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = (
        "id",
        "user",
        "from_plan",
        "to_plan",
        "note",
        "created_at",
        "resolved_at",
        "applied_at",
    )
    ordering = ("-created_at",)
    actions = ["approve_requests", "reject_requests"]

    def _resolve(self, request, queryset, status: str) -> int:
        from django.utils import timezone

        updated = 0
        for row in queryset.filter(status="pending"):
            row.status = status
            row.resolved_at = timezone.now()
            row.resolved_by = request.user.email
            row.save(update_fields=["status", "resolved_at", "resolved_by"])
            updated += 1
        return updated

    @admin.action(description="Одобрить и подключить тариф")
    def approve_requests(self, request, queryset):
        updated = self._resolve(request, queryset, "approved")
        self.message_user(
            request,
            f"Одобрено заявок: {updated}. Бюджет будет начислен и письмо отправлено при следующем "
            "цикле обработки биллинга.",
            messages.SUCCESS,
        )

    @admin.action(description="Отклонить выбранные заявки")
    def reject_requests(self, request, queryset):
        updated = self._resolve(request, queryset, "rejected")
        self.message_user(
            request,
            f"Отклонено заявок: {updated}. Пользователю уйдёт письмо при следующем цикле "
            "обработки биллинга.",
            messages.SUCCESS,
        )


@admin.register(DomainCreditLedgerEntry)
class DomainCreditLedgerEntryAdmin(admin.ModelAdmin):
    """Read-only. `amount` is what the user was charged; provider cost is stored separately so
    margin stays computable (see core/analytics_reporting.py)."""

    list_display = ("created_at", "user", "reason", "amount", "provider", "model", "markup_percent")
    list_filter = ("reason", "provider")
    search_fields = ("user__email", "project_name", "model")
    ordering = ("-created_at",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(DomainUserProviderCredential)
class DomainUserProviderCredentialAdmin(admin.ModelAdmin):
    """The ciphertext is deliberately not exposed - only the last four characters."""

    list_display = ("user", "provider", "last4", "is_valid", "validated_at")
    list_filter = ("provider", "is_valid")
    search_fields = ("user__email",)
    readonly_fields = (
        "id",
        "user",
        "provider",
        "last4",
        "validated_at",
        "last_error",
        "created_at",
    )
    exclude = ("encrypted_key",)
    ordering = ("-created_at",)

    def has_add_permission(self, request) -> bool:
        return False
