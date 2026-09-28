from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Система"

    def ready(self) -> None:
        from core import signals  # noqa: F401
