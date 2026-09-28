"""secret_slot_setup - the generic version of telegram.py's TelegramBotSetupSkill: reserve a
named secret slot for ANY key, not just TELEGRAM_BOT_TOKEN. Wraps secrets.ensure_secret_placeholder
- never sees or sets a value.
"""

from __future__ import annotations

from src.services.orchestration.schemas import (
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill


class SecretSlotSetupSkill(BaseSkill):
    definition = SkillDefinition(
        id="secret_slot_setup",
        version="1.0",
        title="Secret slot setup",
        description="Reserve a named secret slot (key + reason) for the user to fill in later - never touches a value.",
        supported_roles=[SpecialistRole.IMPLEMENTER],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={"key": "string", "reason": "string"},
        output_schema={"key": "string", "already_filled": "bool"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=2),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.db.models.project import Project
        from src.services.secrets import ensure_secret_placeholder

        key = (context.arguments.get("key") or "").strip()
        if not key:
            return SkillResult(status="failed", summary="no secret key provided")

        project = context.db.get(Project, context.project_id)
        if project is None:
            return SkillResult(status="failed", summary=f"project {context.project_id} not found")

        secret, created = ensure_secret_placeholder(
            context.db, project, key, context.arguments.get("reason", "")
        )
        already_filled = secret.encrypted_value is not None
        return SkillResult(
            status="completed",
            summary=f"Secret slot {key!r} {'already filled' if already_filled else 'reserved, awaiting user input'}",
            output={"key": key, "already_filled": already_filled, "created": created},
        )
