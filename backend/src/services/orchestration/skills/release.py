"""release_checkpoint / rollback_release - thin, explicit wrappers around project_git's
existing commit/rollback primitives, given IntegrationAgent/QA-gated names so the orchestration
graph can express "checkpoint here" / "roll back to the last accepted checkpoint" as ordinary
skill-routed tasks rather than ad hoc calls buried in engine.py. git_transaction.py (this same
change) is what actually calls commit_snapshot for every normal task commit - these two skills
are for the explicit, planner-visible checkpoint/rollback operations spec section 10 asks for.
"""

from __future__ import annotations

from pathlib import Path

from src.services.orchestration.schemas import (
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill


class ReleaseCheckpointSkill(BaseSkill):
    definition = SkillDefinition(
        id="release_checkpoint",
        version="1.0",
        title="Release checkpoint",
        description="Commit the current workspace state as a named checkpoint.",
        supported_roles=[SpecialistRole.INTEGRATION_AGENT, SpecialistRole.IMPLEMENTER],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={"message": "string"},
        output_schema={"commit_sha": "string|null"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=2),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.services.project_git import ProjectGitError, commit_snapshot

        workspace_root = Path(context.workspace_root)
        message = context.arguments.get("message", "Release checkpoint")
        try:
            sha = commit_snapshot(workspace_root, message=message)
        except ProjectGitError as exc:
            return SkillResult(status="failed", summary=f"checkpoint commit failed: {exc}")
        return SkillResult(
            status="completed",
            summary="Checkpoint created" if sha else "Nothing to checkpoint (no changes)",
            output={"commit_sha": sha},
        )


class RollbackReleaseSkill(BaseSkill):
    definition = SkillDefinition(
        id="rollback_release",
        version="1.0",
        title="Rollback release",
        description="Roll the workspace back to a specific prior commit, recorded as a new commit (never a destructive history rewrite).",
        supported_roles=[SpecialistRole.INTEGRATION_AGENT, SpecialistRole.DEPLOY_FIXER],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={"commit_hash": "string", "message": "string"},
        output_schema={"commit_sha": "string"},
        risk_level=RiskLevel.HIGH,
        idempotent=False,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.services.project_git import ProjectGitError, rollback_to

        commit_hash = context.arguments.get("commit_hash", "")
        if not commit_hash:
            return SkillResult(status="failed", summary="no commit_hash provided to roll back to")

        workspace_root = Path(context.workspace_root)
        message = context.arguments.get("message", f"Rollback to {commit_hash[:8]}")
        try:
            new_sha = rollback_to(workspace_root, commit_hash=commit_hash, message=message)
        except ProjectGitError as exc:
            return SkillResult(status="failed", summary=f"rollback failed: {exc}")
        return SkillResult(
            status="completed",
            summary=f"Rolled back to {commit_hash[:8]}",
            output={"commit_sha": new_sha},
        )
