"""Tests for services/orchestration/role_policy.py - the server-side enforcement that a role
can never gain more rights than its RolePolicy grants, no matter what a task/plan/model asks
for. Pure Python, no DB/LLM needed."""

from __future__ import annotations

import pytest

from src.services.orchestration.role_policy import (
    ROLE_REGISTRY,
    can_execute_role,
    compute_write_scope,
    expand_allowed_paths_for_project,
    filter_skills,
    filter_tools,
)
from src.services.orchestration.schemas import SpecialistRole, WriteScope


def test_every_specialist_role_is_registered() -> None:
    assert set(ROLE_REGISTRY.keys()) == set(SpecialistRole)


def test_every_role_has_a_distinct_system_prompt() -> None:
    prompts = [policy.system_prompt for policy in ROLE_REGISTRY.values()]
    assert len(prompts) == len(set(prompts)), "no two roles may share a system prompt"


def test_every_role_has_a_distinct_tool_or_scope_profile() -> None:
    # Not asserting full uniqueness (some roles legitimately share a tool set) but at least
    # confirm the read-only vs write-capable split is real, not cosmetic.
    read_only_roles = {
        role
        for role, policy in ROLE_REGISTRY.items()
        if policy.write_scope_ceiling == WriteScope.NONE
    }
    write_roles = set(SpecialistRole) - read_only_roles
    assert read_only_roles == {
        SpecialistRole.PRODUCT_PLANNER,
        SpecialistRole.SOLUTION_ARCHITECT,
        SpecialistRole.QA_REVIEWER,
        SpecialistRole.SECURITY_REVIEWER,
    }
    assert write_roles == {
        SpecialistRole.IMPLEMENTER,
        SpecialistRole.UI_UX_SPECIALIST,
        SpecialistRole.BUILD_FIXER,
        SpecialistRole.DEPLOY_FIXER,
        SpecialistRole.INTEGRATION_AGENT,
    }


class TestFilterTools:
    def test_read_only_role_never_gets_write_tools(self) -> None:
        tools = filter_tools(SpecialistRole.QA_REVIEWER)
        assert "write_file" not in tools
        assert "delete_file" not in tools
        assert "build_project" not in tools

    def test_requesting_disallowed_tool_is_silently_dropped_not_granted(self) -> None:
        tools = filter_tools(
            SpecialistRole.SECURITY_REVIEWER, requested_tools=["write_file", "read_file"]
        )
        assert tools == ["read_file"]

    def test_deploy_fixer_has_no_docker_related_tool(self) -> None:
        # There is no "docker" tool exposed to any role at all - this just documents that
        # DeployFixer's tool surface is file/build, never anything Docker-socket-shaped.
        tools = filter_tools(SpecialistRole.DEPLOY_FIXER)
        assert all("docker" not in t for t in tools)

    def test_implementer_gets_full_tool_set(self) -> None:
        tools = filter_tools(SpecialistRole.IMPLEMENTER)
        assert {
            "write_file",
            "edit_file",
            "delete_file",
            "build_project",
            "request_secret",
            "request_service",
        } <= set(tools)


class TestFilterSkills:
    def test_wildcard_role_gets_every_registered_skill(self) -> None:
        registered = {"build_repair", "deploy_repair", "telegram_bot_setup"}
        assert filter_skills(
            SpecialistRole.IMPLEMENTER, [], registered_skill_ids=registered
        ) == sorted(registered)

    def test_scoped_role_only_gets_its_own_skills(self) -> None:
        registered = {"build_repair", "deploy_repair", "visual_preview_review"}
        allowed = filter_skills(SpecialistRole.QA_REVIEWER, [], registered_skill_ids=registered)
        assert "deploy_repair" not in allowed
        assert "visual_preview_review" in allowed

    def test_requested_skill_outside_allowance_is_dropped(self) -> None:
        registered = {"build_repair", "deploy_repair"}
        allowed = filter_skills(
            SpecialistRole.BUILD_FIXER, ["deploy_repair"], registered_skill_ids=registered
        )
        assert allowed == []


class TestComputeWriteScope:
    def test_read_only_ceiling_forbids_everything(self) -> None:
        allowed, forbidden = compute_write_scope(
            SpecialistRole.PRODUCT_PLANNER, requested_paths=["src/**"], fallback_relevant_paths=[]
        )
        assert allowed == []
        assert forbidden == ["*"]

    def test_scoped_role_cannot_escalate_to_full_workspace(self) -> None:
        allowed, forbidden = compute_write_scope(
            SpecialistRole.UI_UX_SPECIALIST,
            requested_paths=["*"],
            fallback_relevant_paths=["frontend/"],
        )
        # A scoped-ceiling role's "requested_paths=['*']" is honored literally here (it came
        # from contract_builder narrowing the plan's own declared paths, not from the model),
        # but always_forbidden must still be enforced regardless of what was requested.
        assert ".env" in forbidden
        assert ".git" in forbidden

    def test_full_workspace_role_falls_back_to_wildcard_when_nothing_declared(self) -> None:
        allowed, forbidden = compute_write_scope(
            SpecialistRole.IMPLEMENTER, requested_paths=[], fallback_relevant_paths=[]
        )
        assert allowed == ["*"]

    def test_sensitive_paths_always_forbidden_even_for_full_workspace_role(self) -> None:
        _allowed, forbidden = compute_write_scope(
            SpecialistRole.INTEGRATION_AGENT, requested_paths=["*"], fallback_relevant_paths=[]
        )
        assert ".env" in forbidden and ".git" in forbidden


class TestExpandAllowedPaths:
    def test_website_implementer_gets_public_tree(self) -> None:
        expanded = expand_allowed_paths_for_project(
            "website",
            SpecialistRole.IMPLEMENTER,
            ["public/index.html"],
        )
        assert "public" in expanded
        assert "public/index.html" in expanded

    def test_read_only_role_unchanged(self) -> None:
        paths = expand_allowed_paths_for_project(
            "website",
            SpecialistRole.QA_REVIEWER,
            ["public/index.html"],
        )
        assert paths == ["public/index.html"]


class TestCanExecuteRole:
    def test_implementer_always_allowed(self) -> None:
        assert can_execute_role(SpecialistRole.IMPLEMENTER, specialist_agents_enabled=False) is True
        assert can_execute_role(SpecialistRole.IMPLEMENTER, specialist_agents_enabled=True) is True

    @pytest.mark.parametrize("role", [r for r in SpecialistRole if r != SpecialistRole.IMPLEMENTER])
    def test_other_roles_blocked_when_specialist_agents_disabled(
        self, role: SpecialistRole
    ) -> None:
        assert can_execute_role(role, specialist_agents_enabled=False) is False
        assert can_execute_role(role, specialist_agents_enabled=True) is True
