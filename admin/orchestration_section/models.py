import uuid

from django.db import models


class OrchestrationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("core.Project", on_delete=models.DO_NOTHING, db_column="project_id")
    chat_id = models.UUIDField()
    message_id = models.UUIDField(null=True, blank=True)
    user_id = models.UUIDField()

    status = models.CharField(max_length=32)
    goal = models.TextField(null=True, blank=True)
    original_request = models.TextField(null=True, blank=True)
    complexity = models.CharField(max_length=16, null=True, blank=True)
    plan_version = models.IntegerField(default=0)
    current_task_id = models.UUIDField(null=True, blank=True)

    context_summary = models.TextField(null=True, blank=True)

    base_commit_sha = models.CharField(max_length=64, null=True, blank=True)
    final_commit_sha = models.CharField(max_length=64, null=True, blank=True)

    provider = models.CharField(max_length=32, null=True, blank=True)
    model = models.CharField(max_length=128, null=True, blank=True)

    credit_budget = models.IntegerField(null=True, blank=True)
    credits_used = models.IntegerField(default=0)
    token_usage_json = models.TextField(null=True, blank=True)

    cancel_requested = models.BooleanField(default=False)

    created_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    metadata_json = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "orchestration_runs"
        verbose_name = "Запуск оркестрации"
        verbose_name_plural = "Запуски оркестрации"

    def __str__(self) -> str:
        return f"{self.id} · {self.status}"


class RunEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(OrchestrationRun, on_delete=models.DO_NOTHING, db_column="run_id")
    task_id = models.UUIDField(null=True, blank=True)
    seq = models.IntegerField()
    event_type = models.CharField(max_length=64)
    payload_json = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "run_events"
        verbose_name = "Событие запуска"
        verbose_name_plural = "События запусков"
        ordering = ["seq"]

    def __str__(self) -> str:
        return f"#{self.seq} {self.event_type}"


class AgentTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(OrchestrationRun, on_delete=models.DO_NOTHING, db_column="run_id")
    plan_id = models.UUIDField()
    parent_task_id = models.UUIDField(null=True, blank=True)

    local_id = models.CharField(max_length=64)
    sequence = models.IntegerField(default=0)
    title = models.CharField(max_length=255)
    role = models.CharField(max_length=64)
    execution_kind = models.CharField(max_length=32)
    status = models.CharField(max_length=32)

    task_contract_json = models.TextField(null=True, blank=True)
    result_json = models.TextField(null=True, blank=True)
    evidence_json = models.TextField(null=True, blank=True)
    validation_result_json = models.TextField(null=True, blank=True)

    attempt = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)
    depends_on_json = models.TextField(null=True, blank=True)

    skill_id = models.CharField(max_length=64, null=True, blank=True)
    capability_id = models.CharField(max_length=128, null=True, blank=True)
    workspace_mode = models.CharField(max_length=32, default="shared_sequential")

    base_commit_sha = models.CharField(max_length=64, null=True, blank=True)
    accepted_commit_sha = models.CharField(max_length=64, null=True, blank=True)

    created_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "agent_tasks"
        verbose_name = "Задача агента"
        verbose_name_plural = "Задачи агентов"
        ordering = ["sequence", "created_at"]

    def __str__(self) -> str:
        return f"{self.local_id} · {self.title}"


class DomainOrchestrationRun(OrchestrationRun):
    class Meta:
        proxy = True
        verbose_name = "Запуск оркестрации"
        verbose_name_plural = "Запуски оркестрации"
