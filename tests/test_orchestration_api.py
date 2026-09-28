"""Tests for api/routers/orchestration.py - the HTTP/SSE contract only. The engine itself
(does a run actually plan/execute/complete correctly) is already exhaustively covered by
test_orchestration_engine_e2e.py's 13 real-DB/real-git tests; duplicating that here by letting
this file's requests drive a REAL asyncio.create_task background run would mean fighting
TestClient's anyio portal threading model for no additional coverage. Instead:
  - `engine.launch_run_in_background` (the one seam that calls `asyncio.create_task`, shared with
    chat.py's own wiring) is replaced with a synchronous fake that records its arguments - this
    router's OWN job (build the row, return the right response shape, gate on project/run
    ownership, wire cancel/resume's DB effects) is fully exercised without ever depending on a
    background task actually running.
  - `SessionLocal` (the production session factory `stream_run_events` and the real
    `engine.launch_run_in_background` use) is monkeypatched to share this test's own connection/
    transaction - otherwise anything written via the `db` fixture (uncommitted until teardown,
    see conftest.py) would be invisible to a session opened against a separate connection, which
    is exactly the trap the real background path would fall into unpatched.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session
from tests.conftest import TestingSessionLocal, auth_tokens

from src.api.routers import orchestration as orchestration_router
from src.core.config import settings
from src.services.orchestration import engine, events_bus
from src.services.orchestration.repository import OrchestrationRunRepository


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")


@pytest.fixture(autouse=True)
def _share_test_connection(monkeypatch: pytest.MonkeyPatch, db: Session):
    """See module docstring - makes SessionLocal() calls inside orchestration.py resolve to a
    session bound to the SAME connection/transaction as the `db` fixture."""
    monkeypatch.setattr(
        orchestration_router, "SessionLocal", lambda: TestingSessionLocal(bind=db.get_bind())
    )


@pytest.fixture()
def launch_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def _fake_launch(run_id, *, provider_name, model, api_key):
        calls.append(
            {"run_id": run_id, "provider_name": provider_name, "model": model, "api_key": api_key}
        )

    monkeypatch.setattr(engine, "launch_run_in_background", _fake_launch)
    return calls


def _create_project_and_chat(client, headers) -> tuple[str, str]:
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "API Test", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()
    return project["id"], chat["id"]


class TestCreateRun:
    def test_creates_row_and_launches_background_run(self, client, launch_calls):
        headers = auth_tokens(client, "orch-create@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)

        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "created"
        assert body["project_id"] == project_id
        assert body["chat_id"] == chat_id
        assert body["original_request"] == "Сделай лендинг для кофейни"

        assert len(launch_calls) == 1
        assert str(launch_calls[0]["run_id"]) == body["id"]
        assert launch_calls[0]["provider_name"] == "openai"

    def test_unknown_chat_id_is_404(self, client, launch_calls):
        headers = auth_tokens(client, "orch-badchat@airuntime.dev")
        project_id, _chat_id = _create_project_and_chat(client, headers)
        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": "00000000-0000-0000-0000-000000000000", "content": "hi"},
        )
        assert response.status_code == 404
        assert launch_calls == []

    def test_chat_belonging_to_another_project_is_404(self, client, launch_calls):
        headers = auth_tokens(client, "orch-crosschat@airuntime.dev")
        project_a, chat_a = _create_project_and_chat(client, headers)
        project_b, _chat_b = _create_project_and_chat(client, headers)
        response = client.post(
            f"/api/v1/projects/{project_b}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_a, "content": "hi"},
        )
        assert response.status_code == 404

    def test_no_api_key_configured_is_400(self, client, monkeypatch, launch_calls):
        monkeypatch.setattr(settings, "openai_api_key", None)
        headers = auth_tokens(client, "orch-nokey@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг"},
        )
        assert response.status_code == 400
        assert launch_calls == []


class TestReadRuns:
    def test_get_run_returns_tasks_from_the_active_plan(self, client, db: Session, launch_calls):
        headers = auth_tokens(client, "orch-get@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        created = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()

        response = client.get(
            f"/api/v1/projects/{project_id}/orchestration/runs/{created['id']}", headers=headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == created["id"]
        assert body["tasks"] == []  # no plan yet - the (faked) background run never ran

    def test_get_run_404s_for_a_run_owned_by_another_user(self, client, launch_calls):
        headers_a = auth_tokens(client, "orch-owner-a@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers_a)
        run = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers_a,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()

        headers_b = auth_tokens(client, "orch-owner-b@airuntime.dev")
        response = client.get(
            f"/api/v1/projects/{project_id}/orchestration/runs/{run['id']}", headers=headers_b
        )
        assert response.status_code == 404

    def test_get_run_404s_for_unknown_run_id(self, client, launch_calls):
        headers = auth_tokens(client, "orch-unknown@airuntime.dev")
        project_id, _chat_id = _create_project_and_chat(client, headers)
        response = client.get(
            f"/api/v1/projects/{project_id}/orchestration/runs/{uuid.uuid4()}", headers=headers
        )
        assert response.status_code == 404

    def test_list_runs_returns_both_created_runs(self, client, launch_calls):
        # Not asserting exact newest-first order here: this test's `db` fixture wraps both
        # creates in ONE transaction, and Postgres's now() (server_default on created_at) is
        # transaction-scoped - both rows can get an IDENTICAL timestamp, making order genuinely
        # ambiguous under this test setup even though real, separately-transacted HTTP requests
        # in production always get distinct timestamps. The ORDER BY itself is exercised as
        # ordinary, non-error-prone SQL either way.
        headers = auth_tokens(client, "orch-list@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        first = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Первый запрос"},
        ).json()
        second = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Второй запрос"},
        ).json()

        listed = client.get(
            f"/api/v1/projects/{project_id}/orchestration/runs", headers=headers
        ).json()
        assert listed["total"] == 2
        assert {item["id"] for item in listed["items"]} == {first["id"], second["id"]}


class TestEventsStream:
    def test_stream_replays_durable_events_then_sends_done(self, client, db: Session, launch_calls):
        headers = auth_tokens(client, "orch-events@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        created = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()
        run_id = created["id"]

        events_bus.emit(db, run_id=uuid.UUID(run_id), event_type="run_created", payload={})
        events_bus.emit(db, run_id=uuid.UUID(run_id), event_type="run_completed", payload={})

        response = client.get(
            f"/api/v1/projects/{project_id}/orchestration/runs/{run_id}/events", headers=headers
        )
        assert response.status_code == 200
        assert '"event_type": "run_created"' in response.text
        assert '"event_type": "run_completed"' in response.text
        assert "data: [DONE]" in response.text

    def test_stream_404s_for_a_run_owned_by_another_user(self, client, launch_calls):
        headers_a = auth_tokens(client, "orch-events-a@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers_a)
        run = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers_a,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()

        headers_b = auth_tokens(client, "orch-events-b@airuntime.dev")
        response = client.get(
            f"/api/v1/projects/{project_id}/orchestration/runs/{run['id']}/events",
            headers=headers_b,
        )
        assert response.status_code == 404


class TestCancelRun:
    def test_cancel_sets_the_persisted_flag(self, client, db: Session, launch_calls):
        headers = auth_tokens(client, "orch-cancel@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        run = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()

        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs/{run['id']}/cancel",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["cancel_requested"] is True

        db.expire_all()
        row = OrchestrationRunRepository(db).get(uuid.UUID(run["id"]))
        assert row.cancel_requested is True

    def test_cancelling_a_terminal_run_is_a_noop(self, client, db: Session, launch_calls):
        headers = auth_tokens(client, "orch-cancel-term@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        run = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()
        row = OrchestrationRunRepository(db).get(uuid.UUID(run["id"]))
        OrchestrationRunRepository(db).transition(row, "analyzing")
        OrchestrationRunRepository(db).transition(row, "planning")
        OrchestrationRunRepository(db).transition(row, "failed", error_message="test")
        db.commit()

        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs/{run['id']}/cancel",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        db.expire_all()
        assert OrchestrationRunRepository(db).get(uuid.UUID(run["id"])).cancel_requested is False


class TestResumeRun:
    def test_resume_on_a_non_waiting_run_is_a_noop_that_does_not_relaunch(
        self, client, db: Session, launch_calls
    ):
        headers = auth_tokens(client, "orch-resume-noop@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        run = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()
        launch_calls.clear()  # drop the create_run launch call, only care about resume below

        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs/{run['id']}/resume",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "created"
        assert launch_calls == []

    def test_resume_on_a_waiting_run_unblocks_tasks_and_relaunches(
        self, client, db: Session, launch_calls
    ):
        headers = auth_tokens(client, "orch-resume@airuntime.dev")
        project_id, chat_id = _create_project_and_chat(client, headers)
        created = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs",
            headers=headers,
            json={"chat_id": chat_id, "content": "Сделай лендинг для кофейни"},
        ).json()
        launch_calls.clear()

        from src.services.orchestration.repository import (
            AgentTaskRepository,
            OrchestrationPlanRepository,
        )

        run_row = OrchestrationRunRepository(db).get(uuid.UUID(created["id"]))
        OrchestrationRunRepository(db).transition(run_row, "analyzing")
        OrchestrationRunRepository(db).transition(run_row, "planning")
        plan = OrchestrationPlanRepository(db).create_version(
            run_id=run_row.id, version=1, graph_json="{}"
        )
        OrchestrationPlanRepository(db).activate(plan)
        task_repo = AgentTaskRepository(db)
        task = task_repo.create(
            run_id=run_row.id,
            plan_id=plan.id,
            local_id="main",
            title="Main",
            role="implementer",
            execution_kind="codex_task",
            status="ready",
            max_attempts=3,
        )
        task_repo.transition(task, "running")
        task_repo.transition(task, "collecting_evidence")
        task_repo.transition(task, "validating")
        task_repo.transition(task, "waiting_for_user")
        OrchestrationRunRepository(db).transition(run_row, "waiting_for_user")
        db.commit()

        response = client.post(
            f"/api/v1/projects/{project_id}/orchestration/runs/{created['id']}/resume",
            headers=headers,
        )
        assert response.status_code == 200
        assert len(launch_calls) == 1
        assert str(launch_calls[0]["run_id"]) == created["id"]

        db.expire_all()
        assert task_repo.get(task.id).status == "ready"
