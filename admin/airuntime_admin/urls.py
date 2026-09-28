from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, reverse

from airuntime_admin import docker_views
from core import analytics_views, support_views
from orchestration_section import admin as orchestration_admin

_orig_get_app_list = admin.site.get_app_list


def _get_app_list_with_adminuser_under_auth(request, app_label=None):
    app_list = _orig_get_app_list(request, app_label=app_label)

    # We want `core.AdminUser` to show up under the same block as Django auth models
    # (Users/Groups/Permissions), without changing AUTH_USER_MODEL and migration graph.
    try:
        core_app = next((a for a in app_list if a.get("app_label") == "core"), None)
        auth_app = next((a for a in app_list if a.get("app_label") == "auth"), None)
        if not core_app or not auth_app:
            return app_list

        core_models = core_app.get("models") or []
        admin_user_idx = next(
            (i for i, m in enumerate(core_models) if m.get("object_name") == "AdminUser"),
            None,
        )
        if admin_user_idx is None:
            return app_list

        admin_user_item = core_models[admin_user_idx]
        core_app["models"] = [m for i, m in enumerate(core_models) if i != admin_user_idx]

        auth_models = auth_app.get("models") or []
        if not any(m.get("object_name") == "AdminUser" for m in auth_models):
            auth_models.append(admin_user_item)
            auth_app["models"] = auth_models
    except Exception:
        # Never break admin index rendering
        return app_list

    # Remove sections that have custom blocks on admin/index.html.
    app_list = [
        a
        for a in app_list
        if a.get("app_label") not in {"core", "orchestration_section", "product_analytics"}
    ]

    return app_list


admin.site.get_app_list = _get_app_list_with_adminuser_under_auth


def _product_analytics_redirect(request):
    return redirect(reverse("admin:product_analytics_dashboard"))


urlpatterns = [
    path(
        "docker/containers/",
        admin.site.admin_view(docker_views.docker_containers),
        name="admin_docker_containers",
    ),
    path(
        "docker/images/",
        admin.site.admin_view(docker_views.docker_images),
        name="admin_docker_images",
    ),
    path(
        "docker/resources/",
        admin.site.admin_view(docker_views.docker_resources),
        name="admin_docker_resources",
    ),
    path(
        "analytics/",
        admin.site.admin_view(analytics_views.analytics_dashboard_view),
        name="admin_analytics_dashboard",
    ),
    path(
        "analytics/export/<str:report>.csv",
        admin.site.admin_view(analytics_views.analytics_export_csv_view),
        name="admin_analytics_export",
    ),
    path(
        "orchestration/project/<uuid:project_id>/",
        admin.site.admin_view(orchestration_admin.project_dashboard_view),
        name="admin_orchestration_project_dashboard",
    ),
    path(
        "orchestration/run/<uuid:run_id>/",
        admin.site.admin_view(orchestration_admin.run_detail_view),
        name="admin_orchestration_run_detail",
    ),
    path(
        "orchestration/analytics/",
        admin.site.admin_view(orchestration_admin.fleet_analytics_dashboard_view),
        name="admin_orchestration_analytics",
    ),
    path(
        "orchestration/analytics/live.json",
        admin.site.admin_view(orchestration_admin.fleet_analytics_live_view),
        name="admin_orchestration_analytics_live",
    ),
    path(
        "product-analytics/",
        admin.site.admin_view(_product_analytics_redirect),
        name="admin_product_analytics",
    ),
    path(
        "support/open/",
        admin.site.admin_view(support_views.SupportChatBridgeView.as_view()),
        name="admin_support_open",
    ),
    path(
        "support/open/<uuid:user_id>/",
        admin.site.admin_view(support_views.SupportChatBridgeView.as_view()),
        name="admin_support_open_user",
    ),
    path("", admin.site.urls),
]

admin.site.site_header = "AIRuntime Admin"
admin.site.site_title = "AIRuntime"
admin.site.index_title = ""
