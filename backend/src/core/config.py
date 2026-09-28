from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROUTERAI_DEFAULT_BASE_URL = "https://routerai.ru/api/v1"


class Settings(BaseSettings):
    app_name: str = "AIRuntime API"
    api_prefix: str = "/api/v1"
    debug: bool = False
    environment: str = "development"
    # Console log verbosity for both the API and worker processes (see core/logging_setup.py).
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@postgres:5432/airuntime"
    )
    redis_url: str = "redis://redis:6379/0"

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    otp_expire_minutes: int = 10
    app_encryption_key: str | None = None

    # Billing conversion used to turn the provider's USD token price into platform credits.
    # One ruble equals 100 credits (the same ratio used by top-ups); the FX rate is explicit so
    # operators can update it without a code deploy while historical ledger amounts stay fixed.
    billing_credits_per_rub: int = 100
    billing_usd_to_rub: int = 100
    provider_name: str = "openai"
    openai_api_key: str | None = None
    # When set, Codex CLI talks to this OpenAI-compatible Responses endpoint instead of
    # api.openai.com (login is skipped; auth is a Bearer token from OPENAI_API_KEY).
    openai_base_url: str | None = ROUTERAI_DEFAULT_BASE_URL
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    # Optional dedicated RouterAI platform key. Empty = reuse OPENAI_API_KEY (the proxy token).
    routerai_api_key: str | None = None
    # Quality-first coding defaults. Cost/latency are deliberately secondary for the primary
    # OpenAI path; gpt-5.6-sol is the current frontier model for complex coding work.
    default_model_openai: str = "gpt-5.6-sol"
    default_model_anthropic: str = "claude-sonnet-5"
    default_model_gemini: str = "gemini-2.5-pro"
    default_model_openrouter: str = "openai/gpt-5.6-sol"
    default_model_routerai: str = "openai/gpt-5.6-sol"

    app_domain: str = "airuntime.ru"
    frontend_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    public_base_domain: str | None = None
    allowed_origins: str = "http://localhost:3000"
    allowed_hosts: str = "localhost,127.0.0.1"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "noreply@airuntime.ru"
    smtp_from_name: str = "AIRuntime"
    smtp_reply_to: str | None = None
    smtp_use_tls: bool = True
    support_email: str | None = "support@airuntime.ru"

    # --- MAX messenger surface -------------------------------------------------------
    # One platform-owned bot serves every tenant, so the token lives here rather than in
    # per-project secrets (those stay for the bots users generate for themselves).
    max_bot_token: str | None = None
    # Used to build deep links: https://max.ru/<username>?startapp=<slug>
    max_bot_username: str | None = None
    # Stable numeric bot id from GET /me. Current MAX clients use it to resolve open_app
    # buttons reliably even when username lookup is unavailable or stale.
    max_bot_id: int | None = None
    max_api_base_url: str = "https://platform-api2.max.ru"
    # Shared secret in the webhook path. MAX does not sign deliveries, so an unguessable
    # path is what keeps the endpoint from accepting forged updates.
    max_webhook_secret: str | None = None
    # Where the mini app is served. Defaults to <frontend>/max.
    max_miniapp_url: str | None = None
    # MAX recommends invalidating launch parameters after an hour.
    max_init_data_max_age_seconds: int = 3600
    # platform-api2.max.ru is signed by the Ministry of Digital Development's CA, which is
    # in no default trust store. The backend image installs it system-wide, so this is only
    # needed when running outside the container (scripts, local uvicorn). Empty = system store.
    max_ca_bundle: str | None = None
    # The wizard answers in a chat, so it takes the plain HTTP path rather than the Codex
    # runner - and then the platform's default model name is the wrong vocabulary: it names
    # a model for the Codex CLI, which the OpenAI-compatible proxy answers with
    # `Model '...' not found`. Named separately so the wizard cannot silently degrade to its
    # keyword fallback when the platform default moves. Must exist on OPENAI_BASE_URL.
    # MAX storefronts are full customer-facing sites, not a throwaway chat completion.
    # Use the same quality tier as regular AIRuntime generation; owners accepted the extra
    # latency in exchange for art direction, information architecture and better copy.
    max_wizard_model: str = "openai/gpt-5.6-sol"
    # Banner on the bot's welcome message. MAX downloads it from this URL itself, so it
    # must be public HTTPS. Empty = <frontend>/brand/max-welcome-v7.jpg; "-" = no banner.
    # The filename is the cache-buster: MAX reuses a previously fetched URL, so a new
    # picture has to live at a new path or the chat keeps showing the old one.
    max_welcome_image_url: str | None = None

    # Codex CLI runner (replaces direct provider HTTP calls for the "openai" path - see
    # backend/src/services/agent/codex_runtime.py). Other providers keep the old HTTP path.
    # A fresh, single-purpose container per turn (see codex_worker.py) built from this image -
    # not a long-lived named container anymore, so codex_container_name is gone.
    codex_image: str = "airuntime-codex"
    # A real coding turn (write/fix a multi-file project, rebuild until it passes) can
    # legitimately run long - give it room to work without getting cut off mid-task.
    codex_turn_timeout_seconds: int = 1800
    # Planning/review calls also use the frontier model at maximum reasoning effort. A 45-second
    # timeout made that quality setting self-defeating on harder prompts, so allow five minutes.
    codex_simple_timeout_seconds: int = 300
    codex_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "max"
    codex_memory_limit: str = "2g"
    codex_cpu_limit: str = "2.0"
    # Docker network the per-turn Codex container joins - needed so it can resolve
    # docker-socket-proxy by name when codex_docker_host is set. Compose's default network name
    # for this project is "airuntime_default" (<compose project name>_default); override via env
    # if deployed under a different COMPOSE_PROJECT_NAME / -p.
    codex_network: str | None = "airuntime_default"
    # docker-socket-proxy address (e.g. "tcp://docker-socket-proxy:2375") for the per-turn
    # container's own docker build/run calls. None (default) keeps the pre-isolation behavior of
    # bind-mounting the raw host socket into it - opt in once the proxy service is deployed (see
    # docker-compose.yml's docker-socket-proxy service).
    codex_docker_host: str | None = None
    # Real host path of the named volume backing generated_projects_dir, looked up via the
    # Docker API at run time (see codex_worker.py's _resolve_project_mount) so a per-turn
    # container can bind-mount just one project's subtree instead of the whole shared volume.
    # Compose prefixes volume names with the project name ("airuntime" from this file's `name:`
    # key) - verify against `docker volume ls` on the target host if COMPOSE_PROJECT_NAME differs.
    generated_projects_volume_name: str = "airuntime_airruntime_projects_data"

    # Persistent orchestration engine (backend/src/services/orchestration/) - the only chat-turn
    # path (backend/src/api/routers/chat.py always uses it; the pre-engine event_source()/
    # run_product_pipeline path was removed once the engine covered its required-files/
    # deploy-gate safety net too - see chat.py's _orchestration_event_source). Every capability
    # tier (specialist agents, skills, MCP, worktree isolation, replanning) always runs; there
    # are no on/off switches left for any of them. OrchestrationRun/Plan/AgentTask rows are
    # always persisted to Postgres - that's what makes a run survive a restart, not optional
    # infrastructure to gate.

    orchestration_run_lease_ttl_seconds: int = 180
    orchestration_task_default_timeout_seconds: int = 1800
    # Safety ceiling on how many nodes one plan graph may contain - mirrors the old
    # orchestrator.py's _MAX_SUBTASKS=4 in spirit but larger, since this plans a real DAG
    # (independent read-only + isolated-write parallelism) rather than N sequential text
    # chunks against one shared workspace.
    orchestration_max_plan_tasks: int = 16
    # Loop-detection ceiling (failure_policy.py) - a run that would need more replans than this
    # to converge stops and asks the user instead of grinding forever. Build/compile failures
    # still replan; QA taste is no longer a Ralph loop (see orchestration_max_review_rounds).
    orchestration_max_replans: int = 5
    # After the first independent QA verdict the engine may enqueue at most this many
    # implementer fix rounds (each optionally followed by one more judge pass). 1 = judge →
    # TODO list → implementer → optional second judge → ship, even if that judge still revises.
    orchestration_max_review_rounds: int = 1
    orchestration_max_task_attempts: int = 3
    # None = no per-run cap beyond the user's own credit balance (billing.py still gates that).
    orchestration_default_credit_budget: int | None = None
    orchestration_event_backlog_limit: int = 2000
    # Dedicated per-turn Playwright container image (deployment/preview/Dockerfile), built the
    # same way as codex_image (`docker compose build preview`, profiles: [build-only]). Kept out
    # of backend/worker's own Dockerfile so neither image carries browser weight.
    preview_image: str = "airuntime-preview"
    preview_timeout_seconds: int = 90
    preview_memory_limit: str = "1g"
    preview_cpu_limit: str = "1.0"

    docker_binary: str = "docker"
    deployment_port_base: int = 18000
    deployment_memory_limit: str = "512m"
    deployment_cpu_limit: str = "1.0"
    deployment_timeout_seconds: int = 120
    deployment_default_image: str = "nginx:alpine"
    # Persist generated project sources so they survive container restarts.
    generated_projects_dir: str = "/data/airruntime-projects"
    # Host directory for project sidecar data (Postgres/Redis/…). Bind-mounted into
    # service containers so rebuild/redeploy keeps data. Env: DEPLOYMENT_VOLUMES_DIR
    # (also accepts AIRUNTIME_VOLUMES_DIR). Default is durable on the server, not /tmp.
    deployment_volumes_dir: str = "/var/lib/airuntime/volumes"
    auto_deploy_websites: bool = True
    max_running_projects_per_user: int = 3
    deployment_public_network: str | None = None
    deployment_expose_host_ports: bool = True
    deployment_service_memory_limit: str = "256m"
    deployment_service_cpu_limit: str = "0.5"
    max_services_per_project: int = 100

    cf_zone_id: str | None = None
    cf_api_token: str | None = None
    server_ip: str | None = None

    s3_endpoint_url: str | None = "http://minio:9000"
    s3_public_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "airuntime"
    s3_secret_key: str = "airuntime-secret"
    s3_bucket: str = "airuntime-files"
    s3_region: str = "us-east-1"
    s3_max_upload_bytes: int = 10 * 1024 * 1024
    s3_presign_expire_seconds: int = 3600

    # Optional shared secret for POST /analytics/batch. Empty = accept all (dev default).
    analytics_ingest_key: str | None = None

    # Robokassa merchant credentials. Empty = invoices stay manual (admin marks paid).
    # Password #1 signs the payment form and Success URL; #2 verifies Result URL;
    # #3 is the XML API password (status checks / refunds) — stored for later use.
    robokassa_merchant_login: str | None = None
    robokassa_password1: str | None = None
    robokassa_password2: str | None = None
    robokassa_password3: str | None = None
    robokassa_test_mode: bool = False
    robokassa_hash_algorithm: str = "md5"

    @field_validator(
        "s3_endpoint_url",
        "s3_public_endpoint_url",
        "openai_base_url",
        "analytics_ingest_key",
        "robokassa_merchant_login",
        "robokassa_password1",
        "robokassa_password2",
        "robokassa_password3",
        mode="before",
    )
    @classmethod
    def empty_endpoint_to_none(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @property
    def resolved_app_domain(self) -> str:
        return self.public_base_domain or self.app_domain

    @property
    def resolved_frontend_url(self) -> str:
        return self.frontend_url

    @property
    def resolved_max_miniapp_url(self) -> str:
        return (self.max_miniapp_url or f"{self.resolved_frontend_url.rstrip('/')}/max").rstrip("/")

    @property
    def resolved_max_welcome_image_url(self) -> str:
        configured = (self.max_welcome_image_url or "").strip()
        if configured == "-":
            return ""
        return configured or f"{self.resolved_frontend_url.rstrip('/')}/brand/max-welcome-v7.jpg"

    def build_max_service_link(self, slug: str) -> str:
        """Deep link that opens the storefront for ``slug`` inside the MAX bot."""
        username = (self.max_bot_username or "").strip().lstrip("@")
        return f"https://max.ru/{username}?startapp={slug}" if username else ""

    @property
    def resolved_api_url(self) -> str:
        return self.api_url

    @property
    def robokassa_enabled(self) -> bool:
        return bool(
            self.robokassa_merchant_login and self.robokassa_password1 and self.robokassa_password2
        )

    def build_project_url(self, subdomain: str) -> str:
        return f"https://{subdomain}.{self.resolved_app_domain}"

    @property
    def allowed_origins_list(self) -> list[str]:
        origins = [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]
        if self.resolved_frontend_url not in origins:
            origins.append(self.resolved_frontend_url)
        return origins

    @property
    def allowed_hosts_list(self) -> list[str]:
        hosts = [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]
        hosts.append(self.resolved_app_domain)
        return list(dict.fromkeys(hosts))

    def validate_production(self) -> None:
        if self.environment != "production":
            return
        weak = {"change-me", "change-me-in-production", "test-secret"}
        if self.jwt_secret_key in weak:
            raise RuntimeError("JWT_SECRET_KEY must be set for production")
        if not self.app_encryption_key:
            raise RuntimeError("APP_ENCRYPTION_KEY must be set for production")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
settings.validate_production()

# OpenAI auto-select floor (product / API slug).
OPENAI_MODEL_FLOOR = "gpt-5.6-sol"

SUPPORTED_LLM_PROVIDERS = ("openai", "anthropic", "gemini", "openrouter", "routerai")

# Ranked allowlists for auto-select when admin has no preferred_models list.
# Newest / strongest coding-capable models first. This is intentionally quality-first.
CURATED_TOP_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ],
    "anthropic": [
        "claude-sonnet-5",
        "claude-opus-4-8",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
    ],
    "openrouter": [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        "anthropic/claude-sonnet-5",
        "google/gemini-2.5-pro",
    ],
    "routerai": [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "google/gemini-2.5-pro",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.5-flash",
    ],
}
