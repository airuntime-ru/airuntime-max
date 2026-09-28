"""Shared base for concrete skills - sensible default `match`/`plan`/`validate`/`compensate`
so each skill in this package only has to implement `execute` (and override the defaults where
a skill genuinely needs different match/validation logic, e.g. visual_preview_review's
score-based match).
"""

from __future__ import annotations

from src.services.orchestration.schemas import (
    CompensationResult,
    PlannedTask,
    SkillDefinition,
    SkillExecutionPlan,
    SkillMatch,
    SkillResult,
    SkillValidation,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext


def aggregate_usage(records: list[dict]) -> dict:
    """Merge usage from an initial structured call and its optional repair retry."""
    totals: dict[str, int | float] = {}
    for record in records:
        for key, value in record.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            totals[key] = totals.get(key, 0) + value
    return totals


class BaseSkill:
    definition: SkillDefinition

    async def match(self, planned_task: PlannedTask, *, role: SpecialistRole) -> SkillMatch:
        suggested = self.definition.id in planned_task.suggested_skills
        return SkillMatch(
            matched=suggested,
            confidence=0.9 if suggested else 0.0,
            reason="explicitly suggested by the plan" if suggested else "not suggested",
        )

    async def plan(self, context: SkillContext) -> SkillExecutionPlan:
        return SkillExecutionPlan(steps=[self.definition.title])

    async def execute(self, context: SkillContext) -> SkillResult:
        raise NotImplementedError

    async def validate(self, context: SkillContext, result: SkillResult) -> SkillValidation:
        if result.status == "completed":
            return SkillValidation(passed=True)
        return SkillValidation(passed=False, findings=[result.summary])

    async def compensate(self, context: SkillContext, result: SkillResult) -> CompensationResult:
        return CompensationResult(
            compensated=False,
            notes="no compensation implemented - skills in this registry are idempotent, so a retry is the recovery path",
        )
