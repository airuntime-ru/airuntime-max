import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.conftest import auth_tokens


@pytest.fixture(autouse=True)
def _stub_moderation_for_chat_router_tests(monkeypatch):
    """chat.py's stream handler calls `check_project_safety(...)` after the first SSE status
    frame (so a multi-minute Codex moderation call no longer holds the HTTP response at 0 bytes).
    Patching `chat_router.run_product_pipeline` does not cover this - moderation.py calls
    `codex_simple_complete` directly for the "openai" provider, see moderation.py:107-109.
    Whenever a real OPENAI_API_KEY happens to be present in the environment/.env (as it is for
    local dev), `resolve_provider_and_model` picks "openai" as configured and this fires for
    real, blocking each such test for `codex_simple_timeout_seconds` against a Redis queue
    with no worker listening before failing open (moderation.py's own fail-open contract - see
    its docstring). Stubbing it out here is behavior-neutral (tests never exercise moderation
    blocking) and keeps this file fast and deterministic regardless of what is in .env."""
    from src.api.routers import chat as chat_router
    from src.services.moderation import ModerationVerdict

    async def _fake_check_project_safety(**kwargs):
        return ModerationVerdict(blocked=False)

    monkeypatch.setattr(chat_router, "check_project_safety", _fake_check_project_safety)


def _fake_live_deployment(db, project):
    """Mark project live and return a completed Deployment row for stream wait-logic."""
    from src.db.models.deployment import Deployment

    project.status = "live"
    if not project.deployment_url:
        project.deployment_url = "https://example.airuntime.ru"
    deployment = Deployment(
        project_id=project.id,
        status="completed",
        image_ref="test:latest",
        container_id="ctr-test",
        logs_ref=None,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(project)
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment


def _write_minimal_project_files(root: Path, project_type: str) -> None:
    """Write the platform's minimal deploy-contract files (see WEBSITE_REQUIRED/TELEGRAM_REQUIRED
    in agentic_artifacts.py) so ensure_required_files() passes regardless of which type the
    project ends up classified as. There is no template-fallback in the real pipeline any more
    (chat.py's own "Шаблон не подставляется" message is the current, intentional behavior) - a
    fake agent that writes nothing can never reach the verify/deploy phases these tests exercise,
    it just short-circuits into the "agent did not produce required files" error path instead."""
    if project_type in ("website", "mixed"):
        public_dir = root / "public"
        public_dir.mkdir(parents=True, exist_ok=True)
        index = public_dir / "index.html"
        if not index.exists():
            index.write_text("<!doctype html><html><body>Test site</body></html>", encoding="utf-8")

    if project_type in ("telegram_bot", "mixed"):
        app_py = root / "app.py"
        if not app_py.exists():
            app_py.write_text(
                "import os\n\nTELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')\n",
                encoding="utf-8",
            )
        requirements = root / "requirements.txt"
        if not requirements.exists():
            requirements.write_text("aiogram\n", encoding="utf-8")

    dockerfile = root / "Dockerfile"
    if not dockerfile.exists():
        dockerfile.write_text(
            "FROM nginx:alpine\nCOPY public /usr/share/nginx/html\n"
            if project_type == "website"
            else 'FROM python:3.13-slim\nCOPY . /app\nWORKDIR /app\nCMD ["python", "app.py"]\n',
            encoding="utf-8",
        )


@pytest.fixture
def orchestration_stub(monkeypatch, tmp_path, db):
    """chat.py's only turn-producer is now the persistent orchestration engine, so these
    stream tests fake the engine's ONE executor seam (`_build_executor`, the same seam
    test_orchestration_engine_e2e.py uses) instead of a pipeline coroutine. Everything after
    the run - verify/commit/deploy-gate/deploy-wait in _orchestration_event_source - stays real,
    which is what these tests are actually about.

    Returns a `install(...)` callable; `install(...).calls` records each executed task.
    """
    from tests.conftest import TestingSessionLocal

    from src.api.routers import chat as chat_router
    from src.core.config import settings
    from src.services import project_git
    from src.services import workspace as workspace_module
    from src.services.orchestration import engine as orchestration_engine
    from src.services.orchestration import planner as orchestration_planner
    from src.services.orchestration.executors import AgentExecutionResult
    from src.services.orchestration.schemas import (
        AcceptanceCriterion,
        ExecutionPlan,
        PlannedTask,
        SpecialistRole,
        TaskResult,
    )

    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))

    # The engine runs on its own SessionLocal (a separate connection), which would not see this
    # test's uncommitted transaction - point both it and chat.py's own reference at this test's
    # connection. Same rationale as test_chat_orchestration.py's identical fixture.
    def _session_factory():
        return TestingSessionLocal(bind=db.get_bind())

    monkeypatch.setattr(chat_router, "SessionLocal", _session_factory)
    monkeypatch.setattr(orchestration_engine, "SessionLocal", _session_factory)

    def _dir(project_id):
        # engine.py imports services.workspace.project_dir AS project_workspace_dir, so the
        # engine and chat.py's own post-run checks MUST resolve the same directory - only the
        # git init the engine's first transaction needs is added here.
        root = workspace_module.project_dir(project_id)
        project_git.init_repo_if_needed(root)
        return root

    monkeypatch.setattr(orchestration_engine, "project_workspace_dir", _dir)

    class _Stub:
        def __init__(self, requested_service=None, requested_secret=None):
            self.calls: list = []
            self._service = requested_service
            self._secret = requested_secret

        async def execute(self, contract, context, cancellation):
            self.calls.append(
                {
                    "task_goal": contract.task_goal,
                    "project_goal": contract.project_goal,
                    "user_message": contract.project_goal,
                }
            )
            is_read_only = contract.forbidden_paths == ["*"]
            if not is_read_only:
                _write_minimal_project_files(
                    context.workspace_root, contract.current_state.project_type
                )
            return AgentExecutionResult(
                task_result=TaskResult(
                    status="completed",
                    summary="Готово",
                    requested_services=[self._service]
                    if self._service and not is_read_only
                    else [],
                    requested_secrets=[self._secret] if self._secret and not is_read_only else [],
                ),
                build_result={"ok": True, "log_tail": "ok"},
                preview_result={"status": "passed", "pages": []},
            )

    def _install(*, requested_service=None, requested_secret=None) -> _Stub:
        async def _fake_plan(**kwargs):  # noqa: ANN003
            return ExecutionPlan(
                goal=kwargs["user_text"].split("Запрос пользователя:\n", 1)[-1],
                complexity="simple",
                tasks=[
                    PlannedTask(
                        local_id="implementation",
                        title="Реализация",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="Выполнить запрос пользователя",
                        reason="Детерминированный API-test executor",
                        write_scope="full_workspace",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="implementation_build",
                                description="Проект собирается",
                                verification_method="build",
                            )
                        ],
                    )
                ],
            )

        stub = _Stub(requested_service=requested_service, requested_secret=requested_secret)
        monkeypatch.setattr(orchestration_planner, "complete_structured", _fake_plan)
        monkeypatch.setattr(
            orchestration_engine, "_build_executor", lambda kind, *, db, mcp_repo: stub
        )
        return stub

    return _install


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_request_code_verify_and_me(client):
    email = "user@airuntime.dev"
    issued = client.post("/api/v1/auth/request-code", json={"email": email})
    assert issued.status_code == 200
    code = issued.json()["dev_code"]
    assert code

    verified = client.post("/api/v1/auth/verify-code", json={"email": email, "code": code})
    assert verified.status_code == 200
    tokens = verified.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == email
    assert body["is_verified"] is True
    # The default plan's one-time signup grant (100 ₽ -> 10 000 credits), not a
    # hardcoded billion (Epic A5).
    assert body["credits_balance"] == 10_000


def test_projects_crud(client):
    headers = auth_tokens(client, "builder@airuntime.dev")

    create = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Landing", "description": "Marketing site"},
    )
    assert create.status_code == 200
    project = create.json()
    assert project["name"] == "Landing"

    listed = client.get("/api/v1/projects", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["total"] == 1

    patch = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=headers,
        json={"description": "Updated description"},
    )
    assert patch.status_code == 200
    assert patch.json()["description"] == "Updated description"

    subdomain_patch = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=headers,
        json={"deploy_subdomain": "my-landing"},
    )
    assert subdomain_patch.status_code == 200
    body = subdomain_patch.json()
    assert body["deploy_subdomain"] == "my-landing"
    assert body["planned_site_url"] == "https://my-landing.airuntime.ru"


def test_delete_project_cleans_cloudflare_dns(client, monkeypatch):
    from src.api.routers import projects as projects_router

    headers = auth_tokens(client, "dns-delete@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "DNS Site", "description": ""},
    ).json()
    client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=headers,
        json={"deploy_subdomain": "dns-site"},
    )

    deleted_subdomains: list[str] = []

    def fake_delete_dns(subdomain: str):
        deleted_subdomains.append(subdomain)
        return f"{subdomain}.airuntime.ru"

    monkeypatch.setattr(projects_router, "delete_dns_for_website_deploy", fake_delete_dns)
    monkeypatch.setattr(
        projects_router,
        "submit_control_job",
        lambda **kwargs: {"ok": True},
    )

    response = client.delete(f"/api/v1/projects/{project['id']}", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert deleted_subdomains == ["dns-site"]
    assert client.get(f"/api/v1/projects/{project['id']}", headers=headers).status_code == 404


def test_delete_project_keeps_row_when_dns_cleanup_fails(client, monkeypatch):
    from src.api.routers import projects as projects_router
    from src.services.cloudflare_dns import CloudflareDnsError

    headers = auth_tokens(client, "dns-fail@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "mixed", "name": "DNS Fail", "description": ""},
    ).json()
    client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=headers,
        json={"deploy_subdomain": "dns-fail"},
    )

    def boom(_subdomain: str):
        raise CloudflareDnsError("token expired")

    monkeypatch.setattr(projects_router, "delete_dns_for_website_deploy", boom)
    monkeypatch.setattr(
        projects_router,
        "submit_control_job",
        lambda **kwargs: {"ok": True},
    )

    response = client.delete(f"/api/v1/projects/{project['id']}", headers=headers)

    assert response.status_code == 502
    assert client.get(f"/api/v1/projects/{project['id']}", headers=headers).status_code == 200


def test_delete_project_keeps_row_when_docker_cleanup_fails(client, monkeypatch):
    from src.api.routers import projects as projects_router

    headers = auth_tokens(client, "docker-cleanup-fail@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Cleanup Fail", "description": ""},
    ).json()
    monkeypatch.setattr(
        projects_router,
        "submit_control_job",
        lambda **kwargs: {"ok": False, "remaining": {"containers": ["stale"]}},
    )

    response = client.delete(f"/api/v1/projects/{project['id']}", headers=headers)

    assert response.status_code == 503
    assert client.get(f"/api/v1/projects/{project['id']}", headers=headers).status_code == 200


def test_delete_bot_project_skips_dns_cleanup(client, monkeypatch):
    from src.api.routers import projects as projects_router

    headers = auth_tokens(client, "bot-delete@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Bot Only", "description": ""},
    ).json()

    called = []

    monkeypatch.setattr(
        projects_router,
        "delete_dns_for_website_deploy",
        lambda subdomain: called.append(subdomain),
    )
    monkeypatch.setattr(
        projects_router,
        "submit_control_job",
        lambda **kwargs: {"ok": True},
    )

    response = client.delete(f"/api/v1/projects/{project['id']}", headers=headers)

    assert response.status_code == 200
    assert called == []


def test_project_type_is_inferred_when_create_payload_has_no_type(client):
    headers = auth_tokens(client, "intent@airuntime.dev")

    create = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Support automation",
            "description": "Telegram bot for client requests and notifications",
        },
    )

    assert create.status_code == 200
    assert create.json()["type"] == "telegram_bot"


def test_project_type_is_inferred_from_russian_bot_prompt(client):
    headers = auth_tokens(client, "intent-ru@airuntime.dev")

    create = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "ТГ помощник",
            "description": "напиши тг бота который на все сообщения отвечает привет",
        },
    )

    assert create.status_code == 200
    assert create.json()["type"] == "telegram_bot"


def test_project_logs_endpoint(client):
    headers = auth_tokens(client, "logs@airuntime.dev")

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Logs Project", "description": ""},
    ).json()

    response = client.get(f"/api/v1/projects/{project['id']}/logs", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["project_logs"] == ""
    assert body["deployment_status"] is None
    assert body["runtime_logs"] == ""


def test_chat_messages_list(client):
    headers = auth_tokens(client, "chat@airuntime.dev")

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Support Bot", "description": ""},
    ).json()

    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()
    message = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/messages",
        headers=headers,
        json={"content": "Hello AIRuntime"},
    )
    assert message.status_code == 200

    listed = client.get(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/messages",
        headers=headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["content_markdown"] == "Hello AIRuntime"


def test_chat_file_upload_and_message_with_attachment(client):
    headers = auth_tokens(client, "files@airuntime.dev")

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Files Project", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    upload = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/files",
        headers=headers,
        files={"file": ("notes.txt", b"build a landing page", "text/plain")},
    )
    assert upload.status_code == 201
    file_id = upload.json()["id"]
    assert upload.json()["original_filename"] == "notes.txt"

    message = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/messages",
        headers=headers,
        json={"content": "Use this spec", "attachment_ids": [file_id]},
    )
    assert message.status_code == 200
    body = message.json()
    assert body["attachments"][0]["id"] == file_id

    listed = client.get(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/files", headers=headers
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_secret_key_is_normalized_for_telegram_token(client):
    headers = auth_tokens(client, "secret-normalize@airuntime.dev")

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Secret Bot", "description": ""},
    ).json()

    created = client.post(
        f"/api/v1/projects/{project['id']}/secrets",
        headers=headers,
        json={"key": "telegram bot token", "value": "123:abc"},
    )

    assert created.status_code == 200
    assert created.json()["key"] == "TELEGRAM_BOT_TOKEN"


def test_telegram_token_secret_sets_public_bot_url(client, monkeypatch):
    from src.api.routers import secrets as secrets_router

    headers = auth_tokens(client, "secret-telegram-url@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Secret URL Bot", "description": ""},
    ).json()

    monkeypatch.setattr(
        secrets_router,
        "fetch_bot_profile",
        lambda token: SimpleNamespace(public_url="https://t.me/secret_url_bot"),
    )

    secret = client.post(
        f"/api/v1/projects/{project['id']}/secrets",
        headers=headers,
        json={"key": "telegram bot token"},
    ).json()

    updated = client.patch(
        f"/api/v1/projects/{project['id']}/secrets/{secret['id']}",
        headers=headers,
        json={"value": "12345678901234567890:abcdefghijklmnopqrstuvwxyz"},
    )

    assert updated.status_code == 200
    assert updated.json()["url"] == "https://t.me/secret_url_bot"

    refreshed = client.get(f"/api/v1/projects/{project['id']}", headers=headers)
    assert refreshed.json()["deployment_url"] == "https://t.me/secret_url_bot"


def test_telegram_token_save_sets_public_bot_url(client, monkeypatch):
    from src.api.routers import telegram as telegram_router

    headers = auth_tokens(client, "telegram-url@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "URL Bot", "description": ""},
    ).json()

    monkeypatch.setattr(
        telegram_router,
        "fetch_bot_profile",
        lambda token: SimpleNamespace(public_url="https://t.me/url_bot"),
    )

    saved = client.post(
        f"/api/v1/projects/{project['id']}/telegram/token",
        headers=headers,
        json={"bot_token": "12345678901234567890:abc"},
    )

    assert saved.status_code == 200
    assert saved.json()["url"] == "https://t.me/url_bot"

    refreshed = client.get(f"/api/v1/projects/{project['id']}", headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["deployment_url"] == "https://t.me/url_bot"


def test_telegram_start_refreshes_public_bot_url_from_token(client, monkeypatch):
    from src.api.routers import secrets as secrets_router
    from src.api.routers import telegram as telegram_router

    headers = auth_tokens(client, "telegram-start-url@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Start URL Bot", "description": ""},
    ).json()

    monkeypatch.setattr(
        secrets_router,
        "fetch_bot_profile",
        lambda token: SimpleNamespace(public_url="https://t.me/start_url_bot"),
    )
    secret = client.post(
        f"/api/v1/projects/{project['id']}/secrets",
        headers=headers,
        json={"key": "telegram bot token"},
    ).json()
    client.patch(
        f"/api/v1/projects/{project['id']}/secrets/{secret['id']}",
        headers=headers,
        json={"value": "12345678901234567890:abcdefghijklmnopqrstuvwxyz"},
    )

    monkeypatch.setattr(
        telegram_router,
        "fetch_bot_profile",
        lambda token: SimpleNamespace(public_url="https://t.me/start_url_bot"),
    )

    started = client.post(f"/api/v1/projects/{project['id']}/telegram/start", headers=headers)

    assert started.status_code == 200
    assert started.json()["url"] == "https://t.me/start_url_bot"

    refreshed = client.get(f"/api/v1/projects/{project['id']}", headers=headers)
    assert refreshed.json()["deployment_url"] == "https://t.me/start_url_bot"


def test_telegram_profile_settings_are_applied(client, monkeypatch):
    from src.api.routers import secrets as secrets_router
    from src.api.routers import telegram as telegram_router

    headers = auth_tokens(client, "telegram-profile@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Profile Bot", "description": ""},
    ).json()

    monkeypatch.setattr(
        secrets_router,
        "fetch_bot_profile",
        lambda token: SimpleNamespace(public_url="https://t.me/profile_bot_initial"),
    )
    secret = client.post(
        f"/api/v1/projects/{project['id']}/secrets",
        headers=headers,
        json={"key": "telegram bot token"},
    ).json()
    client.patch(
        f"/api/v1/projects/{project['id']}/secrets/{secret['id']}",
        headers=headers,
        json={"value": "12345678901234567890:abcdefghijklmnopqrstuvwxyz"},
    )

    captured = {}

    def fake_update(token, *, name=None, description=None, short_description=None):
        captured.update(
            {
                "token": token,
                "name": name,
                "description": description,
                "short_description": short_description,
            }
        )
        return SimpleNamespace(
            username="profile_bot",
            public_url="https://t.me/profile_bot",
            name=name,
            description=description,
            short_description=short_description,
        )

    monkeypatch.setattr(telegram_router, "update_bot_settings", fake_update)

    saved = client.post(
        f"/api/v1/projects/{project['id']}/telegram/profile",
        headers=headers,
        json={
            "name": "Support Angel",
            "description": "Answers support questions",
            "short_description": "Support in Telegram",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["url"] == "https://t.me/profile_bot"
    assert saved.json()["name"] == "Support Angel"
    assert captured["token"] == "12345678901234567890:abcdefghijklmnopqrstuvwxyz"
    assert captured["description"] == "Answers support questions"


def test_telegram_profile_photo_is_applied(client, monkeypatch):
    from src.api.routers import secrets as secrets_router
    from src.api.routers import telegram as telegram_router

    headers = auth_tokens(client, "telegram-photo@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "telegram_bot", "name": "Photo Bot", "description": ""},
    ).json()

    monkeypatch.setattr(
        secrets_router,
        "fetch_bot_profile",
        lambda token: SimpleNamespace(public_url="https://t.me/photo_bot_initial"),
    )
    secret = client.post(
        f"/api/v1/projects/{project['id']}/secrets",
        headers=headers,
        json={"key": "telegram bot token"},
    ).json()
    client.patch(
        f"/api/v1/projects/{project['id']}/secrets/{secret['id']}",
        headers=headers,
        json={"value": "12345678901234567890:abcdefghijklmnopqrstuvwxyz"},
    )

    captured = {}

    def fake_photo(token, *, filename, content, content_type):
        captured.update(
            {
                "token": token,
                "filename": filename,
                "content": content,
                "content_type": content_type,
            }
        )
        return SimpleNamespace(
            username="photo_bot",
            public_url="https://t.me/photo_bot",
            name="Photo Bot",
            description="",
            short_description="",
        )

    monkeypatch.setattr(telegram_router, "update_bot_profile_photo", fake_photo)

    saved = client.post(
        f"/api/v1/projects/{project['id']}/telegram/profile/photo",
        headers=headers,
        files={"photo": ("avatar.png", b"fake-image", "image/png")},
    )

    assert saved.status_code == 200
    assert saved.json()["url"] == "https://t.me/photo_bot"
    assert captured["filename"] == "avatar.png"
    assert captured["content"] == b"fake-image"
    assert captured["content_type"] == "image/png"


def test_project_start_is_limited_to_three_running_projects(client, db):
    from src.db.models.project import Project

    headers = auth_tokens(client, "runtime-limit@airuntime.dev")
    projects = []
    for index in range(4):
        project = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"type": "website", "name": f"Runtime {index}", "description": ""},
        ).json()
        projects.append(project)

    for project in projects[:3]:
        row = db.get(Project, project["id"])
        row.status = "live"
        db.add(row)
    fourth = db.get(Project, projects[3]["id"])
    fourth.status = "ready"
    db.add(fourth)
    db.commit()

    limits = client.get("/api/v1/projects/runtime-limits", headers=headers)
    assert limits.status_code == 200
    limits_body = limits.json()
    assert limits_body["running"] == 3
    assert limits_body["max_running"] == 3

    started = client.post(f"/api/v1/projects/{projects[3]['id']}/start", headers=headers)
    assert started.status_code == 409
    assert "3" in started.text


def test_project_stop_cancels_active_deployments(client, db, monkeypatch):
    from src.db.models.deployment import Deployment
    from src.db.models.project import Project
    from src.services import project_runtime

    headers = auth_tokens(client, "runtime-stop@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Stop Me", "description": ""},
    ).json()
    row = db.get(Project, project["id"])
    row.status = "live"
    deployment = Deployment(project_id=row.id, status="running")
    db.add(row)
    db.add(deployment)
    db.commit()

    stopped_ids = []

    def fake_submit_control_job(*, action, project_id, **kwargs):
        stopped_ids.append(project_id)
        return {"ok": True}

    monkeypatch.setattr(
        project_runtime,
        "submit_control_job",
        fake_submit_control_job,
    )

    stopped = client.post(f"/api/v1/projects/{project['id']}/stop", headers=headers)

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped_ids == [project["id"]]
    db.refresh(deployment)
    assert deployment.status == "cancelled"


def test_stream_prompt_generates_artifact_and_queues_deployment(
    client, monkeypatch, orchestration_stub
):
    from src.api.routers import chat as chat_router
    from src.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    stub = orchestration_stub()
    calls = stub.calls
    deployments = []

    def fake_create_deployment(db, project):
        deployments.append(project.id)
        return _fake_live_deployment(db, project)

    monkeypatch.setattr(chat_router, "create_deployment_for_project", fake_create_deployment)

    headers = auth_tokens(client, "stream@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Prompt Site", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Сделай светлый лендинг для студии"},
    )

    assert response.status_code == 200
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    chunks = [payload["chunk"] for payload in payloads if "chunk" in payload]
    statuses = [payload["status"]["phase"] for payload in payloads if "status" in payload]
    assert "verify" in statuses
    assert "deploy" in statuses
    assert any("Сайт запущен" in chunk for chunk in chunks)
    assert not any("поставлен в очередь на запуск" in chunk for chunk in chunks)
    assert "data: [DONE]" in response.text
    assert calls
    assert deployments == [uuid.UUID(project["id"])]

    messages = client.get(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/messages",
        headers=headers,
    ).json()
    assert any("Сайт запущен" in message["content_markdown"] for message in messages)

    updated = client.get(f"/api/v1/projects/{project['id']}", headers=headers).json()
    assert updated["status"] == "live"


def test_request_service_tool_creates_project_service_row(
    client, monkeypatch, db, orchestration_stub
):
    from src.api.routers import chat as chat_router
    from src.core.config import settings
    from src.db.models.project_service import ProjectService

    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    def fake_create_deployment(db, project):
        return _fake_live_deployment(db, project)

    orchestration_stub(requested_service="postgres")
    monkeypatch.setattr(chat_router, "create_deployment_for_project", fake_create_deployment)

    headers = auth_tokens(client, "request-service@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Service Site", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Сделай сайт с базой данных для интернет-магазина"},
    )

    assert response.status_code == 200
    assert "postgres" in response.text

    row = (
        db.query(ProjectService)
        .filter(ProjectService.project_id == uuid.UUID(project["id"]))
        .first()
    )
    assert row is not None
    assert row.kind == "postgres"


def test_requesting_service_credential_as_secret_is_suppressed(
    client, monkeypatch, db, orchestration_stub
):
    """Regression test: the agent sometimes calls request_service for a database AND also
    request_secret for its password (e.g. POSTGRES_PASSWORD) - the platform already generates
    and wires up that credential automatically, so the user must never be asked to fill it in."""
    from src.api.routers import chat as chat_router
    from src.core.config import settings
    from src.db.models.secret import Secret

    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    def fake_create_deployment(db, project):
        return _fake_live_deployment(db, project)

    orchestration_stub(requested_service="postgres", requested_secret="POSTGRES_PASSWORD")
    monkeypatch.setattr(chat_router, "create_deployment_for_project", fake_create_deployment)

    headers = auth_tokens(client, "service-credential@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Service Site", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Сделай сайт с базой данных для интернет-магазина"},
    )

    assert response.status_code == 200
    assert "POSTGRES_PASSWORD" not in response.text

    secret = (
        db.query(Secret)
        .filter(Secret.project_id == uuid.UUID(project["id"]), Secret.key == "POSTGRES_PASSWORD")
        .first()
    )
    assert secret is None


def test_stream_accepts_files_already_linked_to_user_message(
    client, monkeypatch, db, orchestration_stub
):
    from src.api.routers import chat as chat_router
    from src.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    stub = orchestration_stub()
    calls = stub.calls

    def fake_create_deployment(db, project):
        return _fake_live_deployment(db, project)

    monkeypatch.setattr(chat_router, "create_deployment_for_project", fake_create_deployment)

    headers = auth_tokens(client, "stream-file@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "File Site", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    upload = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/files",
        headers=headers,
        files={"file": ("brief.txt", b"hero must say hello from attachment", "text/plain")},
    )
    assert upload.status_code == 201
    file_id = upload.json()["id"]

    created = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/messages",
        headers=headers,
        json={"content": "Use attached brief", "attachment_ids": [file_id]},
    )
    assert created.status_code == 200

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Use attached brief", "attachment_ids": [file_id]},
    )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert calls

    # The attachment context is composed into the turn's request text before the engine is
    # handed it, and the engine persists that verbatim as OrchestrationRun.original_request -
    # so that row is where "the agent actually received the attachment" is now observable.
    from src.db.models.orchestration_run import OrchestrationRun

    db.expire_all()
    run = (
        db.query(OrchestrationRun)
        .filter(OrchestrationRun.project_id == uuid.UUID(project["id"]))
        .one()
    )
    assert "Attachment: brief.txt" in run.original_request
    assert "hero must say hello from attachment" in run.original_request


def test_stream_subdomain_from_prompt_sets_deploy_subdomain(
    client, monkeypatch, orchestration_stub
):
    from src.api.routers import chat as chat_router

    orchestration_stub()
    deployments: list[str | None] = []

    def fake_create_deployment(db, project):
        deployments.append(project.deploy_subdomain)
        if project.deploy_subdomain:
            project.deployment_url = f"https://{project.deploy_subdomain}.airuntime.ru"
        return _fake_live_deployment(db, project)

    monkeypatch.setattr(chat_router, "create_deployment_for_project", fake_create_deployment)

    headers = auth_tokens(client, "subdomain@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Subdomain Site", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Собери лендинг и запусти на https://test.airuntime.ru"},
    )

    assert response.status_code == 200
    assert "https://test.airuntime.ru" in response.text
    # response.text is the raw SSE body - json.dumps(..., ensure_ascii=True) (the default, see
    # chat.py's _sse_chunk/_sse_status) escapes Cyrillic to literal \uXXXX, so a Cyrillic
    # substring check must go through json.loads like the other stream tests in this file rather
    # than matching against response.text directly.
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    chunks = [payload["chunk"] for payload in payloads if "chunk" in payload]
    assert any("Сайт запущен" in chunk for chunk in chunks)
    assert deployments == ["test"]


def test_stream_prompt_reclassifies_project_before_generation(
    client, monkeypatch, orchestration_stub
):
    from src.api.routers import chat as chat_router

    orchestration_stub()
    deployments = []

    monkeypatch.setattr(chat_router, "commit_snapshot", lambda artifact_path, message: "abc123")

    def fake_create_deployment(db, project):
        deployments.append(project.id)
        return _fake_live_deployment(db, project)

    monkeypatch.setattr(chat_router, "create_deployment_for_project", fake_create_deployment)

    headers = auth_tokens(client, "reclassify@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Flexible Project", "description": ""},
    ).json()
    assert project["type"] == "website"
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Make a Telegram bot for support requests"},
    )

    assert response.status_code == 200
    assert deployments == []

    updated = client.get(f"/api/v1/projects/{project['id']}", headers=headers).json()
    assert updated["type"] == "telegram_bot"
    assert "TELEGRAM_BOT_TOKEN" in response.text
    updated = client.get(f"/api/v1/projects/{project['id']}", headers=headers).json()
    assert updated["type"] == "telegram_bot"
    assert updated["status"] == "needs_configuration"


def test_project_combining_site_and_bot_signals_is_classified_mixed(
    client, monkeypatch, orchestration_stub
):
    from src.api.routers import chat as chat_router
    from src.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    orchestration_stub()

    monkeypatch.setattr(chat_router, "commit_snapshot", lambda artifact_path, message: "abc123")

    headers = auth_tokens(client, "mixed-project@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Studio", "description": "Сайт студии и Telegram-бот для заявок"},
    ).json()
    assert project["type"] == "mixed"
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={"content": "Сделай сайт для студии дизайна интерьеров и бота для приёма заявок"},
    )

    assert response.status_code == 200
    assert "TELEGRAM_BOT_TOKEN" in response.text

    updated = client.get(f"/api/v1/projects/{project['id']}", headers=headers).json()
    assert updated["type"] == "mixed"
    assert updated["status"] == "needs_configuration"
