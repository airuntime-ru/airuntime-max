"""Tests for services/orchestration/contract_builder.py - especially that the literal bad
example from the spec ({"title": "...", "instructions": "..."}) fails completeness scoring,
and that a contract built through build_task_contract() always honors role_policy's ceilings."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.db.models.chat import Chat
from src.db.models.orchestration_plan import OrchestrationPlan
from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration.context_engine import ContextEngine
from src.services.orchestration.contract_builder import (
    COMPLETENESS_PASS_THRESHOLD,
    build_task_contract,
    derive_validation_steps,
    score_task_contract,
)
from src.services.orchestration.schemas import (
    AcceptanceCriterion,
    PlannedTask,
    ProjectStateSummary,
    SpecialistRole,
    TaskBudget,
    TaskContract,
    TaskOutputContract,
)


def _minimal_bad_contract() -> TaskContract:
    """The exact shape the spec calls out as forbidden, expressed as a TaskContract with
    everything beyond the two bad-example fields left empty/default."""
    return TaskContract(
        task_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        role=SpecialistRole.IMPLEMENTER,
        project_goal="",
        user_value="",
        task_goal="Сделай хороший backend",
        reason="",
        current_state=ProjectStateSummary(project_type="website", project_name=""),
        budget=TaskBudget(max_wall_seconds=0),
        expected_output=TaskOutputContract(report_format=""),
    )


def _well_formed_contract(role: SpecialistRole = SpecialistRole.IMPLEMENTER) -> TaskContract:
    return TaskContract(
        task_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        role=role,
        project_goal="Лендинг для кофейни с формой бронирования столика",
        user_value="Пользователь сможет забронировать стол онлайн",
        task_goal="Добавить страницу /booking.html с формой бронирования",
        reason="Бронирование - основной acceptance criterion брифа",
        current_state=ProjectStateSummary(project_type="website", project_name="Coffee shop"),
        allowed_paths=["public/booking.html"],
        acceptance_criteria=[
            AcceptanceCriterion(
                id="ac1", description="Форма отправляется", verification_method="build"
            )
        ],
        validation_steps=derive_validation_steps(
            role,
            PlannedTask(
                local_id="t",
                title="t",
                role=role,
                goal="g",
                reason="r",
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac1", description="d", verification_method="build")
                ],
            ),
        ),
        budget=TaskBudget(max_wall_seconds=900),
    )


class TestScoreTaskContract:
    def test_minimal_bad_example_is_rejected(self) -> None:
        report = score_task_contract(_minimal_bad_contract())
        assert report.passed is False
        assert "project_goal" in report.missing_required
        assert "reason" in report.missing_required
        assert "acceptance_criteria" in report.missing_required
        assert "current_state" in report.missing_required

    def test_well_formed_contract_passes(self) -> None:
        report = score_task_contract(_well_formed_contract())
        assert report.passed is True
        assert report.score >= COMPLETENESS_PASS_THRESHOLD
        assert report.missing_required == []

    def test_readonly_role_does_not_require_allowed_paths(self) -> None:
        contract = _well_formed_contract(SpecialistRole.QA_REVIEWER)
        contract = contract.model_copy(update={"allowed_paths": [], "validation_steps": []})
        report = score_task_contract(contract)
        assert "allowed_paths_declared" not in report.missing_required
        assert "validation_steps" not in report.missing_required


class TestDeriveValidationSteps:
    def _planned(self, role: SpecialistRole, methods: list[str]) -> PlannedTask:
        return PlannedTask(
            local_id="t",
            title="t",
            role=role,
            goal="g",
            reason="r",
            acceptance_criteria=[
                AcceptanceCriterion(id=f"ac{i}", description="d", verification_method=m)
                for i, m in enumerate(methods)
            ],
        )

    def test_readonly_role_gets_no_scope_or_build_step(self) -> None:
        steps = derive_validation_steps(
            SpecialistRole.QA_REVIEWER, self._planned(SpecialistRole.QA_REVIEWER, [])
        )
        kinds = {s.kind for s in steps}
        assert "scope" not in kinds
        assert "build" not in kinds

    def test_implementer_always_gets_build_step(self) -> None:
        steps = derive_validation_steps(
            SpecialistRole.IMPLEMENTER, self._planned(SpecialistRole.IMPLEMENTER, [])
        )
        assert "build" in {s.kind for s in steps}
        assert "scope" in {s.kind for s in steps}

    def test_deploy_fixer_always_gets_runtime_step(self) -> None:
        steps = derive_validation_steps(
            SpecialistRole.DEPLOY_FIXER, self._planned(SpecialistRole.DEPLOY_FIXER, [])
        )
        assert "runtime" in {s.kind for s in steps}

    def test_preview_criterion_adds_preview_step(self) -> None:
        steps = derive_validation_steps(
            SpecialistRole.UI_UX_SPECIALIST,
            self._planned(SpecialistRole.UI_UX_SPECIALIST, ["preview"]),
        )
        assert "preview" in {s.kind for s in steps}

    def test_readonly_preview_criterion_does_not_block_qa(self) -> None:
        steps = derive_validation_steps(
            SpecialistRole.QA_REVIEWER,
            self._planned(SpecialistRole.QA_REVIEWER, ["preview"]),
        )
        assert "preview" not in {s.kind for s in steps}
        assert "build" not in {s.kind for s in steps}

    def test_security_reviewer_gets_security_step(self) -> None:
        steps = derive_validation_steps(
            SpecialistRole.SECURITY_REVIEWER, self._planned(SpecialistRole.SECURITY_REVIEWER, [])
        )
        assert "security" in {s.kind for s in steps}


@pytest.fixture()
def project(db: Session) -> Project:
    user = User(email=f"{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, type="website", name="Contract test")
    db.add(project)
    db.flush()
    return project


@pytest.fixture()
def run_and_plan(db: Session, project: Project) -> tuple[OrchestrationRun, OrchestrationPlan]:
    chat = Chat(project_id=project.id)
    db.add(chat)
    db.flush()
    run = OrchestrationRun(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
    db.add(run)
    db.flush()
    plan = OrchestrationPlan(run_id=run.id, version=1, status="active", graph_json="{}")
    db.add(plan)
    db.flush()
    return run, plan


class TestBuildTaskContractEndToEnd:
    def test_well_formed_planned_task_yields_passing_contract(
        self, db: Session, project: Project, run_and_plan, tmp_path
    ) -> None:
        run, plan = run_and_plan
        (tmp_path / "public").mkdir()
        (tmp_path / "public" / "index.html").write_text("<html></html>", encoding="utf-8")

        task = AgentTask(
            run_id=run.id,
            plan_id=plan.id,
            local_id="main",
            title="Add booking page",
            role=SpecialistRole.IMPLEMENTER.value,
            execution_kind="codex_task",
            status="pending",
            max_attempts=3,
        )
        db.add(task)
        db.flush()

        planned = PlannedTask(
            local_id="main",
            title="Add booking page",
            role=SpecialistRole.IMPLEMENTER,
            goal="Добавить страницу бронирования",
            reason="Это ключевой сценарий из брифа",
            relevant_paths=["public/"],
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="ac1", description="Страница собирается", verification_method="build"
                )
            ],
        )

        contract = build_task_contract(
            task=task,
            planned_task=planned,
            run_goal="Лендинг кофейни",
            context_engine=ContextEngine(db),
            project=project,
            workspace_root=tmp_path,
            git_sha=None,
            dependency_tasks=[],
            available_files=["public/index.html"],
            registered_skill_ids=set(),
        )

        report = score_task_contract(contract)
        assert report.passed is True, report.missing_required

    def test_readonly_role_gets_empty_allowed_paths_and_no_write_tools(
        self, db: Session, project: Project, run_and_plan, tmp_path
    ) -> None:
        run, plan = run_and_plan
        task = AgentTask(
            run_id=run.id,
            plan_id=plan.id,
            local_id="review",
            title="Review",
            role=SpecialistRole.SECURITY_REVIEWER.value,
            execution_kind="specialist_agent",
            status="pending",
            max_attempts=1,
        )
        db.add(task)
        db.flush()

        planned = PlannedTask(
            local_id="review",
            title="Review",
            role=SpecialistRole.SECURITY_REVIEWER,
            goal="Проверить изменения на утечку секретов",
            reason="Часть плана после Implementer",
            relevant_paths=["*"],  # even if the plan asks for everything, role ceiling wins
        )

        contract = build_task_contract(
            task=task,
            planned_task=planned,
            run_goal="Лендинг кофейни",
            context_engine=ContextEngine(db),
            project=project,
            workspace_root=tmp_path,
            git_sha=None,
            dependency_tasks=[],
            available_files=[],
            registered_skill_ids=set(),
        )

        assert contract.allowed_paths == []
        assert contract.forbidden_paths == ["*"]
        assert "write_file" not in contract.allowed_tools
        assert "read_file" in contract.allowed_tools


class TestAllowedCapabilitiesAndSkillsFromTask:
    """capability_router.py's route() decision is persisted onto task.skill_id/capability_id
    (engine.py's _materialize_plan_tasks) - the contract must surface that exact decision as
    what executors.py's Deterministic/Skill/McpExecutor read (allowed_capabilities[0] /
    allowed_skills[0]), not leave it stranded on the ORM row. Regression test for a bug where
    build_task_contract hardcoded allowed_capabilities=[] and never read task.capability_id at
    all, so every deterministic/MCP-routed task failed immediately in production."""

    def test_task_capability_id_reaches_the_contract(
        self, db: Session, project: Project, run_and_plan, tmp_path
    ) -> None:
        run, plan = run_and_plan
        task = AgentTask(
            run_id=run.id,
            plan_id=plan.id,
            local_id="check",
            title="Build check",
            role=SpecialistRole.QA_REVIEWER.value,
            execution_kind="deterministic_validation",
            status="pending",
            max_attempts=1,
            capability_id="platform:build_check",
        )
        db.add(task)
        db.flush()

        planned = PlannedTask(
            local_id="check",
            title="Build check",
            role=SpecialistRole.QA_REVIEWER,
            goal="Проверить сборку",
            reason="Часть плана после Implementer",
        )

        contract = build_task_contract(
            task=task,
            planned_task=planned,
            run_goal="Лендинг кофейни",
            context_engine=ContextEngine(db),
            project=project,
            workspace_root=tmp_path,
            git_sha=None,
            dependency_tasks=[],
            available_files=[],
            registered_skill_ids=set(),
        )

        assert contract.allowed_capabilities == ["platform:build_check"]

    def test_task_skill_id_is_first_in_allowed_skills(
        self, db: Session, project: Project, run_and_plan, tmp_path
    ) -> None:
        run, plan = run_and_plan
        task = AgentTask(
            run_id=run.id,
            plan_id=plan.id,
            local_id="provision",
            title="Provision Postgres",
            role=SpecialistRole.IMPLEMENTER.value,
            execution_kind="skill",
            status="pending",
            max_attempts=1,
            skill_id="provision_postgres",
        )
        db.add(task)
        db.flush()

        planned = PlannedTask(
            local_id="provision",
            title="Provision Postgres",
            role=SpecialistRole.IMPLEMENTER,
            goal="Подключить Postgres",
            reason="Проекту нужна база данных",
            suggested_skills=["provision_postgres", "database_migrations"],
        )

        contract = build_task_contract(
            task=task,
            planned_task=planned,
            run_goal="Сайт с базой данных",
            context_engine=ContextEngine(db),
            project=project,
            workspace_root=tmp_path,
            git_sha=None,
            dependency_tasks=[],
            available_files=[],
            registered_skill_ids={"provision_postgres", "database_migrations"},
        )

        assert contract.allowed_skills[0] == "provision_postgres"

    def test_no_router_decision_leaves_capabilities_empty(
        self, db: Session, project: Project, run_and_plan, tmp_path
    ) -> None:
        run, plan = run_and_plan
        task = AgentTask(
            run_id=run.id,
            plan_id=plan.id,
            local_id="impl",
            title="Implement",
            role=SpecialistRole.IMPLEMENTER.value,
            execution_kind="specialist_agent",
            status="pending",
            max_attempts=3,
        )
        db.add(task)
        db.flush()

        planned = PlannedTask(
            local_id="impl",
            title="Implement",
            role=SpecialistRole.IMPLEMENTER,
            goal="Написать страницу",
            reason="Ключевой сценарий брифа",
        )

        contract = build_task_contract(
            task=task,
            planned_task=planned,
            run_goal="Лендинг кофейни",
            context_engine=ContextEngine(db),
            project=project,
            workspace_root=tmp_path,
            git_sha=None,
            dependency_tasks=[],
            available_files=[],
            registered_skill_ids=set(),
        )

        assert contract.allowed_capabilities == []
