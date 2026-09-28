"""build_repair / deploy_repair - thin skill wrappers around the two existing, already-working
repair mechanisms (agentic_artifacts.repair_artifact_with_agent for build-time failures,
deployment_check.check_and_repair_deployment for post-deploy runtime/config failures) rather
than reimplementing repair logic. Both underlying functions are synchronous and do real
subprocess/DB/LLM work, so execute() offloads them via asyncio.to_thread - the same pattern
loop.py already uses for build_project (agent/loop.py:143), needed here for the same reason
(don't block the event loop for a multi-second-to-multi-minute call).
"""

from __future__ import annotations

import asyncio

from src.services.orchestration.schemas import (
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill


class BuildRepairSkill(BaseSkill):
    definition = SkillDefinition(
        id="build_repair",
        version="1.0",
        title="Build repair",
        description="Feed a real Docker build error back to a coding-agent turn and let it patch the broken files (agentic_artifacts.repair_artifact_with_agent).",
        supported_roles=[SpecialistRole.BUILD_FIXER, SpecialistRole.IMPLEMENTER],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={"build_error": "string", "attempt": "int"},
        output_schema={"repaired": "bool"},
        risk_level=RiskLevel.MEDIUM,
        idempotent=False,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.db.models.project import Project
        from src.services.agentic_artifacts import repair_artifact_with_agent

        project = context.db.get(Project, context.project_id)
        if project is None:
            return SkillResult(status="failed", summary=f"project {context.project_id} not found")
        build_error = context.arguments.get("build_error", "")
        if not build_error.strip():
            return SkillResult(status="failed", summary="no build_error provided to repair against")

        try:
            await asyncio.to_thread(
                repair_artifact_with_agent,
                context.db,
                project,
                build_error,
                attempt=int(context.arguments.get("attempt", 1)),
            )
        except Exception as exc:  # noqa: BLE001
            return SkillResult(status="failed", summary=f"repair attempt raised: {exc}")

        return SkillResult(
            status="completed",
            summary="Repair pass applied and re-committed",
            output={"repaired": True},
        )


class DeployRepairSkill(BaseSkill):
    definition = SkillDefinition(
        id="deploy_repair",
        version="1.0",
        title="Deploy repair",
        description="Inspect the latest deployment for runtime/config errors and run one automatic repair-and-redeploy pass (deployment_check.check_and_repair_deployment).",
        supported_roles=[SpecialistRole.DEPLOY_FIXER],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={"force_error": "string (optional)"},
        output_schema={"checked": "bool", "found_errors": "bool", "fixed": "bool"},
        risk_level=RiskLevel.MEDIUM,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.db.models.project import Project
        from src.services.deployment_check import check_and_repair_deployment

        project = context.db.get(Project, context.project_id)
        if project is None:
            return SkillResult(status="failed", summary=f"project {context.project_id} not found")

        try:
            outcome = await asyncio.to_thread(
                check_and_repair_deployment,
                context.db,
                project,
                force_error=context.arguments.get("force_error"),
            )
        except Exception as exc:  # noqa: BLE001
            return SkillResult(status="failed", summary=f"deploy repair raised: {exc}")

        status = (
            "completed" if outcome.get("fixed") or not outcome.get("found_errors") else "partial"
        )
        return SkillResult(status=status, summary=outcome.get("summary", ""), output=outcome)
