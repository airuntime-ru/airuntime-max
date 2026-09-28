"""telegram_bot_setup - reserves the TELEGRAM_BOT_TOKEN secret slot (the one secret key that
actually gets delivered into the deployed container today - see artifacts.py's
_telegram_token(), and the platform-wide fix in this same change that makes every OTHER filled
secret get delivered too, not just this one). Wraps secrets.ensure_secret_placeholder - never
touches a token value.
"""

from __future__ import annotations

from src.services.orchestration.schemas import (
    PlannedTask,
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillMatch,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill

TELEGRAM_BOT_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"


class TelegramBotSetupSkill(BaseSkill):
    definition = SkillDefinition(
        id="telegram_bot_setup",
        version="1.0",
        title="Telegram bot setup",
        description="Reserve the TELEGRAM_BOT_TOKEN secret slot for a telegram_bot/mixed project.",
        supported_roles=[SpecialistRole.IMPLEMENTER],
        supported_project_types=["telegram_bot", "mixed"],
        input_schema={"reason": "string"},
        output_schema={"key": "string", "already_filled": "bool"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=2),
    )

    async def match(self, planned_task: PlannedTask, *, role: SpecialistRole) -> SkillMatch:
        suggested = self.definition.id in planned_task.suggested_skills
        goal_lower = planned_task.goal.lower()
        mentions_telegram = "telegram" in goal_lower or "телеграм" in goal_lower
        if not (suggested or mentions_telegram):
            return SkillMatch(
                matched=False, confidence=0.0, reason="not suggested and no telegram mention"
            )
        return SkillMatch(
            matched=True,
            confidence=0.9 if suggested else 0.6,
            reason="explicitly suggested" if suggested else "telegram mentioned in task goal",
        )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.db.models.project import Project
        from src.services.secrets import ensure_secret_placeholder

        project = context.db.get(Project, context.project_id)
        if project is None:
            return SkillResult(status="failed", summary=f"project {context.project_id} not found")

        secret, created = ensure_secret_placeholder(
            context.db,
            project,
            TELEGRAM_BOT_TOKEN_KEY,
            context.arguments.get("reason", "Токен Telegram-бота"),
        )
        already_filled = secret.encrypted_value is not None
        return SkillResult(
            status="completed",
            summary=(
                "Токен уже указан пользователем"
                if already_filled
                else "Слот для TELEGRAM_BOT_TOKEN зарезервирован - пользователь должен указать значение в настройках проекта"
            ),
            output={
                "key": TELEGRAM_BOT_TOKEN_KEY,
                "already_filled": already_filled,
                "created": created,
            },
        )
