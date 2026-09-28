"""Tests for services/orchestration/metrics.py + the admin surface over it (spec section 20).

The raw per-event log already existed; what these cover is the AGGREGATION layer on top - the
"is the engine healthy" questions that no single run's events can answer - plus the fact that the
answers are admin-only and contain nothing secret.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session
from tests.conftest import auth_tokens

from src.db.models.chat import Chat
from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration.metrics import collect_metrics
from src.services.orchestration.repository import (
    AgentTaskRepository,
    OrchestrationPlanRepository,
    OrchestrationRunRepository,
)


def _project(db: Session) -> Project:
    user = User(email=f"{uuid.uuid4().hex}@example.com", credits_balance=1000)
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, type="website", name="Metrics project")
    db.add(project)
    db.flush()
    return project


def _run(db: Session, project: Project, *, status_chain: list[str], **fields):
    chat = Chat(project_id=project.id)
    db.add(chat)
    db.flush()
    repo = OrchestrationRunRepository(db)
    run = repo.create(
        project_id=project.id,
        chat_id=chat.id,
        user_id=project.user_id,
        original_request="x",
        **fields,
    )
    for status in status_chain:
        repo.transition(run, status)
    return run


class TestCollectMetrics:
    def test_empty_window_reports_none_rates_not_zero(self, db: Session) -> None:
        """ "no runs yet" and "0% success" must not render identically on a dashboard."""
        metrics = collect_metrics(db, window_hours=1)
        assert metrics.runs_total == 0
        assert metrics.run_success_rate is None
        assert metrics.average_credits_per_successful_run is None

    def test_counts_runs_by_outcome_and_computes_success_rate(self, db: Session) -> None:
        project = _project(db)
        _run(db, project, status_chain=["analyzing", "planning", "running", "failed"])
        for _ in range(3):
            _run(
                db,
                project,
                status_chain=[
                    "analyzing",
                    "planning",
                    "running",
                    "validating",
                    "integrating",
                    "building",
                    "deploying",
                    "verifying_runtime",
                    "completed",
                ],
            )
        db.commit()

        metrics = collect_metrics(db, window_hours=24)
        assert metrics.runs_completed == 3
        assert metrics.runs_failed == 1
        assert metrics.run_success_rate == 0.75

    def test_cancelled_and_waiting_runs_are_excluded_from_the_success_denominator(
        self, db: Session
    ) -> None:
        # A user pressing Stop, or a run parked on a secret they haven't supplied, says nothing
        # about whether the engine works - counting them as failures would understate health.
        project = _project(db)
        _run(
            db,
            project,
            status_chain=[
                "analyzing",
                "planning",
                "running",
                "validating",
                "integrating",
                "building",
                "deploying",
                "verifying_runtime",
                "completed",
            ],
        )
        _run(db, project, status_chain=["analyzing", "cancelled"])
        _run(db, project, status_chain=["analyzing", "planning", "waiting_for_user"])
        db.commit()

        metrics = collect_metrics(db, window_hours=24)
        assert metrics.runs_cancelled == 1
        assert metrics.runs_waiting_for_user == 1
        assert metrics.run_success_rate == 1.0

    def test_replan_rate_counts_runs_that_needed_more_than_one_plan_version(
        self, db: Session
    ) -> None:
        project = _project(db)
        _run(db, project, status_chain=["analyzing"], plan_version=1)
        _run(db, project, status_chain=["analyzing"], plan_version=3)
        db.commit()

        metrics = collect_metrics(db, window_hours=24)
        assert metrics.replan_rate == 0.5

    def test_first_attempt_and_task_breakdowns(self, db: Session) -> None:
        project = _project(db)
        run = _run(db, project, status_chain=["analyzing", "planning"])
        plan = OrchestrationPlanRepository(db).create_version(
            run_id=run.id, version=1, graph_json="{}", goal="g"
        )
        task_repo = AgentTaskRepository(db)
        for i, (attempt, kind) in enumerate([(1, "skill"), (3, "specialist_agent")]):
            task = task_repo.create(
                run_id=run.id,
                plan_id=plan.id,
                local_id=f"t{i}",
                sequence=i,
                title=f"T{i}",
                role="implementer",
                execution_kind=kind,
                status="pending",
                max_attempts=3,
            )
            task.status = "completed"
            task.attempt = attempt
            db.add(task)
        db.commit()

        metrics = collect_metrics(db, window_hours=24)
        assert metrics.tasks_completed == 2
        assert metrics.first_attempt_success_rate == 0.5
        assert metrics.tasks_by_execution_kind == {"skill": 1, "specialist_agent": 1}
        assert metrics.skill_match_rate == 0.5

    def test_project_id_filter_scopes_run_counts(self, db: Session) -> None:
        project_a = _project(db)
        project_b = _project(db)
        _run(db, project_a, status_chain=["analyzing", "planning", "running", "failed"])
        for _ in range(2):
            _run(
                db,
                project_a,
                status_chain=[
                    "analyzing",
                    "planning",
                    "running",
                    "validating",
                    "integrating",
                    "building",
                    "deploying",
                    "verifying_runtime",
                    "completed",
                ],
            )
        _run(
            db,
            project_b,
            status_chain=[
                "analyzing",
                "planning",
                "running",
                "validating",
                "integrating",
                "building",
                "deploying",
                "verifying_runtime",
                "completed",
            ],
        )
        db.commit()

        scoped = collect_metrics(db, window_hours=24, project_id=project_a.id)
        global_metrics = collect_metrics(db, window_hours=24)

        assert scoped.runs_total == 3
        assert scoped.runs_completed == 2
        assert scoped.runs_failed == 1
        assert global_metrics.runs_total == 4
        assert global_metrics.runs_completed == 3

    def test_payload_is_json_safe_and_carries_no_free_text(self, db: Session) -> None:
        """The admin view must be safe to expose: counts/rates only, never model output or
        secrets."""
        import json

        project = _project(db)
        _run(db, project, status_chain=["analyzing"])
        db.commit()
        payload = collect_metrics(db, window_hours=24).to_dict()
        json.dumps(payload)  # must not raise
        assert "original_request" not in payload
        assert "goal" not in payload


class TestMetricsEndpointIsAdminOnly:
    def test_ordinary_user_gets_404_not_403(self, client) -> None:
        # 404 so an ordinary user cannot even discover the surface exists.
        headers = auth_tokens(client, "not-admin@airuntime.dev")
        response = client.get("/api/v1/admin/orchestration/metrics", headers=headers)
        assert response.status_code == 404

    def test_admin_gets_the_aggregates(self, client, db: Session) -> None:
        headers = auth_tokens(client, "an-admin@airuntime.dev")
        user = db.query(User).filter(User.email == "an-admin@airuntime.dev").one()
        user.role = "admin"
        db.add(user)
        db.commit()

        response = client.get("/api/v1/admin/orchestration/metrics", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["window_hours"] == 24
        assert "run_success_rate" in body
        assert "workspace_lease_conflicts" in body

    def test_unauthenticated_is_rejected(self, client) -> None:
        assert client.get("/api/v1/admin/orchestration/metrics").status_code == 401
