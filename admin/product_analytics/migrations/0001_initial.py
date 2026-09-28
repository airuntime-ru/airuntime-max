from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AnalyticsSession",
            fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("anonymous_id", models.CharField(db_index=True, max_length=64)),
                ("user_id", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("platform", models.CharField(db_index=True, max_length=16)),
                ("entry_screen", models.CharField(blank=True, max_length=64, null=True)),
                ("referrer", models.TextField(blank=True, null=True)),
                ("utm_source", models.CharField(blank=True, max_length=128, null=True)),
                ("utm_medium", models.CharField(blank=True, max_length=128, null=True)),
                ("utm_campaign", models.CharField(blank=True, max_length=128, null=True)),
                ("started_at", models.DateTimeField(db_index=True)),
                ("last_seen_at", models.DateTimeField()),
            ],
            options={
                "db_table": "analytics_sessions",
                "managed": False,
                "verbose_name": "Сессия",
                "verbose_name_plural": "Продуктовая аналитика",
            },
        ),
        migrations.CreateModel(
            name="AnalyticsEvent",
            fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("anonymous_id", models.CharField(db_index=True, max_length=64)),
                ("user_id", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("name", models.CharField(db_index=True, max_length=64)),
                ("screen", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("tab", models.CharField(blank=True, max_length=32, null=True)),
                ("platform", models.CharField(db_index=True, max_length=16)),
                ("duration_ms", models.IntegerField(blank=True, null=True)),
                ("props", models.JSONField(blank=True, null=True)),
                ("occurred_at", models.DateTimeField(db_index=True)),
                (
                    "session",
                    models.ForeignKey(
                        db_column="session_id",
                        on_delete=models.DO_NOTHING,
                        related_name="events",
                        to="product_analytics.analyticssession",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_events",
                "managed": False,
                "verbose_name": "Событие",
                "verbose_name_plural": "События продукта",
            },
        ),
    ]
