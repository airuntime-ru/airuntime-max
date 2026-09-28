"""provision_postgres / provision_mysql / provision_redis / provision_mongodb /
provision_rabbitmq / provision_custom_service - one shared implementation
(ProvisionServiceSkill) parameterized by `kind`, registered under six distinct ids per spec
section 10. Wraps the existing, already-idempotent `project_services.ensure_service_request`
(DB-only; the actual sidecar container is created later, at deploy time, by the worker - see
that module's own docstring) rather than reimplementing service provisioning.
"""

from __future__ import annotations

from src.services.orchestration.schemas import (
    ProjectType,
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill

_PRESET_KINDS = {"postgres", "mysql", "redis", "mongodb", "rabbitmq"}
_PROJECT_TYPES: list[ProjectType] = ["website", "telegram_bot", "mixed"]


class ProvisionServiceSkill(BaseSkill):
    def __init__(self, skill_id: str, *, kind: str, title: str) -> None:
        self._kind = kind
        self.definition = SkillDefinition(
            id=skill_id,
            version="1.0",
            title=title,
            description=f"Provision a {kind} sidecar service for this project (DB-only reservation; the worker creates the container at next deploy).",
            supported_roles=[
                SpecialistRole.IMPLEMENTER,
                SpecialistRole.BUILD_FIXER,
                SpecialistRole.DEPLOY_FIXER,
            ],
            supported_project_types=_PROJECT_TYPES,
            input_schema={
                "reason": "string",
                "image": "string (required for custom kind)",
                "env": "dict[str, str] (optional)",
                "data_path": "string (optional)",
            },
            output_schema={"hostname": "string", "created": "bool"},
            required_tools=[],
            required_capabilities=[],
            risk_level=RiskLevel.MEDIUM,
            idempotent=True,
            retry_policy=RetryPolicy(max_attempts=2),
        )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.db.models.project import Project
        from src.services.project_services import ensure_service_request

        project = context.db.get(Project, context.project_id)
        if project is None:
            return SkillResult(status="failed", summary=f"project {context.project_id} not found")

        image = context.arguments.get("image")
        if self._kind not in _PRESET_KINDS and not image:
            return SkillResult(
                status="failed", summary=f"kind={self._kind!r} requires an explicit image"
            )

        try:
            service, created = ensure_service_request(
                context.db,
                project,
                self._kind,
                context.arguments.get("reason", ""),
                image=image,
                env=context.arguments.get("env"),
                data_path=context.arguments.get("data_path"),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed SkillResult, not a crash
            return SkillResult(status="failed", summary=f"provisioning failed: {exc}")

        return SkillResult(
            status="completed",
            summary=f"{'Reserved' if created else 'Already reserved'} {self._kind} service '{service.container_name}'",
            output={
                "hostname": service.container_name,
                "created": created,
                "service_id": str(service.id),
            },
        )


def build_provisioning_skills() -> list[ProvisionServiceSkill]:
    return [
        ProvisionServiceSkill("provision_postgres", kind="postgres", title="Provision PostgreSQL"),
        ProvisionServiceSkill("provision_mysql", kind="mysql", title="Provision MySQL"),
        ProvisionServiceSkill("provision_redis", kind="redis", title="Provision Redis"),
        ProvisionServiceSkill("provision_mongodb", kind="mongodb", title="Provision MongoDB"),
        ProvisionServiceSkill("provision_rabbitmq", kind="rabbitmq", title="Provision RabbitMQ"),
        ProvisionServiceSkill(
            "provision_custom_service", kind="custom", title="Provision a custom service"
        ),
    ]
