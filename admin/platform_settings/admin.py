from django.contrib import admin

from platform_settings.models import PlatformSystemSetting


@admin.register(PlatformSystemSetting)
class PlatformSystemSettingAdmin(admin.ModelAdmin):
    list_display = ("title", "key", "setting_type", "is_enabled", "updated_at")
    list_filter = ("setting_type", "is_enabled")
    search_fields = ("title", "key", "description")
