"""End-to-end tests for services/orchestration/engine.py against a real Postgres test DB and a
real git-initialized tmp_path workspace. Only two seams are faked:
  - `engine._build_executor` returns a scripted `_FakeExecutor` instead of a real Codex/HTTP
    session - this is the same seam executors.py itself is unit-tested through, so faking it
    here tests the ENGINE's own orchestration logic (routing/contract/evidence/validation/
    failure-policy/budget/events), not a duplicate of executors.py's own test suite.
  - `engine.generate_plan` / `engine.generate_replan` are faked at the planner boundary. Planner
    invariants (including author/reviewer separation for product work) have their own focused
    tests; these tests exercise the engine with a stable graph and override it where a
    multi-task/replan shape is specifically under test.

Everything else - repositories, status transitions, GitTransactionManager, WorkspaceIsolation
Manager, evidence collection, validation, failure_policy, budget, events_bus - runs for real.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session
from tests.conftest import TestingSessionLocal

from src.db.models.chat import Chat
from src.db.models.project import Project
from src.db.models.user import User
from src.services import project_git
from src.services.orchestration import engine
from src.services.orchestration.engine import (
    recover_stranded_runs as _real_recover_stranded_runs,
)
from src.services.orchestration.executors import AgentExecutionResult, TaskContext
from src.services.orchestration.repository import (
    AgentTaskRepository,
    OrchestrationPlanRepository,
    OrchestrationRunRepository,
    RunEventRepository,
)
from src.services.orchestration.schemas import (
    AcceptanceCriterion,
    ExecutionBudget,
    ExecutionPlan,
    PlannedTask,
    SpecialistRole,
    TaskResult,
)


@pytest.fixture()
def project(db: Session) -> Project:
    user = User(email=f"{uuid.uuid4().hex}@example.com", credits_balance=100_000)
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, type="website", name="Engine e2e project")
    db.add(project)
    db.flush()
    return project


def _make_run(db: Session, project: Project, *, original_request: str, **overrides) -> object:
    chat = Chat(project_id=project.id)
    db.add(chat)
    db.flush()
    fields = dict(
        project_id=project.id,
        chat_id=chat.id,
        user_id=project.user_id,
        original_request=original_request,
    )
    fields.update(overrides)
    return OrchestrationRunRepository(db).create(**fields)


@pytest.fixture()
def workspace_root(tmp_path):
    project_git.init_repo_if_needed(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_workspace_dir(monkeypatch: pytest.MonkeyPatch, workspace_root):
    monkeypatch.setattr(engine, "project_workspace_dir", lambda project_id: workspace_root)


@pytest.fixture(autouse=True)
def _stable_default_plan(monkeypatch: pytest.MonkeyPatch):
    """Keep engine tests independent from live planner/model calls and planner policy changes."""
    from src.services.orchestration.planner import PlanGenerationResult

    async def _generate_plan(**kwargs):  # noqa: ANN003
        return PlanGenerationResult(
            plan=ExecutionPlan(
                goal=kwargs["user_message"],
                complexity="simple",
                tasks=[
                    PlannedTask(
                        local_id="main",
                        title="Main task",
                        role=SpecialistRole.IMPLEMENTER,
                        goal=kwargs["user_message"],
                        reason="stable engine e2e plan",
                        write_scope="full_workspace",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="main_build",
                                description="build succeeds",
                                verification_method="build",
                            )
                        ],
                    )
                ],
                estimated_budget=ExecutionBudget(),
            ),
            source="heuristic_simple",
        )

    monkeypatch.setattr(engine, "generate_plan", _generate_plan)


@pytest.fixture()
def db_factory(db: Session):
    def factory() -> Session:
        return TestingSessionLocal(bind=db.get_bind())

    return factory


def _events(db: Session, run_id) -> list[str]:
    return [row.event_type for row in RunEventRepository(db).list_since(run_id, after_seq=0)]


@dataclass
class _FakeExecutor:
    """Each call writes a small, distinct file (so there's a real diff to evidence/commit) and
    returns the next scripted AgentExecutionResult, repeating the last one if the script runs
    out - a test only needs to script as many attempts as it actually cares about."""

    script: list[AgentExecutionResult]
    calls: int = field(default=0)

    async def execute(self, contract, context: TaskContext, cancellation) -> AgentExecutionResult:
        self.calls += 1
        (context.workspace_root / f"output_{self.calls}.txt").write_text(
            f"attempt {self.calls}", encoding="utf-8"
        )
        idx = min(self.calls - 1, len(self.script) - 1)
        return self.script[idx]


def _install_fake_executor(monkeypatch: pytest.MonkeyPatch, fake: _FakeExecutor) -> None:
    monkeypatch.setattr(engine, "_build_executor", lambda kind, *, db, mcp_repo: fake)


def _ok_result(*, summary: str = "done") -> AgentExecutionResult:
    return AgentExecutionResult(
        task_result=TaskResult(
            status="completed", summary=summary, claimed_changed_files=["output.txt"]
        ),
        build_result={"ok": True, "log_tail": "build ok"},
    )


def _build_failed_result(*, summary: str = "attempt failed") -> AgentExecutionResult:
    return AgentExecutionResult(
        task_result=TaskResult(status="completed", summary=summary),
        build_result={"ok": False, "log_tail": "syntax error"},
    )


def _service_request_ok_result(service_kind: str) -> AgentExecutionResult:
    return AgentExecutionResult(
        task_result=TaskResult(
            status="completed",
            summary="done, used a database",
            requested_services=[service_kind],
        ),
        build_result={"ok": True, "log_tail": "ok"},
    )


def _secret_request_result(secret_key: str) -> AgentExecutionResult:
    return AgentExecutionResult(
        task_result=TaskResult(
            status="partial",
            summary="need a credential",
            requested_secrets=[secret_key],
        ),
        build_result=None,
    )


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_task_run_completes_and_commits(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        fake = _FakeExecutor(script=[_ok_result()])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        assert refreshed.final_commit_sha
        assert refreshed.credits_used > 0
        assert fake.calls == 1

        tasks = AgentTaskRepository(db).list_by_run(run.id)
        assert len(tasks) == 1
        assert tasks[0].status == "completed"
        assert tasks[0].accepted_commit_sha == refreshed.final_commit_sha

        # The full durable phase trace for a one-task run - spec section 19 requires each of
        # these to be a real event, not just an internal status change.
        assert _events(db, run.id) == [
            "run_created",
            "planning_started",
            "plan_created",
            "task_ready",
            "task_started",
            # Right after the task's credits are charged, so spend is visible while the run is
            # still going rather than only once it's over.
            "budget_updated",
            "task_validating",
            "task_completed",
            "integration_started",
            "build_started",
            "deploy_started",
            "runtime_verification_started",
            "run_completed",
        ]

    @pytest.mark.asyncio
    async def test_committed_file_is_actually_on_disk_at_head(
        self,
        db: Session,
        db_factory,
        project: Project,
        monkeypatch: pytest.MonkeyPatch,
        workspace_root,
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        _install_fake_executor(monkeypatch, _FakeExecutor(script=[_ok_result()]))

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        assert (workspace_root / "output_1.txt").exists()


class TestServiceAutoProvisioning:
    @pytest.mark.asyncio
    async def test_requested_service_is_provisioned_without_pausing_the_run(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unlike a missing secret, a requested service must never need the user - it's a DB row
        # (ProjectService), not something only a human can supply - so the run should complete
        # normally in one attempt rather than parking at waiting_for_user.
        run = _make_run(db, project, original_request="Сделай сайт с базой данных")
        db.commit()
        fake = _FakeExecutor(script=[_service_request_ok_result("postgres")])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        assert OrchestrationRunRepository(db).get(run.id).status == "completed"
        assert fake.calls == 1

        from src.db.models.project_service import ProjectService

        service = db.query(ProjectService).filter(ProjectService.project_id == project.id).first()
        assert service is not None
        assert service.kind == "postgres"


class TestRetryThenSucceed:
    @pytest.mark.asyncio
    async def test_second_attempt_succeeds_after_first_build_failure(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        fake = _FakeExecutor(script=[_build_failed_result(), _ok_result()])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        assert OrchestrationRunRepository(db).get(run.id).status == "completed"
        assert fake.calls == 2
        task = AgentTaskRepository(db).list_by_run(run.id)[0]
        assert task.status == "completed"
        assert task.attempt == 2
        assert "task_repairing" in _events(db, run.id)


class TestExhaustedAttempts:
    @pytest.mark.asyncio
    async def test_replans_and_completes_under_the_new_plan(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        fake = _FakeExecutor(
            script=[
                _build_failed_result(),
                _build_failed_result(),
                _build_failed_result(),
                _ok_result(),
            ]
        )
        _install_fake_executor(monkeypatch, fake)
        # Loop parking (identical validation fingerprints) is covered elsewhere; this test is
        # specifically the exhausted-attempts -> replan path.
        monkeypatch.setattr(engine.LoopDetector, "any_loop_detected", lambda self: False)

        async def _fake_generate_replan(**kwargs):
            from src.services.orchestration.planner import PlanGenerationResult

            plan = ExecutionPlan(
                goal="revised goal",
                complexity="simple",
                tasks=[
                    PlannedTask(
                        local_id="retry_main",
                        title="Retry",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="revised goal",
                        reason="replanned after repeated build failure",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c1", description="builds", verification_method="build"
                            )
                        ],
                    )
                ],
                estimated_budget=ExecutionBudget(),
            )
            return PlanGenerationResult(plan=plan, source="llm")

        monkeypatch.setattr(engine, "generate_replan", _fake_generate_replan)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        assert refreshed.plan_version == 2

        versions = OrchestrationPlanRepository(db).list_versions(run.id)
        assert [v.status for v in versions] == ["superseded", "active"]

        all_tasks = AgentTaskRepository(db).list_by_run(run.id)
        assert {t.local_id for t in all_tasks} == {"main", "retry_main"}
        original = next(t for t in all_tasks if t.local_id == "main")
        retried = next(t for t in all_tasks if t.local_id == "retry_main")
        assert original.status == "failed"
        assert retried.status == "completed"
        assert "plan_revised" in _events(db, run.id)

    @pytest.mark.asyncio
    async def test_task_crash_replans_instead_of_internal_error(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single executor crash must fail/replan that task, not mark the whole run
        internal_error (the production alert on воздух / пекарня / сиз)."""
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        run_id = run.id
        db.commit()

        class _BoomThenOk:
            calls = 0

            async def execute(self, contract, context: TaskContext, cancellation):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("codex container died")
                (context.workspace_root / f"output_{self.calls}.txt").write_text(
                    f"attempt {self.calls}", encoding="utf-8"
                )
                return _ok_result()

        fake = _BoomThenOk()
        _install_fake_executor(monkeypatch, fake)

        async def _fake_generate_replan(**kwargs):
            from src.services.orchestration.planner import PlanGenerationResult

            plan = ExecutionPlan(
                goal="revised goal",
                complexity="simple",
                tasks=[
                    PlannedTask(
                        local_id="retry_main",
                        title="Retry",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="revised goal",
                        reason="replanned after executor crash",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c1", description="builds", verification_method="build"
                            )
                        ],
                    )
                ],
                estimated_budget=ExecutionBudget(),
            )
            return PlanGenerationResult(plan=plan, source="llm")

        monkeypatch.setattr(engine, "generate_replan", _fake_generate_replan)

        await engine.run_orchestration(
            run_id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run_id)
        assert refreshed.status == "completed"
        assert refreshed.error_code != "internal_error"
        all_tasks = AgentTaskRepository(db).list_by_run(run_id)
        original = next(t for t in all_tasks if t.local_id == "main")
        retried = next(t for t in all_tasks if t.local_id == "retry_main")
        assert original.status == "failed"
        assert original.error_code == "internal_error"
        assert "RuntimeError" in (original.error_message or "")
        assert retried.status == "completed"

    @pytest.mark.asyncio
    async def test_run_level_crash_persists_exception_message(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        run_id = run.id
        db.commit()
        _install_fake_executor(monkeypatch, _FakeExecutor(script=[_ok_result()]))

        def _boom(*_args, **_kwargs):
            raise RuntimeError("finalize exploded")

        monkeypatch.setattr(engine, "_walk_run_to_completed", _boom)

        await engine.run_orchestration(
            run_id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run_id)
        assert refreshed.status == "failed"
        assert refreshed.error_code == "internal_error"
        assert "RuntimeError" in (refreshed.error_message or "")
        assert "finalize exploded" in (refreshed.error_message or "")

    @pytest.mark.asyncio
    async def test_parks_at_waiting_for_user_when_replan_limit_is_reached(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(engine.settings, "orchestration_max_replans", 0)
        monkeypatch.setattr(engine.LoopDetector, "any_loop_detected", lambda self: False)
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        _install_fake_executor(monkeypatch, _FakeExecutor(script=[_build_failed_result()]))

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "waiting_for_user"
        assert refreshed.error_code == "replan_limit_reached"
        assert refreshed.plan_version == 1


class TestWaitingForSecret:
    @pytest.mark.asyncio
    async def test_pauses_then_resumes_and_completes(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай сайт с оплатой")
        db.commit()
        fake = _FakeExecutor(script=[_secret_request_result("STRIPE_API_KEY"), _ok_result()])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        paused = OrchestrationRunRepository(db).get(run.id)
        assert paused.status == "waiting_for_user"
        task = AgentTaskRepository(db).list_by_run(run.id)[0]
        assert task.status == "waiting_for_user"
        assert "waiting_for_secret" in _events(db, run.id)

        engine.resume_task_after_user_input(db, task.id)
        db.commit()

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        resumed = OrchestrationRunRepository(db).get(run.id)
        assert resumed.status == "completed"
        assert fake.calls == 2

    @pytest.mark.asyncio
    async def test_database_url_secret_does_not_pause_the_run(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай CRM с базой")
        db.commit()
        fake = _FakeExecutor(script=[_secret_request_result("DATABASE_URL"), _ok_result()])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        assert "waiting_for_secret" not in _events(db, run.id)
        assert fake.calls == 2


async def _two_task_plan(**kwargs):
    """Two independent Implementer tasks - shared by the budget tests, which need a second task
    still pending when the first one exhausts the run's credits."""
    from src.services.orchestration.planner import PlanGenerationResult

    plan = ExecutionPlan(
        goal="two tasks",
        complexity="compound",
        tasks=[
            PlannedTask(
                local_id="first",
                title="First",
                role=SpecialistRole.IMPLEMENTER,
                goal="first",
                reason="r",
                acceptance_criteria=[
                    AcceptanceCriterion(id="c1", description="d", verification_method="build")
                ],
            ),
            PlannedTask(
                local_id="second",
                title="Second",
                role=SpecialistRole.IMPLEMENTER,
                goal="second",
                reason="r",
                acceptance_criteria=[
                    AcceptanceCriterion(id="c2", description="d", verification_method="build")
                ],
            ),
        ],
        estimated_budget=ExecutionBudget(),
    )
    return PlanGenerationResult(plan=plan, source="llm")


class TestBudget:
    @pytest.mark.asyncio
    async def test_second_task_is_stopped_once_budget_is_exceeded(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(
            db, project, original_request="Сделай лендинг для кофейни", credit_budget=150
        )
        db.commit()

        monkeypatch.setattr(engine, "generate_plan", _two_task_plan)
        # A long summary pushes estimate_task_cost() (char-count fallback, no usage reported)
        # comfortably over the 150-credit run budget on the very first task.
        fake = _FakeExecutor(script=[_ok_result(summary="x" * 500)])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        # waiting_for_user, not failed: running out of budget is recoverable by topping up, and
        # `failed` is terminal with no outgoing transition (spec section 18 requires the run be
        # continuable afterwards).
        assert refreshed.status == "waiting_for_user"
        assert refreshed.error_code == "budget_exceeded"
        assert fake.calls == 1

        tasks = {t.local_id: t for t in AgentTaskRepository(db).list_by_run(run.id)}
        assert tasks["first"].status == "completed"
        # Parked, not skipped/failed - it never ran, and must still run after a top-up.
        assert tasks["second"].status == "waiting_for_user"
        assert tasks["second"].error_code == "budget_exceeded"
        assert tasks["second"].attempt == 0

    @pytest.mark.asyncio
    async def test_topped_up_run_resumes_and_finishes_the_remaining_task(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of parking instead of failing: after extend_budget_after_topup() the
        run must actually make progress, not re-park having done nothing."""
        run = _make_run(
            db, project, original_request="Сделай лендинг для кофейни", credit_budget=150
        )
        db.commit()

        monkeypatch.setattr(engine, "generate_plan", _two_task_plan)
        fake = _FakeExecutor(script=[_ok_result(summary="x" * 500), _ok_result()])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )
        db.expire_all()
        assert OrchestrationRunRepository(db).get(run.id).status == "waiting_for_user"

        # Simulate the user topping up and hitting resume (what the API router does).
        run = OrchestrationRunRepository(db).get(run.id)
        assert engine.extend_budget_after_topup(db, run) is True
        plan = OrchestrationPlanRepository(db).get_active(run.id)
        for task in AgentTaskRepository(db).list_by_plan(plan.id):
            if task.status == "waiting_for_user":
                engine.resume_task_after_user_input(db, task.id)
        db.commit()

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        assert refreshed.error_code is None
        tasks = {t.local_id: t for t in AgentTaskRepository(db).list_by_run(run.id)}
        assert tasks["second"].status == "completed"


class TestCancellation:
    @pytest.mark.asyncio
    async def test_pre_cancelled_token_stops_before_planning(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.orchestration.cancellation import CancellationToken

        async def _boom(**kwargs):
            raise AssertionError("generate_plan must not be called for an already-cancelled run")

        monkeypatch.setattr(engine, "generate_plan", _boom)
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        token = CancellationToken()
        token.cancel("user pressed stop")

        await engine.run_orchestration(
            run.id,
            db_factory=db_factory,
            provider_name="openai",
            model="m",
            api_key="k",
            cancellation=token,
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "cancelled"
        assert _events(db, run.id) == ["run_cancelled"]

    @pytest.mark.asyncio
    async def test_db_cancel_requested_flag_stops_the_run_mid_loop(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()

        class _CancellingExecutor:
            async def execute(self, contract, context, cancellation):
                # Simulate the cancel endpoint flipping the DB flag while this task is running,
                # then let the task finish successfully - cancellation.py's own documented
                # philosophy is that an already-dispatched attempt is allowed to finish rather
                # than be force-killed. This also sidesteps a real trap: _run_one_task's retry
                # loop only checks the in-memory CancellationToken (converted from this DB flag
                # at the TOP of run_orchestration's outer loop, engine.py around line 844) - it
                # is never consulted mid-retry. A FAILING result here would exhaust all 3
                # attempts and (replanning_enabled defaults to True, unmocked in this test) fall
                # through to a real generate_replan()/generate_plan() call with a fake API key,
                # which is what was actually causing this test to hang on a live network call
                # rather than testing cancellation at all. A single successful attempt returns
                # control to the outer loop immediately, where the flag flip is picked up on the
                # very next iteration - before any further task would ever be dispatched.
                #
                # flush(), not commit(): this runs synchronously inside _run_one_task's own
                # active attempt, nested between git_txn.begin() (which can itself open a
                # SAVEPOINT - see WorkspaceLeaseRepository.try_acquire_shared) and
                # git_txn.complete() on the engine's OWN db_factory()-bound session, which
                # shares this test's underlying connection. A real commit() from this separate
                # Session object would end that whole shared transaction out from under the
                # engine's session mid-savepoint, desyncing SQLAlchemy's client-side transaction
                # state from the server's - flush() makes the write visible on the shared
                # connection without touching the transaction boundary.
                run_row = OrchestrationRunRepository(db).get(run.id)
                run_row.cancel_requested = True
                db.add(run_row)
                db.flush()
                return _ok_result()

        monkeypatch.setattr(
            engine, "_build_executor", lambda kind, *, db, mcp_repo: _CancellingExecutor()
        )

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "cancelled"


class TestContractCompleteness:
    @pytest.mark.asyncio
    async def test_task_with_no_acceptance_criteria_triggers_replan_not_a_silent_pass(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_generate_plan(**kwargs):
            from src.services.orchestration.planner import PlanGenerationResult

            plan = ExecutionPlan(
                goal="incomplete",
                complexity="simple",
                tasks=[
                    PlannedTask(
                        local_id="bare",
                        title="Bare",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="goal",
                        reason="r",
                        acceptance_criteria=[],  # deliberately incomplete
                    )
                ],
                estimated_budget=ExecutionBudget(),
            )
            return PlanGenerationResult(plan=plan, source="llm")

        monkeypatch.setattr(engine, "generate_plan", _fake_generate_plan)
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()

        called = {"n": 0}

        async def _fake_generate_replan(**kwargs):
            from src.services.orchestration.planner import PlanGenerationResult

            called["n"] += 1
            plan = ExecutionPlan(
                goal="fixed",
                complexity="simple",
                tasks=[
                    PlannedTask(
                        local_id="fixed",
                        title="Fixed",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="goal",
                        reason="r",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c1", description="d", verification_method="build"
                            )
                        ],
                    )
                ],
                estimated_budget=ExecutionBudget(),
            )
            return PlanGenerationResult(plan=plan, source="llm")

        monkeypatch.setattr(engine, "generate_replan", _fake_generate_replan)
        _install_fake_executor(monkeypatch, _FakeExecutor(script=[_ok_result()]))

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        assert called["n"] == 1
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        tasks = {t.local_id: t for t in AgentTaskRepository(db).list_by_run(run.id)}
        assert tasks["bare"].status == "failed"
        assert tasks["bare"].error_code == "contract_incomplete"
        assert tasks["fixed"].status == "completed"


class TestRestartRecoverySweep:
    """engine.recover_stranded_runs() is what makes the durable rows actually survive a crash -
    without it a run interrupted mid-flight has nothing driving it ever again.

    Calls `_real_recover_stranded_runs` (bound at import time) rather than the module attribute:
    conftest's autouse fixture no-ops the latter so the production startup sweep doesn't fire on
    every TestClient(app) and start background work against other tests' rows."""

    def test_lists_a_stranded_run_but_not_terminal_or_user_parked_ones(
        self, db: Session, project: Project
    ) -> None:
        repo = OrchestrationRunRepository(db)
        stranded = _make_run(db, project, original_request="stranded")
        repo.transition(stranded, "analyzing")
        repo.transition(stranded, "planning")

        done = _make_run(db, project, original_request="done")
        repo.transition(done, "analyzing")
        repo.transition(done, "failed")

        parked = _make_run(db, project, original_request="parked")
        repo.transition(parked, "analyzing")
        repo.transition(parked, "planning")
        repo.transition(parked, "waiting_for_user")

        fresh = _make_run(db, project, original_request="never started")
        db.commit()

        resumable_ids = {r.id for r in repo.list_resumable()}
        assert stranded.id in resumable_ids
        assert done.id not in resumable_ids, "terminal runs must not be resumed"
        assert parked.id not in resumable_ids, "user-parked runs must not be auto-resumed"
        assert fresh.id not in resumable_ids, "never-started runs are the creator's job"

    @pytest.mark.asyncio
    async def test_sweep_relaunches_the_stranded_run(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = OrchestrationRunRepository(db)
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        repo.transition(run, "analyzing")
        repo.transition(run, "planning")
        db.commit()

        launched: list = []
        monkeypatch.setattr(
            engine,
            "launch_run_in_background",
            lambda run_id, **kwargs: launched.append((run_id, kwargs)),
        )
        monkeypatch.setattr(engine.settings, "openai_api_key", "test-key")

        assert _real_recover_stranded_runs(db_factory=db_factory) == 1
        assert [rid for rid, _ in launched] == [run.id]

    @pytest.mark.asyncio
    async def test_sweep_skips_runs_with_no_configured_api_key(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = OrchestrationRunRepository(db)
        run = _make_run(db, project, original_request="Сделай лендинг", provider="anthropic")
        repo.transition(run, "analyzing")
        db.commit()

        launched: list = []
        monkeypatch.setattr(
            engine,
            "launch_run_in_background",
            lambda run_id, **kwargs: launched.append(run_id),
        )
        monkeypatch.setattr(engine.settings, "anthropic_api_key", "")
        monkeypatch.setattr(engine, "resolve_platform_api_key", lambda _p: "")

        assert _real_recover_stranded_runs(db_factory=db_factory) == 0
        assert launched == []


class TestRestartSafety:
    @pytest.mark.asyncio
    async def test_second_call_on_an_already_completed_run_is_a_pure_noop(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        fake = _FakeExecutor(script=[_ok_result()])
        _install_fake_executor(monkeypatch, fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )
        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        assert OrchestrationRunRepository(db).get(run.id).status == "completed"
        assert fake.calls == 1  # the second call must not re-run anything

    @pytest.mark.asyncio
    async def test_resuming_mid_plan_does_not_regenerate_the_plan(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()

        plan_calls = {"n": 0}
        real_generate_plan = engine.generate_plan

        async def _counting_generate_plan(**kwargs):
            plan_calls["n"] += 1
            return await real_generate_plan(**kwargs)

        monkeypatch.setattr(engine, "generate_plan", _counting_generate_plan)
        fake = _FakeExecutor(script=[_build_failed_result(), _ok_result()])
        _install_fake_executor(monkeypatch, fake)

        # First call: task fails once (still "running" the retry loop) - simulate a process
        # restart by simply calling run_orchestration again with a fresh db_factory-backed pass;
        # the active plan from the first call must be reused, not regenerated.
        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        assert plan_calls["n"] == 1
        db.expire_all()
        assert OrchestrationRunRepository(db).get(run.id).status == "completed"


class TestDependencyChaining:
    """A task declaring dependencies=["first"] must not become ready until "first" completes,
    and its TaskContract must carry "first"'s real result through dependency_results -
    build_dependency_results (context_engine.py) has no direct test coverage anywhere, and the
    only existing multi-PlannedTask scenario (TestBudget above) has no dependency edge between
    its two tasks, so this is the one place the planner->readiness->contract_builder dependency
    pass-through is exercised through the real engine end to end."""

    @pytest.mark.asyncio
    async def test_second_task_waits_for_and_receives_first_tasks_result(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай сайт с двумя задачами")
        db.commit()

        async def _fake_generate_plan(**kwargs):
            from src.services.orchestration.planner import PlanGenerationResult

            plan = ExecutionPlan(
                goal="two dependent tasks",
                complexity="compound",
                tasks=[
                    PlannedTask(
                        local_id="first",
                        title="Собрать каркас страницы",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="first",
                        reason="r",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c1", description="d", verification_method="build"
                            )
                        ],
                    ),
                    PlannedTask(
                        local_id="second",
                        title="Добавить стили поверх каркаса",
                        role=SpecialistRole.IMPLEMENTER,
                        goal="second",
                        reason="r",
                        dependencies=["first"],
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c2", description="d", verification_method="build"
                            )
                        ],
                    ),
                ],
                estimated_budget=ExecutionBudget(),
            )
            return PlanGenerationResult(plan=plan, source="llm")

        monkeypatch.setattr(engine, "generate_plan", _fake_generate_plan)

        captured_contracts = []

        class _RecordingExecutor:
            def __init__(self) -> None:
                self.calls = 0

            async def execute(
                self, contract, context: TaskContext, cancellation
            ) -> AgentExecutionResult:
                self.calls += 1
                captured_contracts.append(contract)
                (context.workspace_root / f"output_{self.calls}.txt").write_text(
                    "x", encoding="utf-8"
                )
                summary = "каркас готов: header/main/footer" if self.calls == 1 else "стили готовы"
                return AgentExecutionResult(
                    task_result=TaskResult(
                        status="completed",
                        summary=summary,
                        claimed_changed_files=[f"output_{self.calls}.txt"],
                    ),
                    build_result={"ok": True, "log_tail": "ok"},
                )

        fake = _RecordingExecutor()
        monkeypatch.setattr(engine, "_build_executor", lambda kind, *, db, mcp_repo: fake)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        assert fake.calls == 2

        tasks = {t.local_id: t for t in AgentTaskRepository(db).list_by_run(run.id)}
        assert tasks["first"].status == "completed"
        assert tasks["second"].status == "completed"

        assert len(captured_contracts) == 2
        first_contract, second_contract = captured_contracts
        assert first_contract.dependency_results == []

        assert len(second_contract.dependency_results) == 1
        dep = second_contract.dependency_results[0]
        assert dep.local_id == "first"
        # Proves real sequencing, not just plan order: this is only "completed" with a real
        # summary if "first" had actually finished (and its result was persisted) before
        # "second" was ever handed a contract.
        assert dep.status == "completed"
        assert "каркас готов" in dep.summary
        assert "output_1.txt" in dep.key_outputs


class TestParallelWaveExecution:
    """_select_wave (engine.py) dispatches every ready parallel_read_only/isolated_worktree task
    concurrently via asyncio.gather - one shared_sequential-only plan (every other test in this
    file) can't tell that apart from one-at-a-time dispatch, since there's never more than one
    task ready at once. Two independent read-only-role tasks can be ready simultaneously, which
    is what this proves actually overlap in time."""

    @pytest.mark.asyncio
    async def test_two_independent_read_only_tasks_run_concurrently(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run(db, project, original_request="Сделай сайт и проверь его дважды")
        db.commit()

        async def _fake_generate_plan(**kwargs):
            from src.services.orchestration.planner import PlanGenerationResult

            plan = ExecutionPlan(
                goal="two independent read-only reviews",
                complexity="compound",
                tasks=[
                    PlannedTask(
                        local_id="review_a",
                        title="Review A",
                        role=SpecialistRole.QA_REVIEWER,
                        goal="review a",
                        reason="r",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c1", description="d", verification_method="build"
                            )
                        ],
                    ),
                    PlannedTask(
                        local_id="review_b",
                        title="Review B",
                        role=SpecialistRole.SECURITY_REVIEWER,
                        goal="review b",
                        reason="r",
                        acceptance_criteria=[
                            AcceptanceCriterion(
                                id="c2", description="d", verification_method="build"
                            )
                        ],
                    ),
                ],
                estimated_budget=ExecutionBudget(),
            )
            return PlanGenerationResult(plan=plan, source="llm")

        monkeypatch.setattr(engine, "generate_plan", _fake_generate_plan)

        a_started = asyncio.Event()
        b_started = asyncio.Event()

        class _RendezvousExecutor:
            """Each side signals its own start, then waits for the OTHER side's start signal
            before finishing - only possible if the engine actually dispatched them
            concurrently. A regression to sequential dispatch makes task A wait forever on a
            task B that was never even started yet - asyncio.wait_for's own timeout turns that
            into a fast, clear assertion failure instead of hanging the whole suite."""

            async def execute(self, contract, context, cancellation):
                if contract.task_goal == "review a":
                    a_started.set()
                    await asyncio.wait_for(b_started.wait(), timeout=5)
                else:
                    b_started.set()
                    await asyncio.wait_for(a_started.wait(), timeout=5)
                return AgentExecutionResult(
                    task_result=TaskResult(status="completed", summary="ok"),
                    build_result={"ok": True, "log_tail": "ok"},
                )

        monkeypatch.setattr(
            engine, "_build_executor", lambda kind, *, db, mcp_repo: _RendezvousExecutor()
        )

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        assert a_started.is_set()
        assert b_started.is_set()

        tasks = {t.local_id: t for t in AgentTaskRepository(db).list_by_run(run.id)}
        assert tasks["review_a"].status == "completed"
        assert tasks["review_b"].status == "completed"
        assert tasks["review_a"].workspace_mode == "parallel_read_only"
        assert tasks["review_b"].workspace_mode == "parallel_read_only"


class TestEnsureBuildEvidence:
    @pytest.mark.asyncio
    async def test_returns_existing_without_invoking_build_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.orchestration.schemas import (
            ProjectStateSummary,
            TaskBudget,
            TaskContract,
            ValidationStep,
        )

        calls: list[str] = []

        async def _boom(*_a, **_k):
            calls.append("invoked")
            raise AssertionError("build_check must not run when evidence already exists")

        monkeypatch.setattr(engine.PlatformToolCapabilityProvider, "invoke", _boom)
        contract = TaskContract(
            task_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            role=SpecialistRole.IMPLEMENTER,
            project_goal="g",
            user_value="v",
            task_goal="t",
            reason="r",
            current_state=ProjectStateSummary(project_type="website", project_name="p"),
            budget=TaskBudget(),
            validation_steps=[
                ValidationStep(kind="build", description="must build", required=True)
            ],
        )
        existing = {"ok": True, "log": "already built"}
        result = await engine._ensure_build_evidence(
            contract=contract, project_id=uuid.uuid4(), existing=existing
        )
        assert result is existing
        assert calls == []

    @pytest.mark.asyncio
    async def test_skips_when_build_not_declared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services.orchestration.schemas import (
            ProjectStateSummary,
            TaskBudget,
            TaskContract,
        )

        async def _boom(*_a, **_k):
            raise AssertionError("must not invoke build_check")

        monkeypatch.setattr(engine.PlatformToolCapabilityProvider, "invoke", _boom)
        contract = TaskContract(
            task_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            role=SpecialistRole.QA_REVIEWER,
            project_goal="g",
            user_value="v",
            task_goal="t",
            reason="r",
            current_state=ProjectStateSummary(project_type="website", project_name="p"),
            budget=TaskBudget(),
            validation_steps=[],
        )
        assert (
            await engine._ensure_build_evidence(
                contract=contract, project_id=uuid.uuid4(), existing=None
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_invokes_build_check_when_declared_and_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.orchestration.schemas import (
            CapabilityResult,
            ProjectStateSummary,
            TaskBudget,
            TaskContract,
            ValidationStep,
        )

        async def _fake_invoke(self, capability_id, arguments, context):
            assert capability_id == engine.PLATFORM_CAPABILITY_BUILD_CHECK
            return CapabilityResult(status="completed", output={"ok": True, "log": "built"})

        monkeypatch.setattr(engine.PlatformToolCapabilityProvider, "invoke", _fake_invoke)
        contract = TaskContract(
            task_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            role=SpecialistRole.IMPLEMENTER,
            project_goal="g",
            user_value="v",
            task_goal="t",
            reason="r",
            current_state=ProjectStateSummary(project_type="website", project_name="p"),
            budget=TaskBudget(),
            validation_steps=[
                ValidationStep(kind="build", description="must build", required=True)
            ],
        )
        result = await engine._ensure_build_evidence(
            contract=contract, project_id=uuid.uuid4(), existing=None
        )
        assert result == {"ok": True, "log": "built"}


class TestEnsurePreviewAndRuntimeEvidence:
    @pytest.mark.asyncio
    async def test_collects_preview_and_derives_predeploy_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.orchestration.schemas import (
            CapabilityResult,
            ProjectStateSummary,
            TaskBudget,
            TaskContract,
            ValidationStep,
        )

        async def _fake_invoke(self, capability_id, arguments, context):
            assert capability_id == engine.PLATFORM_CAPABILITY_PREVIEW_CHECK
            assert arguments == {"paths": ["/"]}
            return CapabilityResult(
                status="completed",
                output={
                    "status": "passed",
                    "pages": [{"url": "http://preview/"}],
                    "fatal_errors": [],
                    "warnings": [],
                },
            )

        monkeypatch.setattr(engine.PlatformToolCapabilityProvider, "invoke", _fake_invoke)
        contract = TaskContract(
            task_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            role=SpecialistRole.IMPLEMENTER,
            project_goal="g",
            user_value="v",
            task_goal="t",
            reason="r",
            current_state=ProjectStateSummary(project_type="website", project_name="p"),
            budget=TaskBudget(),
            validation_steps=[
                ValidationStep(kind="preview", description="preview", required=True),
                ValidationStep(kind="runtime", description="runtime", required=True),
            ],
        )

        preview, runtime = await engine._ensure_preview_and_runtime_evidence(
            contract=contract,
            project_id=uuid.uuid4(),
            build_result={"ok": True},
            preview_existing=None,
            runtime_existing=None,
        )

        assert preview is not None and preview["status"] == "passed"
        assert runtime == {
            "ok": True,
            "source": "isolated_preview",
            "preview_status": "passed",
            "pages_checked": 1,
            "fatal_errors": [],
        }

    @pytest.mark.asyncio
    async def test_run_auto_collects_build_when_executor_omits_it(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The bug from production screenshots: Implementer tasks require build validation, but
        # coding executors returned build_result=None -> "no build_result evidence" -> replan
        # loop. Server must collect build evidence itself.
        from src.services.orchestration.schemas import CapabilityResult

        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()
        fake = _FakeExecutor(
            script=[
                AgentExecutionResult(
                    task_result=TaskResult(
                        status="completed",
                        summary="landing ready",
                        claimed_changed_files=["output.txt"],
                    ),
                    build_result=None,
                )
            ]
        )
        _install_fake_executor(monkeypatch, fake)

        async def _fake_invoke(self, capability_id, arguments, context):
            return CapabilityResult(status="completed", output={"ok": True, "log": "auto build"})

        monkeypatch.setattr(engine.PlatformToolCapabilityProvider, "invoke", _fake_invoke)

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        task = AgentTaskRepository(db).list_by_run(run.id)[0]
        assert task.status == "completed"
        assert "build_result" in (task.evidence_json or "")
        assert (
            '"ok": true' in (task.evidence_json or "").lower()
            or '"ok":true' in (task.evidence_json or "").lower()
        )


def _website_with_qa_plan(user_message: str) -> ExecutionPlan:
    return ExecutionPlan(
        goal=user_message,
        complexity="compound",
        tasks=[
            PlannedTask(
                local_id="main",
                title="Сделать сайт",
                role=SpecialistRole.IMPLEMENTER,
                goal=user_message,
                reason="основная реализация",
                write_scope="full_workspace",
                relevant_paths=["public/index.html"],
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="main_build",
                        description="build succeeds",
                        verification_method="build",
                    )
                ],
            ),
            PlannedTask(
                local_id="qa",
                title="Проверить сайт",
                role=SpecialistRole.QA_REVIEWER,
                goal="Независимый visual review",
                reason="судья",
                dependencies=["main"],
                suggested_skills=["visual_preview_review"],
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="qa_visual",
                        description="preview reviewed",
                        verification_method="llm_review",
                    )
                ],
            ),
        ],
        estimated_budget=ExecutionBudget(),
    )


@dataclass
class _JudgeScriptExecutor:
    contracts: list = field(default_factory=list)
    qa_calls: int = 0
    implementer_calls: int = 0

    async def execute(self, contract, context: TaskContext, cancellation) -> AgentExecutionResult:
        self.contracts.append(contract)
        if contract.role == SpecialistRole.QA_REVIEWER:
            self.qa_calls += 1
            todo = (
                "Убери горизонтальный скролл в hero на 390px"
                if self.qa_calls == 1
                else "Всё ещё мало воздуха в секциях"
            )
            return AgentExecutionResult(
                task_result=TaskResult(
                    status="partial",
                    summary=f"skill visual_preview_review: failed; review=revise; {todo}",
                    unresolved=[todo],
                )
            )
        self.implementer_calls += 1
        # Under public/, not the workspace root: the plan scopes the implementer (and the
        # judge fix task that inherits its relevant_paths) to the website tree, so a root-level
        # file is a scope violation that parks the run before the judge handoff is exercised.
        output = context.workspace_root / "public" / f"output_{self.implementer_calls}.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"attempt {self.implementer_calls}", encoding="utf-8")
        return _ok_result()


class TestJudgeTodoHandoff:
    @pytest.mark.asyncio
    async def test_first_revise_injects_implementer_todos_then_ships_after_second_revise(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.orchestration.planner import PlanGenerationResult

        async def _generate_plan(**kwargs):  # noqa: ANN003
            return PlanGenerationResult(
                plan=_website_with_qa_plan(kwargs["user_message"]),
                source="heuristic_simple",
            )

        monkeypatch.setattr(engine, "generate_plan", _generate_plan)
        fake = _JudgeScriptExecutor()
        monkeypatch.setattr(engine, "_build_executor", lambda kind, *, db, mcp_repo: fake)
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        refreshed = OrchestrationRunRepository(db).get(run.id)
        assert refreshed.status == "completed"
        tasks = AgentTaskRepository(db).list_by_run(run.id)
        local_ids = [task.local_id for task in tasks]
        assert local_ids == ["main", "qa", "judge_fix_1", "judge_qa_1"]
        assert all(task.status == "completed" for task in tasks)
        assert fake.qa_calls == 2
        assert fake.implementer_calls == 2
        fix_contract = next(
            contract for contract in fake.contracts if "TODO судьи" in contract.task_goal
        )
        assert "Убери горизонтальный скролл в hero на 390px" in fix_contract.task_goal

    @pytest.mark.asyncio
    async def test_pass_verdict_does_not_enqueue_a_fix_round(
        self, db: Session, db_factory, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.orchestration.planner import PlanGenerationResult

        async def _generate_plan(**kwargs):  # noqa: ANN003
            return PlanGenerationResult(
                plan=_website_with_qa_plan(kwargs["user_message"]),
                source="heuristic_simple",
            )

        class _PassingJudge(_JudgeScriptExecutor):
            async def execute(self, contract, context, cancellation):
                if contract.role == SpecialistRole.QA_REVIEWER:
                    self.qa_calls += 1
                    return AgentExecutionResult(
                        task_result=TaskResult(
                            status="completed",
                            summary="skill visual_preview_review: completed; review=pass",
                        )
                    )
                return await super().execute(contract, context, cancellation)

        monkeypatch.setattr(engine, "generate_plan", _generate_plan)
        fake = _PassingJudge()
        monkeypatch.setattr(engine, "_build_executor", lambda kind, *, db, mcp_repo: fake)
        run = _make_run(db, project, original_request="Сделай лендинг для кофейни")
        db.commit()

        await engine.run_orchestration(
            run.id, db_factory=db_factory, provider_name="openai", model="m", api_key="k"
        )

        db.expire_all()
        tasks = AgentTaskRepository(db).list_by_run(run.id)
        assert [task.local_id for task in tasks] == ["main", "qa"]
        assert OrchestrationRunRepository(db).get(run.id).status == "completed"
        assert fake.qa_calls == 1
        assert fake.implementer_calls == 1
