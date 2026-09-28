"""Tests for services/orchestration/repository.py and status.py against a real Postgres test
DB (conftest.py's `db` fixture) - no fakes needed here, this layer has no LLM/Docker/Redis
dependency, just SQLAlchemy."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from src.db.models.chat import Chat
from src.db.models.mcp_server import McpServer
from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration.repository import (
    AgentTaskRepository,
    McpServerRepository,
    OrchestrationPlanRepository,
    OrchestrationRunRepository,
    RunEventRepository,
    WorkspaceLeaseRepository,
)
from src.services.orchestration.status import IllegalStatusTransition


def _make_user(db: Session) -> User:
    user = User(email=f"{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    return user


def _make_project(db: Session, user: User, *, type_: str = "website") -> Project:
    project = Project(user_id=user.id, type=type_, name="Test project")
    db.add(project)
    db.flush()
    return project


def _make_chat(db: Session, project: Project) -> Chat:
    chat = Chat(project_id=project.id)
    db.add(chat)
    db.flush()
    return chat


@pytest.fixture()
def project(db: Session) -> Project:
    user = _make_user(db)
    return _make_project(db, user)


@pytest.fixture()
def chat(db: Session, project: Project) -> Chat:
    return _make_chat(db, project)


class TestOrchestrationRunRepository:
    def test_create_and_transition_happy_path(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        repo = OrchestrationRunRepository(db)
        run = repo.create(
            project_id=project.id,
            chat_id=chat.id,
            user_id=project.user_id,
            status="created",
            original_request="build me a landing page",
        )
        assert run.status == "created"
        assert run.started_at is None

        repo.transition(run, "analyzing")
        assert run.status == "analyzing"
        assert run.started_at is not None

        repo.transition(run, "planning")
        repo.transition(run, "running")
        repo.transition(run, "validating")
        repo.transition(run, "integrating")
        repo.transition(run, "building")
        repo.transition(run, "deploying")
        repo.transition(run, "verifying_runtime")
        repo.transition(run, "completed")
        assert run.status == "completed"
        assert run.finished_at is not None

    def test_illegal_transition_raises(self, db: Session, project: Project, chat: Chat) -> None:
        repo = OrchestrationRunRepository(db)
        run = repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        with pytest.raises(IllegalStatusTransition):
            repo.transition(run, "completed")

    def test_terminal_status_has_no_further_transitions(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        repo = OrchestrationRunRepository(db)
        run = repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        repo.transition(run, "cancelled")
        with pytest.raises(IllegalStatusTransition):
            repo.transition(run, "running")

    def test_terminal_run_fails_orphan_tasks(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")
        orphan = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="orphan",
            title="Orphan QA",
            role="qa_reviewer",
            execution_kind="skill",
            status="blocked",
            depends_on_json=json.dumps([]),
        )
        finished = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="done",
            title="Done",
            role="implementer",
            execution_kind="specialist_agent",
            status="completed",
            depends_on_json=json.dumps([]),
        )

        run_repo.transition(run, "failed", error_message="deadlock")

        assert task_repo.get(orphan.id).status == "failed"
        assert task_repo.get(orphan.id).error_code == "run_terminal"
        assert task_repo.get(finished.id).status == "completed"

    def test_same_status_transition_is_idempotent_noop(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        repo = OrchestrationRunRepository(db)
        run = repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        repo.transition(run, "created")
        assert run.status == "created"

    def test_request_cancel_sets_flag(self, db: Session, project: Project, chat: Chat) -> None:
        repo = OrchestrationRunRepository(db)
        run = repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        assert run.cancel_requested is False
        repo.request_cancel(run)
        assert run.cancel_requested is True

    def test_list_active_for_project_excludes_terminal(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        repo = OrchestrationRunRepository(db)
        active = repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        done = repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        repo.transition(done, "cancelled")

        results = repo.list_active_for_project(project.id)
        ids = {r.id for r in results}
        assert active.id in ids
        assert done.id not in ids


class TestOrchestrationPlanRepository:
    def test_versioning_and_supersede_on_activate(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)

        v1 = plan_repo.create_version(
            run_id=run.id, version=plan_repo.next_version(run.id), graph_json="{}"
        )
        plan_repo.activate(v1)
        assert v1.status == "active"

        v2 = plan_repo.create_version(
            run_id=run.id,
            version=plan_repo.next_version(run.id),
            graph_json="{}",
            replan_reason="build failed twice",
        )
        assert v2.version == 2
        plan_repo.activate(v2)

        assert v2.status == "active"
        assert v1.status == "superseded"
        assert v1.superseded_at is not None
        assert plan_repo.get_active(run.id).id == v2.id
        assert [p.version for p in plan_repo.list_versions(run.id)] == [1, 2]


class TestAgentTaskRepositoryReadiness:
    def _plan(
        self, db: Session, project: Project, chat: Chat
    ) -> tuple[OrchestrationRunRepository, str]:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")
        return run, plan

    def test_dependent_task_blocked_until_dependency_completes(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")

        task_a = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="a",
            title="A",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json=json.dumps([]),
        )
        task_b = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="b",
            title="B",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json=json.dumps(["a"]),
        )

        ready = task_repo.refresh_readiness(plan.id)
        ready_ids = {t.local_id for t in ready}
        assert ready_ids == {"a"}
        assert task_repo.get(task_b.id).status == "blocked"

        task_repo.transition(task_a, "running")
        task_repo.transition(task_a, "collecting_evidence")
        task_repo.transition(task_a, "validating")
        task_repo.transition(task_a, "completed")

        ready = task_repo.refresh_readiness(plan.id)
        assert {t.local_id for t in ready} == {"b"}
        assert task_repo.get(task_b.id).status == "ready"

    def test_repairing_can_park_on_budget_exceeded(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")
        task = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="main",
            title="Main",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json=json.dumps([]),
        )
        task_repo.refresh_readiness(plan.id)
        task_repo.transition(task, "running")
        task_repo.transition(task, "collecting_evidence")
        task_repo.transition(task, "validating")
        task_repo.transition(task, "repairing")
        task_repo.transition(task, "waiting_for_user", error_code="budget_exceeded")
        assert task_repo.get(task.id).status == "waiting_for_user"

    def test_running_can_park_on_budget_exceeded(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")
        task = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="main",
            title="Main",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json=json.dumps([]),
        )
        task_repo.refresh_readiness(plan.id)
        task_repo.transition(task, "running")
        task_repo.transition(task, "waiting_for_user", error_code="budget_exceeded")
        assert task_repo.get(task.id).status == "waiting_for_user"

    def test_failed_dependency_skips_dependent_forever(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")

        task_a = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="a",
            title="A",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json=json.dumps([]),
        )
        task_b = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="b",
            title="B",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json=json.dumps(["a"]),
        )
        task_repo.refresh_readiness(plan.id)  # a: pending -> ready (no deps)
        task_repo.transition(task_a, "running")
        task_repo.transition(task_a, "collecting_evidence")
        task_repo.transition(task_a, "validating")
        task_repo.transition(task_a, "failed")

        task_repo.refresh_readiness(plan.id)
        assert task_repo.get(task_b.id).status == "skipped"


class TestWorkspaceLeaseRepository:
    def test_shared_lease_is_mutually_exclusive(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        lease_repo = WorkspaceLeaseRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)

        first = lease_repo.try_acquire_shared(
            project_id=project.id, run_id=run.id, holder="worker-1"
        )
        assert first is not None

        second = lease_repo.try_acquire_shared(
            project_id=project.id, run_id=run.id, holder="worker-2"
        )
        assert second is None, "a second concurrent shared lease must be rejected"

        lease_repo.release(first)
        third = lease_repo.try_acquire_shared(
            project_id=project.id, run_id=run.id, holder="worker-3"
        )
        assert third is not None, "releasing the first lease must free the project up"

    def test_expired_lease_is_reaped_automatically(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        lease_repo = WorkspaceLeaseRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)

        stale = lease_repo.try_acquire_shared(
            project_id=project.id, run_id=run.id, holder="crashed-worker", ttl_seconds=-1
        )
        assert stale is not None

        fresh = lease_repo.try_acquire_shared(
            project_id=project.id, run_id=run.id, holder="worker-2"
        )
        assert fresh is not None, "a lease past its expires_at must not block new acquires"

    def test_isolated_leases_can_coexist(self, db: Session, project: Project, chat: Chat) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        lease_repo = WorkspaceLeaseRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")

        leases = [
            lease_repo.acquire_isolated(
                project_id=project.id,
                run_id=run.id,
                task_id=task_repo.create(
                    run_id=run.id,
                    plan_id=plan.id,
                    local_id=f"t{i}",
                    title=f"T{i}",
                    role="implementer",
                    execution_kind="codex_task",
                    status="pending",
                ).id,
                holder=f"worker-{i}",
                worktree_path=f"/tmp/wt-{i}",
                branch_name=f"task-{i}",
            )
            for i in range(3)
        ]
        active = lease_repo.list_active_isolated(project.id)
        assert len(active) == 3
        assert {lease.id for lease in leases} == {lease.id for lease in active}


class TestRunEventRepository:
    def test_events_get_monotonic_per_run_sequence(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        event_repo = RunEventRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)

        e1 = event_repo.append(run_id=run.id, event_type="run_created", payload_json="{}")
        e2 = event_repo.append(run_id=run.id, event_type="planning_started", payload_json="{}")
        e3 = event_repo.append(run_id=run.id, event_type="plan_created", payload_json="{}")

        assert [e.seq for e in (e1, e2, e3)] == [1, 2, 3]
        assert event_repo.latest_seq(run.id) == 3

        replay = event_repo.list_since(run.id, after_seq=1)
        assert [e.id for e in replay] == [e2.id, e3.id]

    def test_events_scoped_per_run(self, db: Session, project: Project, chat: Chat) -> None:
        run_repo = OrchestrationRunRepository(db)
        event_repo = RunEventRepository(db)
        run_a = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        run_b = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)

        event_repo.append(run_id=run_a.id, event_type="run_created", payload_json="{}")
        event_repo.append(run_id=run_b.id, event_type="run_created", payload_json="{}")
        event_repo.append(run_id=run_a.id, event_type="run_completed", payload_json="{}")

        assert event_repo.latest_seq(run_a.id) == 2
        assert event_repo.latest_seq(run_b.id) == 1


class TestStaleRunningRecovery:
    def test_reset_stale_running_moves_task_back_to_ready(
        self, db: Session, project: Project, chat: Chat
    ) -> None:
        run_repo = OrchestrationRunRepository(db)
        plan_repo = OrchestrationPlanRepository(db)
        task_repo = AgentTaskRepository(db)
        run = run_repo.create(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
        plan = plan_repo.create_version(run_id=run.id, version=1, graph_json="{}")
        task = task_repo.create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="a",
            title="A",
            role="implementer",
            execution_kind="specialist_agent",
            status="running",
            depends_on_json="[]",
        )
        task.started_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()

        reset = task_repo.reset_stale_running(plan.id, stale_seconds=300)
        assert [t.local_id for t in reset] == ["a"]
        assert task_repo.get(task.id).status == "ready"


class TestMcpServerRepository:
    def test_list_enabled_filters_disabled_servers(self, db: Session) -> None:
        db.add(McpServer(name="enabled-one", transport="stdio", enabled=True))
        db.add(McpServer(name="disabled-one", transport="http", enabled=False))
        db.flush()

        repo = McpServerRepository(db)
        names = {s.name for s in repo.list_enabled()}
        assert names == {"enabled-one"}
        assert repo.get_by_name("disabled-one") is not None
