from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.conftest import auth_tokens

from src.db.models.project import Project
from src.db.models.user import User
from src.services.project_runtime import (
    RunningProjectLimitError,
    assert_can_start_project,
    count_running_projects,
    start_project_runtime,
    stop_project_runtime,
)


def _user(db, email: str, client) -> User:
    auth_tokens(client, email)
    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    return user


def _project(db, user: User, *, name: str, status: str) -> Project:
    project = Project(
        id=uuid4(),
        user_id=user.id,
        type="website",
        name=name,
        description="",
        status=status,
    )
    db.add(project)
    db.flush()
    return project


def test_count_running_projects_includes_live_and_deploying(client, db):
    user = _user(db, "count-running@test.com", client)
    _project(db, user, name="live", status="live")
    _project(db, user, name="deploying", status="deploying")
    _project(db, user, name="stopped", status="stopped")
    db.commit()

    assert count_running_projects(db, user.id) == 2


def test_assert_can_start_project_raises_at_limit(client, db):
    user = _user(db, "assert-limit@test.com", client)
    for index in range(3):
        _project(db, user, name=f"live-{index}", status="live")
    db.commit()

    with pytest.raises(RunningProjectLimitError) as exc_info:
        assert_can_start_project(db, user.id)

    assert exc_info.value.limit == 3
    assert exc_info.value.running == 3


def test_stop_project_runtime_sets_stopped(client, db, monkeypatch):
    user = _user(db, "stop-runtime@test.com", client)
    project = _project(db, user, name="site", status="live")
    db.commit()

    calls = []

    def fake_submit_control_job(*, action, project_id, **kwargs):
        calls.append((action, project_id))
        return {"ok": True}

    monkeypatch.setattr(
        "src.services.project_runtime.submit_control_job",
        fake_submit_control_job,
    )

    stopped = stop_project_runtime(db, project)

    assert stopped.status == "stopped"
    assert calls == [("stop", str(project.id))]


def test_start_project_runtime_queues_deploy_when_slot_available(client, db, monkeypatch):
    user = _user(db, "start-runtime@test.com", client)
    project = _project(db, user, name="site", status="stopped")
    db.commit()
    queued: list[str] = []

    def fake_create_deployment(db_session, current_project):
        queued.append(str(current_project.id))
        current_project.status = "deploying"
        db_session.add(current_project)
        db_session.commit()
        return None

    monkeypatch.setattr(
        "src.services.deployments.create_deployment_for_project",
        fake_create_deployment,
    )

    started = start_project_runtime(db, project)

    assert queued == [str(project.id)]
    assert started.status == "deploying"


def test_runtime_reconciler_redeploys_only_missing_live_container(client, db, monkeypatch):
    from src.services import runtime_reconciler

    user = _user(db, "runtime-reconcile@test.com", client)
    healthy = _project(db, user, name="healthy", status="live")
    missing = _project(db, user, name="missing", status="live")
    db.commit()

    class _Adapter:
        def app_container_status(self, project_id: str):
            return "running" if project_id == str(healthy.id) else None

    queued: list[str] = []

    def fake_create(session, project, *, skip_auto_check=False):
        queued.append(str(project.id))
        project.status = "deploying"
        session.add(project)
        session.commit()
        return SimpleNamespace(id=uuid4(), status="queued")

    monkeypatch.setattr(runtime_reconciler, "create_deployment_for_project", fake_create)

    repaired = runtime_reconciler.reconcile_live_projects(db, adapter=_Adapter())

    assert repaired == [str(missing.id)]
    assert queued == [str(missing.id)]
    db.refresh(healthy)
    db.refresh(missing)
    assert healthy.status == "live"
    assert missing.status == "deploying"
    assert "Автовосстановление" in missing.logs


def test_failed_enqueue_returns_project_to_ready(client, db, monkeypatch):
    from src.services import deployments

    user = _user(db, "enqueue-failure@test.com", client)
    project = _project(db, user, name="queue failure", status="live")
    db.commit()
    monkeypatch.setattr(deployments, "enqueue_deployment", lambda **kwargs: False)

    deployment = deployments.create_deployment_for_project(db, project)

    db.refresh(project)
    assert deployment.status == "failed"
    assert project.status == "ready"


def test_runtime_limits_endpoint(client, db):
    headers = auth_tokens(client, "runtime-limits@airuntime.dev")
    user = _user(db, "runtime-limits@airuntime.dev", client)
    _project(db, user, name="one", status="live")
    _project(db, user, name="two", status="deploying")
    db.commit()

    response = client.get("/api/v1/projects/runtime-limits", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["running"] == 2
    assert body["max_running"] == 3
    # Epic A4 added the total-project limit alongside the concurrency one.
    assert body["total"] == 2
    assert "max_total" in body


def test_start_project_endpoint_returns_conflict_when_limit_reached(client, db):
    headers = auth_tokens(client, "runtime-conflict@airuntime.dev")
    user = _user(db, "runtime-conflict@airuntime.dev", client)
    for index in range(3):
        _project(db, user, name=f"live-{index}", status="live")
    stopped = _project(db, user, name="stopped", status="stopped")
    db.commit()

    response = client.post(f"/api/v1/projects/{stopped.id}/start", headers=headers)

    assert response.status_code == 409
    assert "лимит" in response.json()["detail"].lower()


def test_stop_project_endpoint(client, db, monkeypatch):
    headers = auth_tokens(client, "runtime-stop@airuntime.dev")
    user = _user(db, "runtime-stop@airuntime.dev", client)
    project = _project(db, user, name="live", status="live")
    db.commit()

    monkeypatch.setattr(
        "src.services.project_runtime.submit_control_job",
        lambda *, action, project_id, **kwargs: {"ok": True},
    )

    response = client.post(f"/api/v1/projects/{project.id}/stop", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
