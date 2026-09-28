from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0002_systemsetting"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformSystemSetting",
            fields=[],
            options={
                "verbose_name": "Системная настройка",
                "verbose_name_plural": "Системные настройки",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.systemsetting",),
        ),
    ]
