from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0003_unmanaged_platform_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatSectionChat",
            fields=[],
            options={
                "verbose_name": "Чат",
                "verbose_name_plural": "Чаты",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.chat",),
        ),
        migrations.CreateModel(
            name="ChatSectionMessage",
            fields=[],
            options={
                "verbose_name": "Сообщение",
                "verbose_name_plural": "Сообщения",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.message",),
        ),
        migrations.CreateModel(
            name="ChatSectionFile",
            fields=[],
            options={
                "verbose_name": "Файл чата",
                "verbose_name_plural": "Файлы чата",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.chatfile",),
        ),
    ]
