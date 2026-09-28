"""runtime_health_check - wraps the new `runtime_health_check` worker RPC action
(docker_control_actions._run_runtime_health_check) added in this same change. This is real new
capability, not a wrapper around something that already existed: before this, the platform's
only "is it actually up" signals were docker_adapter.py's flat 5-second-sleep status check at
deploy time and deployment_check.py's regex log scan - neither confirms the app is still
running later, checks for a restart loop, or confirms anything is listening on the app port.
"""

from __future__ import annotations

import asyncio

from src.services.docker_control_queue import submit_control_job
from src.services.orchestration.schemas import (
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill


class RuntimeHealthCheckSkill(BaseSkill):
    definition = SkillDefinition(
        id="runtime_health_check",
        version="1.0",
        title="Runtime health check",
        description="Confirm the deployed app container is running, not crash-looping, and listening on its port.",
        supported_roles=[SpecialistRole.DEPLOY_FIXER, SpecialistRole.QA_REVIEWER],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={},
        output_schema={
            "ok": "bool",
            "container_status": "string",
            "restart_count": "int",
            "restart_loop_suspected": "bool",
            "port_80_listening": "bool",
        },
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=2),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        result = await asyncio.to_thread(
            submit_control_job,
            action="runtime_health_check",
            project_id=context.project_id,
            timeout_seconds=30,
        )
        if result is None:
            return SkillResult(
                status="failed", summary="runtime_health_check RPC timed out or worker unreachable"
            )
        if not result.get("container_found", True):
            return SkillResult(
                status="failed", summary="no app container found for this project", output=result
            )

        summary_bits = [f"status={result.get('container_status')}"]
        if result.get("restart_loop_suspected"):
            summary_bits.append(
                f"restart_count={result.get('restart_count')} (suspected crash loop)"
            )
        if not result.get("port_80_listening"):
            summary_bits.append("port 80 not confirmed listening")

        return SkillResult(
            status="completed" if result.get("ok") else "partial",
            summary="; ".join(summary_bits),
            output=result,
        )
