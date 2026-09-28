"""Turns a PlannedTask + the current run/project state into a self-sufficient TaskContract, and
scores its completeness so an incomplete contract never reaches an executor.

This is the module that makes the spec's central complaint impossible to reproduce:

    {"title": "Сделать backend", "instructions": "Сделай хороший backend"}

`score_task_contract` hard-fails (score capped, `passed=False`) whenever any of the fields that
example is missing - task_goal, reason, current_state, acceptance_criteria, validation_steps,
budget - are empty. A contract that fails scoring is never handed to executors.py; the caller
(engine.py) routes it back through replanner.py instead (spec: "Неполный контракт не
запускается и возвращается planner на доработку").

Everything a task is *allowed* to do here is computed from role_policy.py, never taken as-is
from PlannedTask - a plan can *suggest* paths/skills, contract_builder can only narrow that
suggestion against the role's ceiling, never widen it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from src.db.models.agent_task import AgentTask
from src.db.models.project import Project
from src.services.orchestration.context_engine import ContextEngine
from src.services.orchestration.role_policy import (
    compute_write_scope,
    expand_allowed_paths_for_project,
    filter_skills,
    filter_tools,
    get_role_policy,
)
from src.services.orchestration.schemas import (
    ContextItem,
    PlannedTask,
    SpecialistRole,
    TaskBudget,
    TaskContract,
    TaskOutputContract,
    ValidationStep,
    WriteScope,
)

COMPLETENESS_PASS_THRESHOLD = 0.75


@dataclass
class ContractCompletenessReport:
    score: float
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    passed: bool = False


def score_task_contract(contract: TaskContract) -> ContractCompletenessReport:
    """Deterministic, LLM-free scoring - every check here is a plain presence/non-triviality
    check, not a quality judgment (quality is QAReviewer's job, on the *result*, later)."""
    role_readonly = get_role_policy(contract.role).write_scope_ceiling == WriteScope.NONE

    required: list[tuple[str, bool]] = [
        ("project_goal", bool(contract.project_goal.strip())),
        ("task_goal", bool(contract.task_goal.strip())),
        ("reason", bool(contract.reason.strip())),
        ("current_state", bool(contract.current_state.project_name.strip())),
        ("acceptance_criteria", len(contract.acceptance_criteria) > 0),
        ("validation_steps", len(contract.validation_steps) > 0 or role_readonly),
        ("budget", contract.budget.max_wall_seconds > 0),
        ("allowed_paths_declared", role_readonly or bool(contract.allowed_paths)),
        ("expected_output", bool(contract.expected_output.report_format.strip())),
    ]
    optional: list[tuple[str, bool]] = [
        ("user_value", bool(contract.user_value.strip())),
        (
            "relevant_context_or_files",
            bool(contract.relevant_context) or bool(contract.relevant_files),
        ),
        ("constraints_or_rules", bool(contract.constraints) or bool(contract.architectural_rules)),
        (
            "dependency_results",
            bool(contract.dependency_results) or True,
        ),  # absence is fine for a root task
    ]

    missing_required = [name for name, present in required if not present]
    missing_optional = [name for name, present in optional if not present]

    required_ratio = (len(required) - len(missing_required)) / len(required)
    optional_ratio = (len(optional) - len(missing_optional)) / len(optional)
    score = round(0.8 * required_ratio + 0.2 * optional_ratio, 3)
    passed = not missing_required and score >= COMPLETENESS_PASS_THRESHOLD
    return ContractCompletenessReport(
        score=score,
        missing_required=missing_required,
        missing_optional=missing_optional,
        passed=passed,
    )


def derive_validation_steps(
    role: SpecialistRole, planned_task: PlannedTask
) -> list[ValidationStep]:
    policy = get_role_policy(role)
    steps: list[ValidationStep] = []
    if policy.write_scope_ceiling == WriteScope.NONE:
        # Reviewers report on evidence. They must not be hard-failed by the site's own
        # preview/build issues — that retried the same QA node until loop_detected.
        if role == SpecialistRole.SECURITY_REVIEWER:
            steps.append(
                ValidationStep(
                    kind="security",
                    description="Явная проверка security-инвариантов",
                    required=True,
                )
            )
        return steps

    steps.append(
        ValidationStep(
            kind="scope",
            description="Изменения не выходят за allowed_paths и не затрагивают forbidden_paths",
            required=True,
        )
    )
    # Required (not advisory) for anything that writes: validation.py only treats a finding as
    # blocking when its step is declared here, so without this row a syntactically broken .py
    # or malformed package.json would be recorded in the evidence and then accepted anyway.
    steps.append(
        ValidationStep(
            kind="static",
            description="Изменённые .py/.json файлы синтаксически корректны",
            required=True,
        )
    )
    methods = {c.verification_method for c in planned_task.acceptance_criteria}
    build_roles = (
        SpecialistRole.IMPLEMENTER,
        SpecialistRole.BUILD_FIXER,
        SpecialistRole.UI_UX_SPECIALIST,
        SpecialistRole.INTEGRATION_AGENT,
    )
    if "build" in methods or role in build_roles:
        steps.append(
            ValidationStep(
                kind="build", description="Проект собирается (build_project)", required=True
            )
        )
    if "preview" in methods:
        steps.append(
            ValidationStep(
                kind="preview", description="Предпросмотр без критических ошибок", required=True
            )
        )
    if "runtime" in methods or role == SpecialistRole.DEPLOY_FIXER:
        steps.append(
            ValidationStep(
                kind="runtime", description="Рантайм-проверка после деплоя", required=True
            )
        )
    return steps


def build_task_contract(
    *,
    task: AgentTask,
    planned_task: PlannedTask,
    run_goal: str,
    context_engine: ContextEngine,
    project: Project,
    workspace_root: Path,
    git_sha: str | None,
    dependency_tasks: list[AgentTask],
    available_files: list[str],
    registered_skill_ids: set[str],
    error_context_items: list[ContextItem] | None = None,
    user_value: str | None = None,
    last_build_status: str | None = None,
    last_deploy_status: str | None = None,
    default_timeout_seconds: int = 900,
) -> TaskContract:
    role = SpecialistRole(task.role)
    policy = get_role_policy(role)

    allowed_paths, forbidden_paths = compute_write_scope(
        role,
        requested_paths=planned_task.relevant_paths,
        fallback_relevant_paths=planned_task.relevant_paths,
    )
    allowed_paths = expand_allowed_paths_for_project(project.type, role, allowed_paths)
    allowed_tools = filter_tools(role)
    role_permitted_skills = filter_skills(
        role, planned_task.suggested_skills, registered_skill_ids=registered_skill_ids
    )
    # capability_router.py's route() already decided *the* skill/capability for this task
    # (persisted as task.skill_id/task.capability_id at creation, engine.py's
    # _materialize_plan_tasks) - executors.py's SkillExecutor/DeterministicExecutor/McpExecutor
    # read allowed_skills[0]/allowed_capabilities[0] (or, for MCP, the "mcp:"-prefixed entry) as
    # THE thing to run, not a menu to choose from, so the router's actual match must be first.
    allowed_skills = (
        [task.skill_id, *[s for s in role_permitted_skills if s != task.skill_id]]
        if task.skill_id
        else role_permitted_skills
    )
    allowed_capabilities = [task.capability_id] if task.capability_id else []

    current_state = context_engine.build_project_state_summary(
        project,
        workspace_root,
        git_sha=git_sha,
        last_build_status=last_build_status,
        last_deploy_status=last_deploy_status,
    )
    relevant_files = context_engine.build_relevant_files(
        workspace_root,
        available_files=available_files,
        goal_text=planned_task.goal,
        relevant_path_hints=planned_task.relevant_paths,
    )
    dependency_results = context_engine.build_dependency_results(dependency_tasks)

    constraints: list[str] = []
    if not policy.can_request_secrets:
        constraints.append(
            "Эта роль не может запрашивать новые секреты (request_secret недоступен)."
        )
    if not policy.can_request_services:
        constraints.append(
            "Эта роль не может запрашивать новые сервисы (request_service недоступен)."
        )
    if forbidden_paths == ["*"]:
        constraints.append("Эта роль read-only: изменения файлов не допускаются.")

    budget = TaskBudget(
        max_wall_seconds=default_timeout_seconds,
        max_attempts=task.max_attempts,
    )

    return TaskContract(
        task_id=UUID(str(task.id)),
        run_id=UUID(str(task.run_id)),
        role=role,
        project_goal=run_goal,
        user_value=user_value or run_goal,
        task_goal=planned_task.goal,
        reason=planned_task.reason,
        current_state=current_state,
        relevant_context=list(error_context_items or []),
        relevant_files=relevant_files,
        dependency_results=dependency_results,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        allowed_tools=allowed_tools,
        allowed_skills=allowed_skills,
        allowed_capabilities=allowed_capabilities,
        constraints=constraints,
        architectural_rules=list(policy.default_architectural_rules),
        acceptance_criteria=planned_task.acceptance_criteria,
        validation_steps=derive_validation_steps(role, planned_task),
        budget=budget,
        expected_output=TaskOutputContract(),
    )
