"""Unmanaged mirrors of product analytics tables."""

from django.db import models


class AnalyticsSession(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    anonymous_id = models.CharField(max_length=64, db_index=True)
    user_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    platform = models.CharField(max_length=16, db_index=True)
    entry_screen = models.CharField(max_length=64, null=True, blank=True)
    referrer = models.TextField(null=True, blank=True)
    utm_source = models.CharField(max_length=128, null=True, blank=True)
    utm_medium = models.CharField(max_length=128, null=True, blank=True)
    utm_campaign = models.CharField(max_length=128, null=True, blank=True)
    started_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "analytics_sessions"
        verbose_name = "Сессия"
        verbose_name_plural = "Продуктовая аналитика"


class AnalyticsEvent(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    session = models.ForeignKey(
        AnalyticsSession,
        on_delete=models.DO_NOTHING,
        db_column="session_id",
        related_name="events",
    )
    anonymous_id = models.CharField(max_length=64, db_index=True)
    user_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    name = models.CharField(max_length=64, db_index=True)
    screen = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    tab = models.CharField(max_length=32, null=True, blank=True)
    platform = models.CharField(max_length=16, db_index=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    props = models.JSONField(null=True, blank=True)
    occurred_at = models.DateTimeField(db_index=True)

    class Meta:
        managed = False
        db_table = "analytics_events"
        verbose_name = "Событие"
        verbose_name_plural = "События продукта"
