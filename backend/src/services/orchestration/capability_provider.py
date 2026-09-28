"""The three CapabilityProvider implementations (spec section 11):
  - PlatformToolCapabilityProvider: deterministic, no-LLM platform operations (build check,
    git diff, secret scan) - self-contained, no dependency on skills/ or mcp/.
  - SkillCapabilityProvider: wraps skills/base.py's SkillRegistry.
  - McpCapabilityProvider: wraps mcp/registry.py + mcp/client.py.

All three share the same `invoke(capability_id, arguments, context)` shape so executors.py's
DeterministicExecutor/SkillExecutor/McpExecutor can treat them uniformly - only
PlatformToolCapabilityProvider's capabilities are usable without an executor/LLM at all, the
other two still need role_policy.py's allowlist applied by the caller before invocation (this
module does not itself enforce role policy). Discovery ("what capabilities exist for this role")
goes through more direct, purpose-built paths instead - PLATFORM_CAPABILITY_IDS (a static set),
SkillRegistry.find_best_match, and mcp/registry.py's list_capabilities_for_role - not through a
generic per-provider listing method, since capability_router.py already knows which of the three
kinds it's checking at each routing step.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from src.core.config import settings
from src.services import project_git
from src.services.docker_control_queue import submit_control_job
from src.services.orchestration.evidence import scan_for_secret_leaks
from src.services.orchestration.mcp import registry as mcp_registry
from src.services.orchestration.repository import McpServerRepository
from src.services.orchestration.schemas import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityResult,
    RiskLevel,
)
from src.services.orchestration.skills.base import SkillContext, SkillRegistry

logger = logging.getLogger(__name__)


class CapabilityProvider(Protocol):
    async def invoke(
        self, capability_id: str, arguments: dict, context: CapabilityContext
    ) -> CapabilityResult: ...


# --------------------------------------------------------------------------------------------
# Platform (deterministic, no LLM)
# --------------------------------------------------------------------------------------------

PLATFORM_CAPABILITY_BUILD_CHECK = "platform.build_check"
PLATFORM_CAPABILITY_PREVIEW_CHECK = "platform.preview_check"
PLATFORM_CAPABILITY_GIT_DIFF_STAT = "platform.git_diff_stat"
PLATFORM_CAPABILITY_SECRET_SCAN = "platform.secret_scan"

_PLATFORM_DEFINITIONS: dict[str, CapabilityDefinition] = {
    PLATFORM_CAPABILITY_BUILD_CHECK: CapabilityDefinition(
        id=PLATFORM_CAPABILITY_BUILD_CHECK,
        title="Build check",
        description="Run a Docker build via the worker RPC without deploying (agent/tools.py's build_project, invoked directly).",
        input_schema={},
        output_schema={"ok": "bool", "log_tail": "string"},
        risk_level=RiskLevel.LOW,
        side_effects=False,
        source="platform",
    ),
    PLATFORM_CAPABILITY_PREVIEW_CHECK: CapabilityDefinition(
        id=PLATFORM_CAPABILITY_PREVIEW_CHECK,
        title="Preview check",
        description="Boot the generated image in an isolated preview network and inspect it with a real browser.",
        input_schema={"paths": "list[string]"},
        output_schema={
            "status": "passed|issues_found|failed",
            "pages": "list[object]",
            "fatal_errors": "list[string]",
            "warnings": "list[string]",
        },
        risk_level=RiskLevel.LOW,
        side_effects=False,
        source="platform",
    ),
    PLATFORM_CAPABILITY_GIT_DIFF_STAT: CapabilityDefinition(
        id=PLATFORM_CAPABILITY_GIT_DIFF_STAT,
        title="Git diff stat",
        description="Textual diff --stat between two commits in the project's workspace.",
        input_schema={"base_sha": "string|null", "head": "string"},
        output_schema={"diff_stat": "string"},
        risk_level=RiskLevel.LOW,
        side_effects=False,
        source="platform",
    ),
    PLATFORM_CAPABILITY_SECRET_SCAN: CapabilityDefinition(
        id=PLATFORM_CAPABILITY_SECRET_SCAN,
        title="Secret leak scan",
        description="Regex scan of a diff for credential-shaped added lines.",
        input_schema={"diff_text": "string"},
        output_schema={"findings": "list[string]"},
        risk_level=RiskLevel.LOW,
        side_effects=False,
        source="platform",
    ),
}


PLATFORM_CAPABILITY_IDS: frozenset[str] = frozenset(_PLATFORM_DEFINITIONS.keys())


class PlatformToolCapabilityProvider:
    """No constructor dependencies beyond what's already global (docker_control_queue,
    project_git) - matches every other caller of these in this codebase."""

    async def invoke(
        self, capability_id: str, arguments: dict, context: CapabilityContext
    ) -> CapabilityResult:
        if capability_id == PLATFORM_CAPABILITY_BUILD_CHECK:
            return await self._build_check(context)
        if capability_id == PLATFORM_CAPABILITY_PREVIEW_CHECK:
            return await self._preview_check(context, arguments)
        if capability_id == PLATFORM_CAPABILITY_GIT_DIFF_STAT:
            return self._git_diff_stat(context, arguments)
        if capability_id == PLATFORM_CAPABILITY_SECRET_SCAN:
            return self._secret_scan(arguments)
        return CapabilityResult(
            status="failed", error=f"unknown platform capability {capability_id!r}"
        )

    async def _build_check(self, context: CapabilityContext) -> CapabilityResult:
        import asyncio

        result = await asyncio.to_thread(
            submit_control_job,
            action="build_check",
            project_id=str(context.project_id),
            timeout_seconds=180,
        )
        if result is None:
            return CapabilityResult(
                status="failed", error="build_check RPC timed out or worker unreachable"
            )
        return CapabilityResult(status="completed" if result.get("ok") else "failed", output=result)

    async def _preview_check(self, context: CapabilityContext, arguments: dict) -> CapabilityResult:
        import asyncio

        paths = arguments.get("paths")
        if not isinstance(paths, list):
            paths = ["/"]
        result = await asyncio.to_thread(
            submit_control_job,
            action="preview",
            project_id=str(context.project_id),
            timeout_seconds=max(30, int(settings.preview_timeout_seconds) + 30),
            extra={"paths": paths},
        )
        if result is None:
            error = "preview RPC timed out or worker unreachable"
            return CapabilityResult(
                status="failed",
                output={
                    "status": "failed",
                    "pages": [],
                    "fatal_errors": [error],
                    "warnings": [],
                },
                error=error,
            )
        preview_status = str(result.get("status") or "failed")
        return CapabilityResult(
            status="completed" if preview_status == "passed" else "failed",
            output=result,
            error=None
            if preview_status == "passed"
            else "; ".join(str(item) for item in (result.get("fatal_errors") or []))
            or f"preview status={preview_status}",
        )

    def _git_diff_stat(self, context: CapabilityContext, arguments: dict) -> CapabilityResult:
        workspace_root = Path(arguments.get("workspace_root", ""))
        if not workspace_root.exists():
            return CapabilityResult(status="failed", error=f"workspace not found: {workspace_root}")
        stat = project_git.diff_stat(
            workspace_root, base_sha=arguments.get("base_sha"), head=arguments.get("head", "HEAD")
        )
        return CapabilityResult(status="completed", output={"diff_stat": stat})

    def _secret_scan(self, arguments: dict) -> CapabilityResult:
        findings = scan_for_secret_leaks(arguments.get("diff_text", ""))
        return CapabilityResult(status="completed", output={"findings": findings})


# --------------------------------------------------------------------------------------------
# Skill
# --------------------------------------------------------------------------------------------


class SkillCapabilityProvider:
    def __init__(self, skill_registry: SkillRegistry, *, db: Session) -> None:
        self._registry = skill_registry
        self._db = db

    async def invoke(
        self, capability_id: str, arguments: dict, context: CapabilityContext
    ) -> CapabilityResult:
        skill_id = capability_id.removeprefix("skill:")
        skill = self._registry.get(skill_id)
        if skill is None:
            return CapabilityResult(status="failed", error=f"unknown skill {skill_id!r}")
        skill_context = SkillContext(
            project_id=str(context.project_id),
            run_id=str(context.run_id),
            task_id=str(context.task_id),
            workspace_root=str(arguments.get("workspace_root", "")),
            project_type=arguments.get("project_type", "website"),
            arguments=arguments,
            db=self._db,
        )
        started = time.monotonic()
        try:
            result = await skill.execute(skill_context)
        except Exception as exc:  # noqa: BLE001 - a skill crashing must not crash the router
            logger.exception("skill %s raised during execute()", skill_id)
            return CapabilityResult(status="failed", error=str(exc))
        duration = time.monotonic() - started
        output = dict(result.output)
        output.setdefault("duration_seconds", duration)
        return CapabilityResult(
            status="completed" if result.status == "completed" else "failed",
            output=output,
            error=None if result.status == "completed" else result.summary,
        )


# --------------------------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------------------------


class McpCapabilityProvider:
    def __init__(self, server_repository: McpServerRepository) -> None:
        self._repo = server_repository

    async def invoke(
        self, capability_id: str, arguments: dict, context: CapabilityContext
    ) -> CapabilityResult:
        # capability_id shape: "mcp:<server_name>:<tool_name>"
        try:
            _prefix, server_name, tool_name = capability_id.split(":", 2)
        except ValueError:
            return CapabilityResult(
                status="failed", error=f"malformed MCP capability id {capability_id!r}"
            )
        server = self._repo.get_by_name(server_name)
        if server is None or not server.enabled:
            return CapabilityResult(
                status="failed", error=f"MCP server {server_name!r} is not enabled"
            )
        if context.role not in mcp_registry.allowed_roles(server):
            return CapabilityResult(
                status="failed", error=f"role {context.role} is not allowed to use {server_name!r}"
            )
        client = mcp_registry.get_client(server)
        result = await client.call_tool(tool_name, arguments)
        return CapabilityResult(
            status="completed" if result.ok else "failed",
            output={"content": result.content, "truncated": result.truncated},
            error=result.error,
        )
