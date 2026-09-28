import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0003_unmanaged_platform_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrchestrationRun",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("chat_id", models.UUIDField()),
                ("message_id", models.UUIDField(blank=True, null=True)),
                ("user_id", models.UUIDField()),
                ("status", models.CharField(max_length=32)),
                ("goal", models.TextField(blank=True, null=True)),
                ("original_request", models.TextField(blank=True, null=True)),
                ("complexity", models.CharField(blank=True, max_length=16, null=True)),
                ("plan_version", models.IntegerField(default=0)),
                ("current_task_id", models.UUIDField(blank=True, null=True)),
                ("context_summary", models.TextField(blank=True, null=True)),
                ("base_commit_sha", models.CharField(blank=True, max_length=64, null=True)),
                ("final_commit_sha", models.CharField(blank=True, max_length=64, null=True)),
                ("provider", models.CharField(blank=True, max_length=32, null=True)),
                ("model", models.CharField(blank=True, max_length=128, null=True)),
                ("credit_budget", models.IntegerField(blank=True, null=True)),
                ("credits_used", models.IntegerField(default=0)),
                ("token_usage_json", models.TextField(blank=True, null=True)),
                ("cancel_requested", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField()),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=64, null=True)),
                ("error_message", models.TextField(blank=True, null=True)),
                ("metadata_json", models.TextField(blank=True, null=True)),
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
                "db_table": "orchestration_runs",
                "managed": False,
                "verbose_name": "Запуск оркестрации",
                "verbose_name_plural": "Запуски оркестрации",
            },
        ),
        migrations.CreateModel(
            name="RunEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("task_id", models.UUIDField(blank=True, null=True)),
                ("seq", models.IntegerField()),
                ("event_type", models.CharField(max_length=64)),
                ("payload_json", models.TextField()),
                ("created_at", models.DateTimeField()),
                (
                    "run",
                    models.ForeignKey(
                        db_column="run_id",
                        on_delete=models.DO_NOTHING,
                        to="orchestration_section.orchestrationrun",
                    ),
                ),
            ],
            options={
                "db_table": "run_events",
                "managed": False,
                "verbose_name": "Событие запуска",
                "verbose_name_plural": "События запусков",
                "ordering": ["seq"],
            },
        ),
        migrations.CreateModel(
            name="AgentTask",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("plan_id", models.UUIDField()),
                ("parent_task_id", models.UUIDField(blank=True, null=True)),
                ("local_id", models.CharField(max_length=64)),
                ("sequence", models.IntegerField(default=0)),
                ("title", models.CharField(max_length=255)),
                ("role", models.CharField(max_length=64)),
                ("execution_kind", models.CharField(max_length=32)),
                ("status", models.CharField(max_length=32)),
                ("task_contract_json", models.TextField(blank=True, null=True)),
                ("result_json", models.TextField(blank=True, null=True)),
                ("evidence_json", models.TextField(blank=True, null=True)),
                ("validation_result_json", models.TextField(blank=True, null=True)),
                ("attempt", models.IntegerField(default=0)),
                ("max_attempts", models.IntegerField(default=3)),
                ("depends_on_json", models.TextField(blank=True, null=True)),
                ("skill_id", models.CharField(blank=True, max_length=64, null=True)),
                ("capability_id", models.CharField(blank=True, max_length=128, null=True)),
                ("workspace_mode", models.CharField(default="shared_sequential", max_length=32)),
                ("base_commit_sha", models.CharField(blank=True, max_length=64, null=True)),
                ("accepted_commit_sha", models.CharField(blank=True, max_length=64, null=True)),
                ("created_at", models.DateTimeField()),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=64, null=True)),
                ("error_message", models.TextField(blank=True, null=True)),
                (
                    "run",
                    models.ForeignKey(
                        db_column="run_id",
                        on_delete=models.DO_NOTHING,
                        to="orchestration_section.orchestrationrun",
                    ),
                ),
            ],
            options={
                "db_table": "agent_tasks",
                "managed": False,
                "verbose_name": "Задача агента",
                "verbose_name_plural": "Задачи агентов",
                "ordering": ["sequence", "created_at"],
            },
        ),
        migrations.CreateModel(
            name="DomainOrchestrationRun",
            fields=[],
            options={
                "verbose_name": "Запуск оркестрации",
                "verbose_name_plural": "Запуски оркестрации",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("orchestration_section.orchestrationrun",),
        ),
    ]
