import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_systemsetting"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppUser",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("password_hash", models.CharField(blank=True, max_length=255, null=True)),
                ("is_verified", models.BooleanField(default=False)),
                ("role", models.CharField(default="user", max_length=50)),
                ("credits_balance", models.IntegerField(default=1_000_000_000)),
                ("onboarding_completed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
            ],
            options={
                "db_table": "users",
                "managed": False,
                "verbose_name": "Пользователь",
                "verbose_name_plural": "Пользователи",
            },
        ),
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("type", models.CharField(max_length=32)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("status", models.CharField(max_length=50)),
                ("logs", models.TextField()),
                ("deployment_url", models.CharField(blank=True, max_length=512, null=True)),
                ("deploy_subdomain", models.CharField(blank=True, max_length=63, null=True)),
                ("git_history", models.TextField()),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=models.DO_NOTHING,
                        to="core.appuser",
                    ),
                ),
            ],
            options={
                "db_table": "projects",
                "managed": False,
                "verbose_name": "Проект",
                "verbose_name_plural": "Проекты",
            },
        ),
        migrations.CreateModel(
            name="Chat",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                (
                    "project",
                    models.ForeignKey(
                        db_column="project_id",
                        on_delete=models.DO_NOTHING,
                        to="core.project",
                    ),
                ),
            ],
            options={
                "db_table": "chats",
                "managed": False,
                "verbose_name": "Чат",
                "verbose_name_plural": "Чаты",
            },
        ),
        migrations.CreateModel(
            name="Message",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("role", models.CharField(max_length=32)),
                ("content_markdown", models.TextField()),
                ("metadata_json", models.TextField()),
                ("created_at", models.DateTimeField()),
                (
                    "chat",
                    models.ForeignKey(
                        db_column="chat_id",
                        on_delete=models.DO_NOTHING,
                        to="core.chat",
                    ),
                ),
            ],
            options={
                "db_table": "messages",
                "managed": False,
                "verbose_name": "Сообщение",
                "verbose_name_plural": "Сообщения",
            },
        ),
        migrations.CreateModel(
            name="Deployment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(max_length=32)),
                ("image_ref", models.CharField(blank=True, max_length=512, null=True)),
                ("container_id", models.CharField(blank=True, max_length=255, null=True)),
                ("logs_ref", models.CharField(blank=True, max_length=512, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "project",
                    models.ForeignKey(
                        db_column="project_id",
                        on_delete=models.DO_NOTHING,
                        to="core.project",
                    ),
                ),
            ],
            options={
                "db_table": "deployments",
                "managed": False,
                "verbose_name": "Деплой",
                "verbose_name_plural": "Деплои",
            },
        ),
        migrations.CreateModel(
            name="Secret",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("key", models.CharField(max_length=255)),
                ("encrypted_value", models.TextField()),
                ("kms_key_id", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                (
                    "project",
                    models.ForeignKey(
                        db_column="project_id",
                        on_delete=models.DO_NOTHING,
                        to="core.project",
                    ),
                ),
            ],
            options={
                "db_table": "secrets",
                "managed": False,
                "verbose_name": "Секрет",
                "verbose_name_plural": "Секреты",
            },
        ),
        migrations.CreateModel(
            name="ChatFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("object_key", models.CharField(max_length=1024)),
                ("original_filename", models.CharField(max_length=512)),
                ("content_type", models.CharField(max_length=255)),
                ("size_bytes", models.IntegerField()),
                ("created_at", models.DateTimeField()),
                (
                    "chat",
                    models.ForeignKey(
                        db_column="chat_id",
                        on_delete=models.DO_NOTHING,
                        to="core.chat",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        blank=True,
                        db_column="message_id",
                        null=True,
                        on_delete=models.DO_NOTHING,
                        to="core.message",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        db_column="project_id",
                        on_delete=models.DO_NOTHING,
                        to="core.project",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=models.DO_NOTHING,
                        to="core.appuser",
                    ),
                ),
            ],
            options={
                "db_table": "chat_files",
                "managed": False,
                "verbose_name": "Файл чата",
                "verbose_name_plural": "Файлы чата",
            },
        ),
    ]
