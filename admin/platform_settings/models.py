from core.models import SystemSetting


class PlatformSystemSetting(SystemSetting):
    class Meta:
        proxy = True
        verbose_name = "Системная настройка"
        verbose_name_plural = "Системные настройки"
