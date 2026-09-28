from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0003_unmanaged_platform_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="DomainAppUser",
            fields=[],
            options={
                "verbose_name": "Пользователь платформы",
                "verbose_name_plural": "Пользователи платформы",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.appuser",),
        ),
        migrations.CreateModel(
            name="DomainProject",
            fields=[],
            options={
                "verbose_name": "Проект",
                "verbose_name_plural": "Проекты",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.project",),
        ),
        migrations.CreateModel(
            name="DomainSecret",
            fields=[],
            options={
                "verbose_name": "Секрет",
                "verbose_name_plural": "Секреты",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.secret",),
        ),
        migrations.CreateModel(
            name="DomainDeployment",
            fields=[],
            options={
                "verbose_name": "Деплой",
                "verbose_name_plural": "Деплои",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.deployment",),
        ),
    ]
