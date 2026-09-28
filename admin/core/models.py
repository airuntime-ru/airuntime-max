import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class AdminUserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class AdminUser(AbstractBaseUser, PermissionsMixin):
    id = models.BigAutoField(primary_key=True)
    email = models.EmailField(unique=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = AdminUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "django_admin_users"
        verbose_name = "Системный пользователь"
        verbose_name_plural = "Системные пользователи"


class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    monthly_budget_rub = models.IntegerField(default=0)
    max_concurrent_projects = models.IntegerField()
    max_projects = models.IntegerField(default=1)
    price_rub = models.IntegerField(default=0)
    grant_renews = models.BooleanField(default=True)
    allowed_models = models.JSONField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "plans"
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"

    def __str__(self) -> str:
        return self.name


class AppUser(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    password_hash = models.CharField(max_length=255, null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    role = models.CharField(max_length=50, default="user")
    credits_balance = models.IntegerField(default=0)
    onboarding_completed = models.BooleanField(default=False)
    is_banned = models.BooleanField(default=False)
    banned_reason = models.CharField(max_length=500, null=True, blank=True)
    plan = models.ForeignKey(
        Plan, on_delete=models.DO_NOTHING, db_column="plan_id", null=True, blank=True
    )
    billing_period_start = models.DateTimeField(null=True, blank=True)
    billing_period_end = models.DateTimeField(null=True, blank=True)
    low_credits_notified_at = models.DateTimeField(null=True, blank=True)
    period_ending_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.email


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    type = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(max_length=50)
    logs = models.TextField()
    deployment_url = models.CharField(max_length=512, null=True, blank=True)
    deploy_subdomain = models.CharField(max_length=63, null=True, blank=True)
    custom_domain = models.CharField(max_length=253, null=True, blank=True)
    custom_domain_status = models.CharField(max_length=20, default="none")
    git_history = models.TextField()
    blocked_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "projects"
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"

    def __str__(self) -> str:
        return self.name


class Chat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, db_column="project_id")
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "chats"
        verbose_name = "Чат"
        verbose_name_plural = "Чаты"

    def __str__(self) -> str:
        return self.title


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(Chat, on_delete=models.DO_NOTHING, db_column="chat_id")
    role = models.CharField(max_length=32)
    content_markdown = models.TextField()
    metadata_json = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "messages"
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"


class Deployment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, db_column="project_id")
    status = models.CharField(max_length=32)
    image_ref = models.CharField(max_length=512, null=True, blank=True)
    container_id = models.CharField(max_length=255, null=True, blank=True)
    logs_ref = models.CharField(max_length=512, null=True, blank=True)
    error_text = models.TextField(null=True, blank=True)
    log_text = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "deployments"
        verbose_name = "Деплой"
        verbose_name_plural = "Деплои"


class Secret(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, db_column="project_id")
    key = models.CharField(max_length=255)
    encrypted_value = models.TextField()
    kms_key_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "secrets"
        verbose_name = "Секрет"
        verbose_name_plural = "Секреты"


class RefreshToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    token_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "refresh_tokens"
        verbose_name = "Refresh-токен"
        verbose_name_plural = "Refresh-токены"


class ChatFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, db_column="project_id")
    chat = models.ForeignKey(Chat, on_delete=models.DO_NOTHING, db_column="chat_id")
    message = models.ForeignKey(
        Message, on_delete=models.DO_NOTHING, db_column="message_id", null=True, blank=True
    )
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    object_key = models.CharField(max_length=1024)
    original_filename = models.CharField(max_length=512)
    content_type = models.CharField(max_length=255)
    size_bytes = models.IntegerField()
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "chat_files"
        verbose_name = "Файл чата"
        verbose_name_plural = "Файлы чата"


class ModerationEvent(models.Model):
    ACTIONS = [
        ("flagged", "Заблокирован"),
        ("unblocked", "Разблокирован"),
        ("deleted", "Удалён"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.DO_NOTHING, db_column="project_id", null=True, blank=True
    )
    project_name = models.CharField(max_length=255)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    action = models.CharField(max_length=32, choices=ACTIONS)
    category = models.CharField(max_length=64, null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "moderation_events"
        verbose_name = "Событие модерации"
        verbose_name_plural = "История модерации"

    def __str__(self) -> str:
        return f"{self.get_action_display()} - {self.project_name}"


class CreditTopUp(models.Model):
    STATUSES = [
        ("pending", "Ожидает оплаты"),
        ("paid", "Оплачен"),
        ("cancelled", "Отменён"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inv_id = models.IntegerField(null=True, blank=True)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    credits = models.IntegerField()
    amount_rub = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    credited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "credit_topups"
        verbose_name = "Пополнение баланса"
        verbose_name_plural = "Пополнения баланса"

    def __str__(self) -> str:
        return f"{self.user.email} - {self.credits} кредитов"


class SystemSetting(models.Model):
    SETTING_TYPES = [
        ("api_key", "API ключ"),
        ("limit", "Лимит"),
        ("feature_toggle", "Фича-тогл"),
        ("periodic_task", "Периодическая задача"),
        ("other", "Прочее"),
    ]

    id = models.BigAutoField(primary_key=True)
    key = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=255)
    setting_type = models.CharField(max_length=32, choices=SETTING_TYPES, default="other")
    value_text = models.TextField(blank=True, default="")
    value_json = models.JSONField(blank=True, null=True)
    value_number = models.IntegerField(blank=True, null=True)
    is_enabled = models.BooleanField(default=True)
    cron_expression = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "admin_system_settings"
        verbose_name = "Системная настройка"
        verbose_name_plural = "Системные настройки"

    def __str__(self) -> str:
        return self.title


class PlanChangeRequest(models.Model):
    """User application to move to another plan. Approving it is what actually grants the budget."""

    STATUS_CHOICES = [
        ("pending", "На рассмотрении"),
        ("approved", "Одобрена"),
        ("rejected", "Отклонена"),
        ("cancelled", "Отменена"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    from_plan = models.ForeignKey(
        Plan,
        on_delete=models.DO_NOTHING,
        db_column="from_plan_id",
        null=True,
        blank=True,
        related_name="plan_requests_from",
    )
    to_plan = models.ForeignKey(
        Plan,
        on_delete=models.DO_NOTHING,
        db_column="to_plan_id",
        related_name="plan_requests_to",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    note = models.TextField(null=True, blank=True)
    admin_note = models.TextField(null=True, blank=True)
    resolved_by = models.CharField(max_length=255, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    # Set by the FastAPI billing sweep once the grant has actually been issued.
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "plan_change_requests"
        verbose_name = "Заявка на смену тарифа"
        verbose_name_plural = "Заявки на смену тарифа"

    def __str__(self) -> str:
        return f"{self.user.email} → {self.to_plan.name} ({self.get_status_display()})"


class CreditLedgerEntry(models.Model):
    """Read-only mirror of the FastAPI credit ledger, used by the analytics dashboard.

    `amount` is what the user was charged (negative for usage, markup already applied);
    `provider_cost_usd_micros` is the raw provider price. Keeping both is what makes margin
    computable. Rows with reason="byok_usage" have amount 0 - the user paid their own provider.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    project_id = models.UUIDField(null=True, blank=True)
    project_name = models.CharField(max_length=255, null=True, blank=True)
    amount = models.IntegerField()
    reason = models.CharField(max_length=32)
    provider = models.CharField(max_length=32, null=True, blank=True)
    model = models.CharField(max_length=128, null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    cached_input_tokens = models.IntegerField(null=True, blank=True)
    cache_write_input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    provider_cost_usd_micros = models.IntegerField(null=True, blank=True)
    markup_percent = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "credit_ledger_entries"
        verbose_name = "Операция с кредитами"
        verbose_name_plural = "Операции с кредитами"

    def __str__(self) -> str:
        return f"{self.reason} {self.amount}"


class UserProviderCredential(models.Model):
    """BYOK key. The ciphertext is never exposed in the admin - only the last four characters."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(AppUser, on_delete=models.DO_NOTHING, db_column="user_id")
    provider = models.CharField(max_length=32)
    encrypted_key = models.TextField()
    last4 = models.CharField(max_length=8)
    is_valid = models.BooleanField(default=False)
    validated_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "user_provider_credentials"
        verbose_name = "Свой API-ключ"
        verbose_name_plural = "Свои API-ключи"

    def __str__(self) -> str:
        return f"{self.user.email} · {self.provider} ····{self.last4}"
