"""Tests for capability_router.py + capability_provider.py's PlatformToolCapabilityProvider.
No live LLM/Docker: build_check is monkeypatched at its import site in capability_provider.py,
matching this repo's existing convention for docker_control_queue.submit_control_job."""

from __future__ import annotations

import uuid

import pytest

from src.services import project_git
from src.services.orchestration import capability_provider
from src.services.orchestration.capability_provider import (
    PLATFORM_CAPABILITY_BUILD_CHECK,
    PLATFORM_CAPABILITY_GIT_DIFF_STAT,
    PLATFORM_CAPABILITY_PREVIEW_CHECK,
    PLATFORM_CAPABILITY_SECRET_SCAN,
    PlatformToolCapabilityProvider,
)
from src.services.orchestration.capability_router import CapabilityRouter
from src.services.orchestration.schemas import (
    CapabilityContext,
    ExecutionKind,
    PlannedTask,
    SkillDefinition,
    SkillMatch,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillRegistry


def _ctx(role: SpecialistRole = SpecialistRole.IMPLEMENTER) -> CapabilityContext:
    return CapabilityContext(
        project_id=uuid.uuid4(), run_id=uuid.uuid4(), task_id=uuid.uuid4(), role=role
    )


def _task(**overrides) -> PlannedTask:  # noqa: ANN003
    base = dict(
        local_id="t",
        title="t",
        role=SpecialistRole.IMPLEMENTER,
        goal="do it",
        reason="because",
    )
    base.update(overrides)
    return PlannedTask(**base)


class FakeSkill:
    def __init__(self, skill_id: str, *, matches: bool = True, confidence: float = 0.9) -> None:
        self.definition = SkillDefinition(
            id=skill_id,
            version="1.0",
            title=skill_id,
            description="fake",
            supported_roles=list(SpecialistRole),
            supported_project_types=["website", "telegram_bot", "mixed"],
            input_schema={},
            output_schema={},
        )
        self._matches = matches
        self._confidence = confidence

    async def match(self, planned_task, *, role):  # noqa: ANN001
        return SkillMatch(matched=self._matches, confidence=self._confidence, reason="fake")

    async def plan(self, context):  # noqa: ANN001
        raise NotImplementedError

    async def execute(self, context):  # noqa: ANN001
        raise NotImplementedError

    async def validate(self, context, result):  # noqa: ANN001
        raise NotImplementedError

    async def compensate(self, context, result):  # noqa: ANN001
        raise NotImplementedError


class TestPlatformToolCapabilityProvider:
    def test_git_diff_stat_against_real_repo(self, tmp_path) -> None:
        project_git.init_repo_if_needed(tmp_path)
        (tmp_path / "a.txt").write_text("1", encoding="utf-8")
        sha1 = project_git.commit_snapshot(tmp_path, message="first")
        (tmp_path / "a.txt").write_text("2", encoding="utf-8")
        project_git.commit_snapshot(tmp_path, message="second")

        provider = PlatformToolCapabilityProvider()

        async def _run():
            return await provider.invoke(
                PLATFORM_CAPABILITY_GIT_DIFF_STAT,
                {"workspace_root": str(tmp_path), "base_sha": sha1},
                _ctx(),
            )

        import asyncio

        result = asyncio.run(_run())
        assert result.status == "completed"
        assert "a.txt" in result.output["diff_stat"]

    def test_secret_scan_flags_credential_pattern(self) -> None:
        provider = PlatformToolCapabilityProvider()

        async def _run():
            return await provider.invoke(
                PLATFORM_CAPABILITY_SECRET_SCAN,
                {"diff_text": "+password = 'hunter2hunter2'\n"},
                _ctx(),
            )

        import asyncio

        result = asyncio.run(_run())
        assert result.status == "completed"
        assert len(result.output["findings"]) == 1

    @pytest.mark.asyncio
    async def test_build_check_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            capability_provider,
            "submit_control_job",
            lambda **kwargs: {"ok": True, "log_tail": "built"},  # noqa: ANN003
        )
        provider = PlatformToolCapabilityProvider()
        result = await provider.invoke(PLATFORM_CAPABILITY_BUILD_CHECK, {}, _ctx())
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_build_check_rpc_timeout_reported_as_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(capability_provider, "submit_control_job", lambda **kwargs: None)  # noqa: ANN003
        provider = PlatformToolCapabilityProvider()
        result = await provider.invoke(PLATFORM_CAPABILITY_BUILD_CHECK, {}, _ctx())
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_preview_check_collects_real_browser_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []

        def _preview(**kwargs):  # noqa: ANN003, ANN202
            calls.append(kwargs)
            return {
                "status": "passed",
                "pages": [{"url": "http://preview/"}],
                "fatal_errors": [],
                "warnings": [],
            }

        monkeypatch.setattr(capability_provider, "submit_control_job", _preview)
        provider = PlatformToolCapabilityProvider()
        result = await provider.invoke(PLATFORM_CAPABILITY_PREVIEW_CHECK, {"paths": ["/"]}, _ctx())

        assert result.status == "completed"
        assert result.output["status"] == "passed"
        assert calls[0]["action"] == "preview"
        assert calls[0]["extra"] == {"paths": ["/"]}

    @pytest.mark.asyncio
    async def test_unknown_capability_id_fails_cleanly(self) -> None:
        provider = PlatformToolCapabilityProvider()
        result = await provider.invoke("platform.does_not_exist", {}, _ctx())
        assert result.status == "failed"


@pytest.mark.asyncio
class TestCapabilityRouter:
    async def test_required_capability_routes_to_deterministic_platform_op(self) -> None:
        router = CapabilityRouter(skill_registry=SkillRegistry())
        task = _task(required_capabilities=[PLATFORM_CAPABILITY_BUILD_CHECK])
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
        )
        assert decision.execution_kind == ExecutionKind.DETERMINISTIC_VALIDATION
        assert decision.capability_id == PLATFORM_CAPABILITY_BUILD_CHECK

    async def test_matching_skill_takes_priority_over_specialist_agent(self) -> None:
        registry = SkillRegistry()
        registry.register(FakeSkill("build_repair"))
        router = CapabilityRouter(skill_registry=registry)
        task = _task(role=SpecialistRole.BUILD_FIXER, suggested_skills=["build_repair"])
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
        )
        assert decision.execution_kind == ExecutionKind.SKILL
        assert decision.skill_id == "build_repair"

    async def test_skill_outside_role_allowlist_is_not_routed_even_if_matched(self) -> None:
        registry = SkillRegistry()
        registry.register(FakeSkill("deploy_repair"))  # not in QAReviewer's allowlist
        router = CapabilityRouter(skill_registry=registry)
        task = _task(role=SpecialistRole.QA_REVIEWER, suggested_skills=["deploy_repair"])
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
        )
        assert decision.execution_kind != ExecutionKind.SKILL

    async def test_skills_disabled_falls_through_to_specialist_agent(self) -> None:
        registry = SkillRegistry()
        registry.register(FakeSkill("build_repair"))
        router = CapabilityRouter(skill_registry=registry)
        task = _task(role=SpecialistRole.BUILD_FIXER, suggested_skills=["build_repair"])
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=False,
            mcp_enabled=True,
        )
        assert decision.execution_kind != ExecutionKind.SKILL

    async def test_mcp_capability_routed_when_allowlisted(self) -> None:
        router = CapabilityRouter(skill_registry=SkillRegistry())
        task = _task(required_capabilities=["mcp:search:query"])
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
            mcp_capability_ids=frozenset({"mcp:search:query"}),
        )
        assert decision.execution_kind == ExecutionKind.MCP
        assert decision.capability_id == "mcp:search:query"

    async def test_mcp_capability_not_allowlisted_falls_through(self) -> None:
        router = CapabilityRouter(skill_registry=SkillRegistry())
        task = _task(required_capabilities=["mcp:search:query"])
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
            mcp_capability_ids=frozenset(),
        )
        assert decision.execution_kind != ExecutionKind.MCP

    async def test_specialist_agents_disabled_collapses_role_to_implementer(self) -> None:
        router = CapabilityRouter(skill_registry=SkillRegistry())
        task = _task(role=SpecialistRole.SECURITY_REVIEWER)
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=False,
            skills_enabled=True,
            mcp_enabled=True,
        )
        assert decision.effective_role == SpecialistRole.IMPLEMENTER

    async def test_default_falls_through_to_role_default_execution_kind(self) -> None:
        router = CapabilityRouter(skill_registry=SkillRegistry())
        task = _task(role=SpecialistRole.IMPLEMENTER)
        decision = await router.route(
            planned_task=task,
            project_type="website",
            specialist_agents_enabled=True,
            skills_enabled=True,
            mcp_enabled=True,
        )
        assert decision.execution_kind == ExecutionKind.CODEX_TASK
        assert decision.effective_role == SpecialistRole.IMPLEMENTER
