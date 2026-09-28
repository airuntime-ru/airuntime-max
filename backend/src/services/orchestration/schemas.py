"""Pydantic contracts for the orchestration domain: the execution graph the planner produces,
the self-sufficient TaskContract each executor receives, and the claimed-result/factual-evidence
split validation is built on.

None of these are SQLAlchemy models - they're the JSON shape stored in the *_json Text columns
on OrchestrationPlan/AgentTask (db/models/), serialized with `.model_dump_json()` /
parsed with `.model_validate_json()`. Keeping them as plain Pydantic models (not tied to the ORM)
is what lets a TaskContract be hashed, diffed, scored for completeness, and handed to an
executor that has zero DB access (e.g. a Codex container) without any ORM leakage.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

ProjectType = Literal["website", "telegram_bot", "mixed"]
Complexity = Literal["simple", "compound", "large"]


class SpecialistRole(StrEnum):
    """Every role a task can be assigned. Distinctness is enforced by role_policy.py's policy
    matrix (system prompt, context shape, allowed tools/skills, write scope, failure policy) -
    this enum is just the stable identifier the rest of the system keys off."""

    PRODUCT_PLANNER = "product_planner"
    SOLUTION_ARCHITECT = "solution_architect"
    IMPLEMENTER = "implementer"
    UI_UX_SPECIALIST = "ui_ux_specialist"
    BUILD_FIXER = "build_fixer"
    DEPLOY_FIXER = "deploy_fixer"
    QA_REVIEWER = "qa_reviewer"
    SECURITY_REVIEWER = "security_reviewer"
    INTEGRATION_AGENT = "integration_agent"


class ExecutionKind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"
    SPECIALIST_AGENT = "specialist_agent"
    CODEX_TASK = "codex_task"
    DETERMINISTIC_VALIDATION = "deterministic_validation"
    USER_INPUT = "user_input"
    INTEGRATION = "integration"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WriteScope(StrEnum):
    """What a task is allowed to touch. Computed server-side from role + declared paths -
    never taken as-is from what the planner/model *asked* for (contract_builder.py narrows it
    against role_policy.py's ceiling for that role)."""

    NONE = "none"
    SCOPED_PATHS = "scoped_paths"
    FULL_WORKSPACE = "full_workspace"


class ExecutionPreference(StrEnum):
    """Planner's hint for the capability router - a hint, not a directive; capability_router.py
    always re-derives the actual route (deterministic skill beats a specialist agent whenever a
    verified skill matches, regardless of what the planner preferred)."""

    DETERMINISTIC_SKILL = "deterministic_skill"
    SPECIALIST_AGENT = "specialist_agent"
    EITHER = "either"


class WorkspaceMode(StrEnum):
    SHARED_SEQUENTIAL = "shared_sequential"
    PARALLEL_READ_ONLY = "parallel_read_only"
    ISOLATED_WORKTREE = "isolated_worktree"


class FailureClass(StrEnum):
    PLANNING_ERROR = "planning_error"
    CONTRACT_INCOMPLETE = "contract_incomplete"
    AGENT_ERROR = "agent_error"
    INVALID_OUTPUT = "invalid_output"
    SCOPE_VIOLATION = "scope_violation"
    TOOL_ERROR = "tool_error"
    SKILL_ERROR = "skill_error"
    MCP_ERROR = "mcp_error"
    BUILD_ERROR = "build_error"
    TEST_ERROR = "test_error"
    DEPLOY_ERROR = "deploy_error"
    RUNTIME_ERROR = "runtime_error"
    MISSING_SECRET = "missing_secret"
    MISSING_SERVICE = "missing_service"
    VALIDATION_FAILED = "validation_failed"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"


class FailureDecision(StrEnum):
    RETRY = "retry"
    REPAIR = "repair"
    REPLACE_EXECUTOR = "replace_executor"
    REPLACE_SKILL = "replace_skill"
    REPLAN = "replan"
    WAIT_FOR_USER = "wait_for_user"
    ROLLBACK = "rollback"
    FAIL = "fail"


# --------------------------------------------------------------------------------------------
# Execution graph (planner output)
# --------------------------------------------------------------------------------------------


class AcceptanceCriterion(BaseModel):
    id: str
    description: str
    verification_method: Literal["build", "test", "preview", "runtime", "manual", "llm_review"]
    required: bool = True

    @model_validator(mode="before")
    @classmethod
    def _supply_missing_llm_id(cls, value: Any) -> Any:
        """Keep otherwise valid planner output usable when the model omits a criterion id."""
        if isinstance(value, str):
            value = {
                "description": value.strip() or "criterion",
                "verification_method": "manual",
            }
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        method = normalized.get("verification_method")
        if isinstance(method, str):
            key = method.strip().lower().replace(" ", "_").replace("-", "_")
            aliases = {
                "llm": "llm_review",
                "review": "llm_review",
                "visual": "preview",
                "browser": "preview",
                "unit": "test",
                "compile": "build",
            }
            normalized["verification_method"] = aliases.get(key, key)
        if str(normalized.get("id") or "").strip():
            return normalized
        description = str(normalized.get("description") or "criterion")
        digest = hashlib.sha256(description.encode("utf-8")).hexdigest()[:12]
        normalized["id"] = f"criterion_{digest}"
        return normalized


class Risk(BaseModel):
    description: str
    severity: RiskLevel = RiskLevel.MEDIUM
    mitigation: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_string_risk(cls, value: Any) -> Any:
        """Codex often emits risks as plain strings; a schema mismatch here used to trigger a
        full extra planning container (~3-4 minutes) for a repair retry."""
        if isinstance(value, str):
            text = value.strip() or "риск"
            return {"description": text}
        return value

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        key = value.strip().lower()
        aliases = {
            "низкий": "low",
            "средний": "medium",
            "высокий": "high",
            "критический": "critical",
            "med": "medium",
        }
        return aliases.get(key, key)


class ExecutionBudget(BaseModel):
    max_credits: int | None = None
    max_wall_seconds: int | None = None
    max_tasks: int | None = None
    max_attempts_per_task: int = 3


class PlannedTask(BaseModel):
    local_id: str
    title: str
    role: SpecialistRole
    goal: str
    reason: str
    execution_preference: ExecutionPreference = ExecutionPreference.EITHER
    dependencies: list[str] = Field(default_factory=list)
    relevant_paths: list[str] = Field(default_factory=list)
    suggested_skills: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    write_scope: WriteScope = WriteScope.SCOPED_PATHS

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_quirks(cls, value: Any) -> Any:
        """Economy models often omit reason/title or invent role casing - keep the plan usable."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        local_id = str(data.get("local_id") or data.get("id") or "task").strip() or "task"
        data["local_id"] = local_id
        title = str(data.get("title") or "").strip()
        goal = str(data.get("goal") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not title:
            title = goal or local_id
        if not goal:
            goal = title
        if not reason:
            reason = title
        data["title"] = title
        data["goal"] = goal
        data["reason"] = reason
        role = data.get("role")
        if isinstance(role, str):
            normalized = role.strip().lower().replace(" ", "_").replace("-", "_")
            aliases = {
                "implementer": "implementer",
                "implementation": "implementer",
                "coder": "implementer",
                "developer": "implementer",
                "ui_ux_specialist": "ui_ux_specialist",
                "uiux": "ui_ux_specialist",
                "designer": "ui_ux_specialist",
                "qa": "qa_reviewer",
                "qa_reviewer": "qa_reviewer",
                "reviewer": "qa_reviewer",
                "architect": "solution_architect",
                "solution_architect": "solution_architect",
                "planner": "product_planner",
                "product_planner": "product_planner",
                "build_fixer": "build_fixer",
                "deploy_fixer": "deploy_fixer",
                "security_reviewer": "security_reviewer",
                "integration_agent": "integration_agent",
                "integrator": "integration_agent",
            }
            data["role"] = aliases.get(normalized, normalized)
        criteria = data.get("acceptance_criteria")
        if criteria is None:
            data["acceptance_criteria"] = [
                {
                    "description": f"{title} выполнен",
                    "verification_method": "manual",
                }
            ]
        return data

    @field_validator("execution_preference", mode="before")
    @classmethod
    def _normalize_scheduling_aliases(cls, value: Any) -> Any:
        """The planner sometimes confuses execution routing with task scheduling."""
        if isinstance(value, str) and value.strip().lower() in {"parallel", "sequential"}:
            return ExecutionPreference.EITHER
        return value

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalize_risk_level(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        key = value.strip().lower()
        aliases = {
            "низкий": "low",
            "средний": "medium",
            "высокий": "high",
            "критический": "critical",
            "med": "medium",
        }
        return aliases.get(key, key)

    @field_validator("write_scope", mode="before")
    @classmethod
    def _normalize_write_scope(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        key = value.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "scoped": "scoped_paths",
            "paths": "scoped_paths",
            "full": "full_workspace",
            "workspace": "full_workspace",
            "readonly": "none",
            "read_only": "none",
        }
        return aliases.get(key, key)

    @field_validator("local_id")
    @classmethod
    def _local_id_not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("local_id must not be empty")
        return value.strip()

    @field_validator("dependencies")
    @classmethod
    def _no_self_dependency(cls, value: list[str], info) -> list[str]:  # noqa: ANN001
        local_id = info.data.get("local_id")
        if local_id and local_id in value:
            raise ValueError(f"task {local_id!r} cannot depend on itself")
        return value


class ExecutionPlan(BaseModel):
    goal: str
    complexity: Complexity
    tasks: list[PlannedTask]
    final_acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    estimated_budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @model_validator(mode="before")
    @classmethod
    def _coerce_plan_quirks(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        complexity = data.get("complexity")
        if isinstance(complexity, str):
            key = complexity.strip().lower()
            aliases = {
                "trivial": "simple",
                "small": "simple",
                "medium": "compound",
                "multi": "compound",
                "big": "large",
                "complex": "large",
            }
            data["complexity"] = aliases.get(key, key)
        if not str(data.get("goal") or "").strip():
            tasks = data.get("tasks") or []
            if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
                data["goal"] = str(tasks[0].get("title") or tasks[0].get("goal") or "Запрос")
            else:
                data["goal"] = "Запрос"
        return data

    @field_validator("tasks")
    @classmethod
    def _at_least_one_task(cls, value: list[PlannedTask]) -> list[PlannedTask]:
        if not value:
            raise ValueError("plan must contain at least one task")
        return value


# --------------------------------------------------------------------------------------------
# Task contract (what an executor actually receives - must be self-sufficient)
# --------------------------------------------------------------------------------------------


class ContextItem(BaseModel):
    kind: Literal[
        "summary",
        "file_excerpt",
        "build_error",
        "deploy_error",
        "runtime_error",
        "secret_inventory",
        "service_inventory",
        "architecture_note",
        "user_constraint",
    ]
    title: str
    content: str
    provenance: str
    token_estimate: int = 0


class FileReference(BaseModel):
    path: str
    reason: str
    content: str | None = None
    truncated: bool = False


class DependencyResult(BaseModel):
    local_id: str
    title: str
    status: str
    summary: str
    key_outputs: list[str] = Field(default_factory=list)


class ValidationStep(BaseModel):
    kind: Literal["scope", "static", "build", "product", "preview", "runtime", "security"]
    description: str
    required: bool = True


class ProjectStateSummary(BaseModel):
    project_type: ProjectType
    project_name: str
    current_files: list[str] = Field(default_factory=list)
    recent_changes_summary: str = ""
    git_head_sha: str | None = None
    # Key names only - never values. See context_engine.py's redaction pass.
    secrets_inventory: list[str] = Field(default_factory=list)
    services_inventory: list[str] = Field(default_factory=list)
    last_build_status: str | None = None
    last_deploy_status: str | None = None


class TaskBudget(BaseModel):
    max_credits: int | None = None
    max_wall_seconds: int = 900
    max_attempts: int = 3
    max_tool_calls: int | None = None
    max_output_chars: int | None = None


class TaskOutputContract(BaseModel):
    expected_artifacts: list[str] = Field(default_factory=list)
    report_format: str = "TaskResult JSON"
    must_not_include: list[str] = Field(
        default_factory=lambda: ["secret values", "raw environment dumps"]
    )


class TaskContract(BaseModel):
    task_id: UUID
    run_id: UUID
    role: SpecialistRole

    project_goal: str
    user_value: str
    task_goal: str
    reason: str

    current_state: ProjectStateSummary
    relevant_context: list[ContextItem] = Field(default_factory=list)
    relevant_files: list[FileReference] = Field(default_factory=list)
    dependency_results: list[DependencyResult] = Field(default_factory=list)

    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)

    constraints: list[str] = Field(default_factory=list)
    architectural_rules: list[str] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    validation_steps: list[ValidationStep] = Field(default_factory=list)

    budget: TaskBudget
    expected_output: TaskOutputContract = Field(default_factory=TaskOutputContract)


# --------------------------------------------------------------------------------------------
# Task result (claimed) vs. task evidence (factual) - see validation.py
# --------------------------------------------------------------------------------------------


class ClaimedCheck(BaseModel):
    name: str
    passed: bool
    details: str | None = None


class ClaimedAcceptanceResult(BaseModel):
    criterion_id: str
    status: Literal["passed", "failed", "unknown"]
    notes: str | None = None


class TaskResult(BaseModel):
    """What the executor *says* happened. Never treated as proof - see TaskEvidence and
    validation.py. Stored verbatim in AgentTask.result_json."""

    status: Literal["completed", "partial", "failed", "waiting_for_user"]
    summary: str
    claimed_changed_files: list[str] = Field(default_factory=list)
    checks: list[ClaimedCheck] = Field(default_factory=list)
    acceptance_results: list[ClaimedAcceptanceResult] = Field(default_factory=list)
    requested_secrets: list[str] = Field(default_factory=list)
    requested_services: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None


class TaskEvidence(BaseModel):
    """What the server *independently observed*. Stored in AgentTask.evidence_json, collected
    by evidence.py after every execution regardless of what TaskResult claimed. Validation
    (validation.py) is computed from this, never from TaskResult directly."""

    git_diff_stat: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    created_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    build_result: dict | None = None
    test_result: dict | None = None
    lint_result: dict | None = None
    dependency_changes: list[str] = Field(default_factory=list)
    secret_scan_findings: list[str] = Field(default_factory=list)
    service_requests: list[str] = Field(default_factory=list)
    secret_requests: list[str] = Field(default_factory=list)
    preview_result: dict | None = None
    runtime_health_result: dict | None = None
    duration_seconds: float = 0.0
    usage: dict | None = None
    logs_digest: str | None = None


class ValidationFinding(BaseModel):
    step: str
    passed: bool
    severity: RiskLevel = RiskLevel.MEDIUM
    message: str
    evidence_ref: str | None = None


class ValidationResult(BaseModel):
    accepted: bool
    findings: list[ValidationFinding] = Field(default_factory=list)
    acceptance_results: list[ClaimedAcceptanceResult] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Skills (section 10)
# --------------------------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    max_attempts: int = 3
    backoff_seconds: float = 2.0


class SkillDefinition(BaseModel):
    id: str
    version: str
    title: str
    description: str
    supported_roles: list[SpecialistRole]
    supported_project_types: list[ProjectType]
    input_schema: dict
    output_schema: dict
    required_tools: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    idempotent: bool = True
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


class SkillMatch(BaseModel):
    matched: bool
    confidence: float = 0.0
    reason: str = ""


class SkillExecutionPlan(BaseModel):
    steps: list[str] = Field(default_factory=list)
    estimated_seconds: float | None = None


class SkillResult(BaseModel):
    status: Literal["completed", "failed", "partial"]
    summary: str
    output: dict = Field(default_factory=dict)
    evidence: dict | None = None


class SkillValidation(BaseModel):
    passed: bool
    findings: list[str] = Field(default_factory=list)


class CompensationResult(BaseModel):
    compensated: bool
    notes: str = ""


# --------------------------------------------------------------------------------------------
# MCP capability abstraction (section 11)
# --------------------------------------------------------------------------------------------


class CapabilityDefinition(BaseModel):
    id: str
    title: str
    description: str
    input_schema: dict
    output_schema: dict
    risk_level: RiskLevel = RiskLevel.MEDIUM
    side_effects: bool = False
    source: Literal["platform", "skill", "mcp"] = "platform"


class CapabilityContext(BaseModel):
    project_id: UUID
    run_id: UUID
    task_id: UUID
    role: SpecialistRole


class CapabilityResult(BaseModel):
    status: Literal["completed", "failed"]
    output: dict = Field(default_factory=dict)
    error: str | None = None
