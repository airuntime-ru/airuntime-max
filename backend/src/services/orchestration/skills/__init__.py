"""Populates the module-level `registry` (skills/base.py) with every concrete skill at import
time. capability_router.py / capability_provider.py import `registry` from here, never build
their own skill list - this is the single source of truth for "which skills exist".

All built-in skills named in spec section 10 plus the independent product-quality gate:
  provision_postgres, provision_mysql, provision_redis, provision_mongodb, provision_rabbitmq,
  provision_custom_service, telegram_bot_setup, database_migrations, visual_preview_review,
  accessibility_review, product_quality_review, build_repair, deploy_repair, secret_slot_setup,
  dependency_health_check, project_structure_review, runtime_health_check,
  release_checkpoint, rollback_release.
"""

from __future__ import annotations

from src.services.orchestration.skills.analysis import (
    DatabaseMigrationsSkill,
    DependencyHealthCheckSkill,
    ProjectStructureReviewSkill,
)
from src.services.orchestration.skills.base import SkillRegistry, registry
from src.services.orchestration.skills.preview_review import (
    AccessibilityReviewSkill,
    VisualPreviewReviewSkill,
)
from src.services.orchestration.skills.product_review import ProductQualityReviewSkill
from src.services.orchestration.skills.provisioning import build_provisioning_skills
from src.services.orchestration.skills.release import ReleaseCheckpointSkill, RollbackReleaseSkill
from src.services.orchestration.skills.repair import BuildRepairSkill, DeployRepairSkill
from src.services.orchestration.skills.runtime_health import RuntimeHealthCheckSkill
from src.services.orchestration.skills.secret_setup import SecretSlotSetupSkill
from src.services.orchestration.skills.telegram import TelegramBotSetupSkill


def build_default_registry() -> SkillRegistry:
    fresh = SkillRegistry()
    for skill in build_provisioning_skills():
        fresh.register(skill)
    for skill in (
        TelegramBotSetupSkill(),
        DatabaseMigrationsSkill(),
        VisualPreviewReviewSkill(),
        AccessibilityReviewSkill(),
        ProductQualityReviewSkill(),
        BuildRepairSkill(),
        DeployRepairSkill(),
        SecretSlotSetupSkill(),
        DependencyHealthCheckSkill(),
        ProjectStructureReviewSkill(),
        RuntimeHealthCheckSkill(),
        ReleaseCheckpointSkill(),
        RollbackReleaseSkill(),
    ):
        fresh.register(skill)
    return fresh


# Populate the shared module-level registry (skills/base.py's `registry`) at import time - the
# one capability_router.py/capability_provider.py actually use in production. Tests that want
# an isolated registry should call build_default_registry() themselves instead of relying on
# this shared singleton's exact contents.
for _skill in build_default_registry().all_skills():
    if _skill.definition.id not in registry.all_ids():
        registry.register(_skill)
