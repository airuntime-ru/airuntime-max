"""Tests for services/orchestration/executors.py:
- ScopedWorkspaceTools: the PREVENTIVE half of scope enforcement (tool/path allowlists rejected
  before they reach the real WorkspaceTools / disk).
- _BaseAgentTurnExecutor._drive: server-side normalization of a raw agent event stream into a
  TaskResult, independent of which session type produced the events.
- DeterministicExecutor / SkillExecutor / McpExecutor: capability routing + status mapping.
"""

from __future__ import annotations

import uuid

import pytest

from src.services.agent.events import AgentDone, TextDelta, ToolCallRequested, ToolCallResult
from src.services.agent.tools import ToolExecutionResult
from src.services.orchestration.executors import (
    DeterministicExecutor,
    McpExecutor,
    ScopedWorkspaceTools,
    SkillExecutor,
    TaskContext,
    _BaseAgentTurnExecutor,
)
from src.services.orchestration.schemas import (
    CapabilityResult,
    ProjectStateSummary,
    SpecialistRole,
    TaskBudget,
    TaskContract,
)


def _contract(**overrides) -> TaskContract:
    defaults = dict(
        task_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        role=SpecialistRole.IMPLEMENTER,
        project_goal="goal",
        user_value="value",
        task_goal="task goal",
        reason="reason",
        current_state=ProjectStateSummary(project_type="website", project_name="p"),
        allowed_paths=["public"],
        forbidden_paths=[],
        allowed_tools=["write_file", "read_file"],
        budget=TaskBudget(),
    )
    defaults.update(overrides)
    return TaskContract(**defaults)


class _FakeInnerWorkspace:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.root = "/tmp/fake-root"
        self.project_id = "proj-1"
        self.requested_secrets: list[tuple[str, str]] = []
        self.requested_services: list = []
        self.build_succeeded = None
        self.last_build_result = None

    def call(self, name: str, arguments: dict) -> ToolExecutionResult:
        self.calls.append((name, arguments))
        return ToolExecutionResult(ok=True, summary=f"did {name}")


class TestScopedWorkspaceTools:
    def test_rejects_tool_not_in_allowed_tools(self) -> None:
        inner = _FakeInnerWorkspace()
        contract = _contract(allowed_tools=["read_file"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("write_file", {"path": "public/index.html", "content": "x"})

        assert result.ok is False
        assert "write_file" in result.summary
        assert inner.calls == []

    def test_rejects_write_outside_allowed_paths(self) -> None:
        inner = _FakeInnerWorkspace()
        contract = _contract(allowed_tools=["write_file"], allowed_paths=["public"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("write_file", {"path": "secrets/keys.env", "content": "x"})

        assert result.ok is False
        assert "вне разрешённой области" in result.summary
        assert inner.calls == []

    def test_allows_write_inside_allowed_paths(self) -> None:
        inner = _FakeInnerWorkspace()
        contract = _contract(allowed_tools=["write_file"], allowed_paths=["public"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("write_file", {"path": "public/index.html", "content": "x"})

        assert result.ok is True
        assert inner.calls == [("write_file", {"path": "public/index.html", "content": "x"})]

    def test_forbidden_paths_wins_even_inside_allowed_paths(self) -> None:
        inner = _FakeInnerWorkspace()
        contract = _contract(
            allowed_tools=["write_file"],
            allowed_paths=["*"],
            forbidden_paths=["public/index.html"],
        )
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("write_file", {"path": "public/index.html", "content": "x"})

        assert result.ok is False
        assert inner.calls == []

    def test_wildcard_forbidden_paths_blocks_every_write(self) -> None:
        inner = _FakeInnerWorkspace()
        contract = _contract(allowed_tools=["write_file"], allowed_paths=[], forbidden_paths=["*"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("write_file", {"path": "anything.txt", "content": "x"})

        assert result.ok is False
        assert inner.calls == []

    def test_non_path_tool_bypasses_path_check(self) -> None:
        inner = _FakeInnerWorkspace()
        contract = _contract(allowed_tools=["list_files"], allowed_paths=["public"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("list_files", {"path": "."})

        assert result.ok is True
        assert inner.calls == [("list_files", {"path": "."})]

    def test_passthrough_properties(self) -> None:
        inner = _FakeInnerWorkspace()
        inner.requested_secrets.append(("KEY", "why"))
        contract = _contract()
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        assert scoped.root == inner.root
        assert scoped.project_id == inner.project_id
        assert scoped.requested_secrets == [("KEY", "why")]
        assert scoped.requested_services == inner.requested_services
        assert scoped.build_succeeded is None


async def _events(*items):
    for item in items:
        yield item


class TestDriveNormalization:
    @pytest.mark.asyncio
    async def test_stop_reason_maps_to_completed_with_joined_text(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(
            _events(
                TextDelta(text="Hello "),
                TextDelta(text="world"),
                AgentDone(reason="stop", usage={"total_tokens": 42}),
            )
        )
        assert result.task_result.status == "completed"
        assert result.task_result.summary == "Hello world"
        assert result.usage == {"total_tokens": 42}
        assert result.error is None

    @pytest.mark.asyncio
    async def test_max_iterations_maps_to_partial(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(_events(AgentDone(reason="max_iterations")))
        assert result.task_result.status == "partial"

    @pytest.mark.asyncio
    async def test_error_reason_maps_to_failed_with_error_propagated(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(_events(AgentDone(reason="error", error="boom")))
        assert result.task_result.status == "failed"
        assert result.task_result.unresolved == ["boom"]
        assert result.error == "boom"

    @pytest.mark.asyncio
    async def test_cancelled_reason_maps_to_waiting_for_user_not_failed(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(
            _events(AgentDone(reason="cancelled", error="Cancelled by user"))
        )
        assert result.task_result.status == "waiting_for_user"
        # Only "failed" status surfaces AgentExecutionResult.error - cancellation is not a
        # failure the failure-policy engine should treat as retryable.
        assert result.error is None

    @pytest.mark.asyncio
    async def test_missing_agent_done_is_reported_as_failed(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(_events(TextDelta(text="partial output")))
        assert result.task_result.status == "failed"
        assert result.error == "no AgentDone received"
        assert "agent stream ended without AgentDone" in result.task_result.unresolved

    @pytest.mark.asyncio
    async def test_empty_text_falls_back_to_placeholder_summary(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(_events(AgentDone(reason="stop")))
        assert result.task_result.summary == "(агент не оставил текстового отчёта)"

    @pytest.mark.asyncio
    async def test_tool_results_recorded_as_claimed_changed_files_and_check(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(
            _events(
                ToolCallRequested(
                    call_id="1", name="write_file", arguments={"path": "public/index.html"}
                ),
                ToolCallResult(
                    call_id="1", name="write_file", ok=True, summary="wrote public/index.html"
                ),
                AgentDone(reason="stop"),
            )
        )
        assert result.task_result.claimed_changed_files == ["wrote public/index.html"]
        assert result.task_result.checks == [
            type(result.task_result.checks[0])(name="tool_calls_ok", passed=True)
        ]

    @pytest.mark.asyncio
    async def test_a_failed_tool_call_flips_tool_calls_ok_to_false(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(
            _events(
                ToolCallResult(call_id="1", name="build_project", ok=False, summary="build failed"),
                AgentDone(reason="stop"),
            )
        )
        check = result.task_result.checks[0]
        assert check.name == "tool_calls_ok"
        assert check.passed is False

    @pytest.mark.asyncio
    async def test_workspace_requested_secrets_and_services_surface_on_task_result(self) -> None:
        # Regression test: request_secret/request_service tool calls during a turn record
        # themselves on the ScopedWorkspaceTools instance as a side effect - _drive() must read
        # that back onto TaskResult, or engine.py has no way to ever see them (WorkspaceTools
        # itself is never exposed to the caller once the turn ends).
        from src.services.agent.tools import ServiceRequest

        inner = _FakeInnerWorkspace()
        inner.requested_secrets.append(("STRIPE_API_KEY", "нужен для оплаты"))
        inner.requested_services.append(ServiceRequest(kind="postgres", reason="нужна БД"))
        scoped = ScopedWorkspaceTools(inner, contract=_contract())

        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(_events(AgentDone(reason="stop")), workspace=scoped)

        assert result.task_result.requested_secrets == ["STRIPE_API_KEY"]
        assert result.task_result.requested_services == ["postgres"]

    @pytest.mark.asyncio
    async def test_build_result_lifted_from_workspace_last_build_result(self) -> None:
        # Regression: contracts declare a required "build" validation step for Implementer, but
        # coding executors used to drop the mid-turn build_project outcome - validation then
        # failed with "no build_result evidence is present" and burned the replan budget.
        inner = _FakeInnerWorkspace()
        inner.build_succeeded = True
        inner.last_build_result = {"ok": True, "log": "Build succeeded: tag", "log_tail": "ok"}
        scoped = ScopedWorkspaceTools(inner, contract=_contract())

        result = await _BaseAgentTurnExecutor()._drive(
            _events(AgentDone(reason="stop")), workspace=scoped
        )

        assert result.build_result == {
            "ok": True,
            "log": "Build succeeded: tag",
            "log_tail": "ok",
        }

    @pytest.mark.asyncio
    async def test_build_result_falls_back_to_build_succeeded_flag(self) -> None:
        inner = _FakeInnerWorkspace()
        inner.build_succeeded = False
        scoped = ScopedWorkspaceTools(inner, contract=_contract())

        result = await _BaseAgentTurnExecutor()._drive(
            _events(AgentDone(reason="stop")), workspace=scoped
        )

        assert result.build_result == {"ok": False}

    @pytest.mark.asyncio
    async def test_no_workspace_passed_leaves_requested_lists_empty(self) -> None:
        executor = _BaseAgentTurnExecutor()
        result = await executor._drive(_events(AgentDone(reason="stop")))
        assert result.task_result.requested_secrets == []
        assert result.task_result.requested_services == []
        assert result.build_result is None


class _FakeCapabilityProvider:
    def __init__(self, result: CapabilityResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, capability_id: str, arguments: dict, context) -> CapabilityResult:
        self.calls.append((capability_id, arguments))
        return self.result


def _task_context(**overrides) -> TaskContext:
    defaults = dict(
        workspace_root="/tmp/ws",
        project_id=str(uuid.uuid4()),
        provider_name="anthropic",
        model="m",
        api_key="k",
    )
    defaults.update(overrides)
    return TaskContext(**defaults)


class TestDeterministicExecutor:
    @pytest.mark.asyncio
    async def test_no_capability_id_fails_without_calling_provider(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="completed"))
        executor = DeterministicExecutor(provider)
        contract = _contract(allowed_capabilities=[])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "failed"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_delegates_to_first_allowed_capability(self) -> None:
        provider = _FakeCapabilityProvider(
            CapabilityResult(status="completed", output={"ok": True})
        )
        executor = DeterministicExecutor(provider)
        contract = _contract(allowed_capabilities=["platform.build_check", "platform.secret_scan"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert provider.calls[0][0] == "platform.build_check"
        assert result.task_result.status == "completed"
        assert result.build_result == {"ok": True}

    @pytest.mark.asyncio
    async def test_build_result_only_populated_for_build_check_capability(self) -> None:
        provider = _FakeCapabilityProvider(
            CapabilityResult(status="completed", output={"diff_stat": "x"})
        )
        executor = DeterministicExecutor(provider)
        contract = _contract(allowed_capabilities=["platform.git_diff_stat"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.build_result is None

    @pytest.mark.asyncio
    async def test_failed_capability_status_maps_to_failed_task_result(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="failed", error="nope"))
        executor = DeterministicExecutor(provider)
        contract = _contract(allowed_capabilities=["platform.build_check"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "failed"
        assert result.error == "nope"


class TestSkillExecutor:
    @pytest.mark.asyncio
    async def test_no_skill_id_fails_without_calling_provider(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="completed"))
        executor = SkillExecutor(provider)
        contract = _contract(allowed_skills=[])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "failed"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_delegates_with_skill_prefix_and_task_context_arguments(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="completed", output={}))
        executor = SkillExecutor(provider)
        contract = _contract(allowed_skills=["provisioning"])
        context = _task_context(provider_name="openai", model="gpt-5.4-mini", api_key="k123")

        await executor.execute(contract, context, cancellation=None)

        capability_id, arguments = provider.calls[0]
        assert capability_id == "skill:provisioning"
        assert arguments["provider_name"] == "openai"
        assert arguments["model"] == "gpt-5.4-mini"
        assert arguments["api_key"] == "k123"
        assert contract.task_goal in arguments["brief_text"]
        assert contract.project_goal in arguments["brief_text"]

    @pytest.mark.asyncio
    async def test_non_completed_status_maps_to_partial_not_failed(self) -> None:
        # Skills degrade gracefully (spec: prefer a partial result over a hard failure) - only
        # DeterministicExecutor/McpExecutor treat "not completed" as an outright failure.
        provider = _FakeCapabilityProvider(CapabilityResult(status="failed", error="degraded"))
        executor = SkillExecutor(provider)
        contract = _contract(allowed_skills=["repair"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "partial"
        assert result.error == "degraded"

    @pytest.mark.asyncio
    async def test_visual_review_findings_are_preserved_for_replanning(self) -> None:
        provider = _FakeCapabilityProvider(
            CapabilityResult(
                status="failed",
                error="review verdict=revise",
                output={
                    "review": {
                        "verdict": "revise",
                        "critical_issues": [],
                        "major_issues": ["generic hero", "mobile CTA below fold"],
                        "recommended_fixes": ["rework composition"],
                    }
                },
            )
        )
        executor = SkillExecutor(provider)
        contract = _contract(allowed_skills=["visual_preview_review"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "partial"
        assert "generic hero" in result.task_result.summary
        assert "mobile CTA below fold" in result.task_result.unresolved

    @pytest.mark.asyncio
    async def test_visual_review_todos_are_handed_to_implementer_first(self) -> None:
        provider = _FakeCapabilityProvider(
            CapabilityResult(
                status="failed",
                error="review verdict=revise",
                output={
                    "review": {
                        "verdict": "revise",
                        "todos": ["public/styles.css: убери overflow на 390px"],
                        "critical_issues": [],
                        "major_issues": ["generic hero"],
                        "recommended_fixes": ["rework composition"],
                    }
                },
            )
        )
        executor = SkillExecutor(provider)
        contract = _contract(allowed_skills=["visual_preview_review"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "partial"
        assert result.task_result.unresolved[0] == "public/styles.css: убери overflow на 390px"

    @pytest.mark.asyncio
    async def test_runtime_health_result_only_for_that_specific_skill(self) -> None:
        provider = _FakeCapabilityProvider(
            CapabilityResult(status="completed", output={"restart_count": 0})
        )
        executor = SkillExecutor(provider)
        contract = _contract(allowed_skills=["runtime_health_check"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.runtime_health_result == {"restart_count": 0}


class TestMcpExecutor:
    @pytest.mark.asyncio
    async def test_no_mcp_capability_fails_without_calling_provider(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="completed"))
        executor = McpExecutor(provider)
        contract = _contract(allowed_capabilities=["platform.build_check"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "failed"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_picks_first_mcp_prefixed_capability(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="completed"))
        executor = McpExecutor(provider)
        contract = _contract(
            allowed_capabilities=["platform.build_check", "mcp:github:list_issues"]
        )

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert provider.calls[0][0] == "mcp:github:list_issues"
        assert result.task_result.status == "completed"

    @pytest.mark.asyncio
    async def test_failed_status_propagates_error(self) -> None:
        provider = _FakeCapabilityProvider(CapabilityResult(status="failed", error="mcp down"))
        executor = McpExecutor(provider)
        contract = _contract(allowed_capabilities=["mcp:github:list_issues"])

        result = await executor.execute(contract, _task_context(), cancellation=None)

        assert result.task_result.status == "failed"
        assert result.error == "mcp down"
