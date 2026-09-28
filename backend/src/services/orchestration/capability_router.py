"""Routes one PlannedTask to an ExecutionKind, per spec section 12's priority order:

    1. deterministic platform operation (PlannedTask.required_capabilities names one that
       actually exists in PlatformToolCapabilityProvider - declarative, never guessed from
       free-text goal wording)
    2. verified skill match (SkillRegistry.find_best_match, gated by
       role_policy.py's per-role skill allowlist)
    3. allowed MCP capability match (gated by role_policy + the server's
       own admin allowlist/role allowlist)
    4. specialist agent (codex_task/specialist_agent, per role_policy's default_execution_kind),
       collapsed to a plain Implementer task if the calling role isn't permitted to execute
    5. user_input (only reachable today via a task explicitly planned with role requiring
       information the contract can't supply - see contract_builder's completeness gate,
       which is really where this is caught upstream)
    6. controlled failure (nothing matched and the role itself can't execute - should not
       normally happen since step 4 always has a fallback, but the caller must still handle it)

"LLM не является единственным router": the planner's execution_preference/suggested_skills/
required_capabilities are hints this module verifies against real registries - a suggestion for
a skill that isn't registered, or a capability that isn't allowlisted, is simply not taken.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.services.orchestration.capability_provider import PLATFORM_CAPABILITY_IDS
from src.services.orchestration.role_policy import can_execute_role, filter_skills, get_role_policy
from src.services.orchestration.schemas import (
    ExecutionKind,
    PlannedTask,
    ProjectType,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillRegistry


@dataclass
class RoutingDecision:
    execution_kind: ExecutionKind
    effective_role: SpecialistRole
    skill_id: str | None = None
    capability_id: str | None = None
    reason: str = ""


class CapabilityRouter:
    def __init__(self, *, skill_registry: SkillRegistry) -> None:
        self._skills = skill_registry

    async def route(
        self,
        *,
        planned_task: PlannedTask,
        project_type: ProjectType,
        specialist_agents_enabled: bool,
        skills_enabled: bool,
        mcp_enabled: bool,
        mcp_capability_ids: frozenset[str] = frozenset(),
    ) -> RoutingDecision:
        role = planned_task.role

        # 1. deterministic platform operation - only via an explicit, verified id.
        platform_match = next(
            (
                cap_id
                for cap_id in planned_task.required_capabilities
                if cap_id in PLATFORM_CAPABILITY_IDS
            ),
            None,
        )
        if platform_match is not None:
            return RoutingDecision(
                execution_kind=ExecutionKind.DETERMINISTIC_VALIDATION,
                effective_role=role,
                capability_id=platform_match,
                reason=f"required_capabilities named a verified platform operation ({platform_match})",
            )

        # 2. skill match.
        if skills_enabled:
            registered_ids = self._skills.all_ids()
            allowed_for_role = set(
                filter_skills(
                    role, planned_task.suggested_skills, registered_skill_ids=registered_ids
                )
            )
            if allowed_for_role or not planned_task.suggested_skills:
                match = await self._skills.find_best_match(
                    planned_task, role=role, project_type=project_type
                )
                if match is not None:
                    skill, outcome = match
                    if not planned_task.suggested_skills or skill.definition.id in allowed_for_role:
                        return RoutingDecision(
                            execution_kind=ExecutionKind.SKILL,
                            effective_role=role,
                            skill_id=skill.definition.id,
                            reason=f"skill {skill.definition.id} matched (confidence={outcome.confidence:.2f})",
                        )

        # 3. MCP capability match - required_capabilities may name "mcp:<server>:<tool>" ids
        # directly; only accept ones the caller already confirmed are allowlisted+discovered
        # (mcp_capability_ids, computed by the caller via mcp/registry.py for this role).
        if mcp_enabled:
            mcp_match = next(
                (
                    cap_id
                    for cap_id in planned_task.required_capabilities
                    if cap_id in mcp_capability_ids
                ),
                None,
            )
            if mcp_match is not None:
                return RoutingDecision(
                    execution_kind=ExecutionKind.MCP,
                    effective_role=role,
                    capability_id=mcp_match,
                    reason=f"required_capabilities named an allowlisted MCP capability ({mcp_match})",
                )

        # 4. specialist agent, collapsing to Implementer if specialist agents are disabled or
        # this particular role isn't permitted to run at all right now.
        effective_role = (
            role
            if can_execute_role(role, specialist_agents_enabled=specialist_agents_enabled)
            else SpecialistRole.IMPLEMENTER
        )
        policy = get_role_policy(effective_role)
        reason = (
            "specialist agent"
            if effective_role == role
            else f"specialist agents disabled - collapsed {role.value} to implementer"
        )
        return RoutingDecision(
            execution_kind=policy.default_execution_kind,
            effective_role=effective_role,
            reason=reason,
        )
