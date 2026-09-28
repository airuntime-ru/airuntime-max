from django.db import migrations


def seed_system_settings(apps, schema_editor) -> None:
    SystemSetting = apps.get_model("core", "SystemSetting")

    def upsert(key: str, **fields):
        obj, _created = SystemSetting.objects.get_or_create(key=key, defaults=fields)
        # Keep table stable across deploys
        for k, v in fields.items():
            setattr(obj, k, v)
        obj.save(update_fields=list(fields.keys()) + ["updated_at"])

    upsert(
        "openai_api_key",
        title="OpenAI API ключ",
        setting_type="api_key",
        value_text="",
        is_enabled=True,
        description="Ключ для OpenAI (не заполнять, если используешь другой провайдер).",
    )
    upsert(
        "max_projects_per_user",
        title="Лимит проектов на пользователя",
        setting_type="limit",
        value_number=50,
        is_enabled=True,
        description="Максимум проектов, которые может создать один пользователь.",
    )
    upsert(
        "enable_chat_files",
        title="Включить файлы в чате",
        setting_type="feature_toggle",
        value_json={"enabled": True},
        is_enabled=True,
        description="Разрешает прикрепление файлов к сообщениям в чате.",
    )
    upsert(
        "worker_periodic_cleanup",
        title="Периодическая очистка",
        setting_type="periodic_task",
        cron_expression="0 3 * * *",
        is_enabled=False,
        description="Ежедневная задача для очистки временных данных.",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_systemsetting"),
    ]

    operations = [
        migrations.RunPython(seed_system_settings, reverse_code=migrations.RunPython.noop),
    ]

