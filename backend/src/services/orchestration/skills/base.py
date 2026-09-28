"""The Skill contract itself - "серверный безопасный workflow, а не prompt и не отдельная
модель." A Skill is plain Python: it may call an LLM internally (e.g. visual_preview_review
reuses product_pipeline's review call) but the *decision* to run it, its allowed
tools/capabilities, and its retry/idempotency behavior are all server-controlled, never
delegated to the model driving the surrounding task.

`SkillRegistry` is the single place skills get registered (skills/__init__.py populates a
module-level instance at import time) - capability_router.py and capability_provider.py both
read from it, never a hardcoded skill list of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from src.services.orchestration.schemas import (
    CompensationResult,
    PlannedTask,
    ProjectType,
    SkillDefinition,
    SkillExecutionPlan,
    SkillMatch,
    SkillResult,
    SkillValidation,
    SpecialistRole,
)


@dataclass
class SkillContext:
    """Tool-tier context (spec section 6: "только необходимые входные данные;
    project-scoped identifiers; никакой лишней истории") - deliberately much smaller than a
    TaskContract. Skills read workspace_root/project_id directly; they never receive the full
    chat history or other tasks' contracts.

    `db` is a live SQLAlchemy Session, not a serializable field - every existing service
    function these skills wrap (project_services.py, secrets.py, project_git.py, ...) is
    synchronous-Session-based, matching this codebase's house pattern (no repository layer of
    its own - see repository.py's docstring for why the orchestration domain's repositories are
    the exception, not the rule), so skills need real DB access to reuse them rather than
    reimplementing DB writes from scratch. Never put `db` in anything that gets logged,
    persisted as JSON, or handed to an LLM - only CapabilityContext (schemas.py) does that, and
    it deliberately has no db field."""

    project_id: str
    run_id: str
    task_id: str
    workspace_root: str
    project_type: ProjectType
    arguments: dict
    db: Session


@runtime_checkable
class Skill(Protocol):
    definition: SkillDefinition

    async def match(self, planned_task: PlannedTask, *, role: SpecialistRole) -> SkillMatch: ...

    async def plan(self, context: SkillContext) -> SkillExecutionPlan: ...

    async def execute(self, context: SkillContext) -> SkillResult: ...

    async def validate(self, context: SkillContext, result: SkillResult) -> SkillValidation: ...

    async def compensate(
        self, context: SkillContext, result: SkillResult
    ) -> CompensationResult: ...


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.definition.id in self._skills:
            raise ValueError(f"skill {skill.definition.id!r} already registered")
        self._skills[skill.definition.id] = skill

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def all_ids(self) -> set[str]:
        return set(self._skills.keys())

    def all_definitions(self) -> list[SkillDefinition]:
        return [s.definition for s in self._skills.values()]

    def for_role_and_type(self, *, role: SpecialistRole, project_type: ProjectType) -> list[Skill]:
        return [
            s
            for s in self._skills.values()
            if role in s.definition.supported_roles
            and project_type in s.definition.supported_project_types
        ]

    async def find_best_match(
        self, planned_task: PlannedTask, *, role: SpecialistRole, project_type: ProjectType
    ) -> tuple[Skill, SkillMatch] | None:
        """Confidence-ranked match among skills this role/project_type combination may use at
        all (role_policy.py narrows further before a match is actually granted - see
        capability_router.py). Returns the highest-confidence match with matched=True, or None."""
        candidates = self.for_role_and_type(role=role, project_type=project_type)
        best: tuple[Skill, SkillMatch] | None = None
        for skill in candidates:
            outcome = await skill.match(planned_task, role=role)
            if not outcome.matched:
                continue
            if best is None or outcome.confidence > best[1].confidence:
                best = (skill, outcome)
        return best


registry = SkillRegistry()
