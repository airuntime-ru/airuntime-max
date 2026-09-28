"""AgentExecutor protocol + implementations (spec section 16): each takes a self-sufficient
TaskContract + local execution wiring (TaskContext, deliberately NOT part of the portable
contract) + a CancellationToken, and returns a normalized AgentExecutionResult.

The claimed TaskResult here is SERVER-CONSTRUCTED from what the executor actually observed
(the AgentDone outcome, accumulated text, tool calls made during the run) - not something the
underlying coding agent is asked to emit as its own structured JSON. This is deliberate: a
Codex/HTTP tool-calling agent is not schema-constrained the way pipeline_llm.complete_structured
calls are, and validation.py's evidence-based acceptance is authoritative regardless of what
TaskResult says, so hand-crafting a claim from real observations is honest and sufficient -
there is no missing rigor in skipping a model-authored TaskResult here.

ScopedWorkspaceTools is the PREVENTIVE half of scope enforcement (validation.py's
validate_scope is the DETECTIVE half, checked again afterward against real evidence regardless
of whether prevention worked): a write/edit/delete call outside contract.allowed_paths, or a
tool not in contract.allowed_tools at all, is rejected before it ever reaches disk - this is
what makes "role policies реально ограничивают tools" (Definition of Done) true at the tool
layer, not just at the after-the-fact validation layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from src.services.agent.codex_runtime import CodexAgentSession, resolve_codex_base_url
from src.services.agent.events import AgentDone, TextDelta, ToolCallRequested, ToolCallResult
from src.services.agent.loop import CodingAgentSession
from src.services.agent.tools import ToolExecutionResult, WorkspaceTools
from src.services.file_context import ImageAttachment
from src.services.orchestration.cancellation import CancellationToken
from src.services.orchestration.capability_provider import (
    McpCapabilityProvider,
    PlatformToolCapabilityProvider,
    SkillCapabilityProvider,
)
from src.services.orchestration.role_policy import get_role_policy
from src.services.orchestration.schemas import (
    CapabilityContext,
    ClaimedCheck,
    TaskContract,
    TaskResult,
)
from src.services.orchestration.validation import path_matches_any

logger = logging.getLogger(__name__)


@dataclass
class TaskContext:
    """Local execution wiring - deliberately separate from GlobalContext (orchestrator-only)
    and from TaskContract (must stay a plain, portable, prompt-able object)."""

    workspace_root: Path
    project_id: str
    provider_name: str
    model: str
    api_key: str
    history: list[dict[str, str]] = field(default_factory=list)
    db: Session | None = None
    # Plan-scoped Codex reasoning effort; None keeps the global default.
    reasoning_effort: str | None = None
    images: list[ImageAttachment] = field(default_factory=list)


@dataclass
class AgentExecutionResult:
    task_result: TaskResult | None
    raw_logs: str = ""
    usage: dict | None = None
    build_result: dict | None = None
    # Optional {"ok": bool, ...} from an executor that ran a REAL linter/test suite (a skill
    # shelling out through the worker, say). The server runs its own parse-level static check
    # regardless (evidence.run_static_checks) and merges the two - these fields can only add
    # findings, never clear the server's own (spec section 8: claims aren't evidence).
    lint_result: dict | None = None
    test_result: dict | None = None
    preview_result: dict | None = None
    runtime_health_result: dict | None = None
    error: str | None = None


class AgentExecutor(Protocol):
    async def execute(
        self, contract: TaskContract, context: TaskContext, cancellation: CancellationToken
    ) -> AgentExecutionResult: ...


class ScopedWorkspaceTools:
    """Wraps the real WorkspaceTools (agent/tools.py), enforcing a TaskContract's
    allowed_tools/allowed_paths/forbidden_paths before a call ever reaches the filesystem.
    Read visibility is scoped the same way for any role with a real write scope; a NONE write
    ceiling (read-only roles) leaves read_file unscoped on purpose - see call()."""

    _PATH_ARG_TOOLS = frozenset({"write_file", "edit_file", "delete_file", "read_file"})

    def __init__(self, inner: WorkspaceTools, *, contract: TaskContract) -> None:
        self._inner = inner
        self._allowed_tools = set(contract.allowed_tools)
        self._allowed_paths = contract.allowed_paths
        self._forbidden_paths = contract.forbidden_paths

    @property
    def root(self) -> Path:
        return self._inner.root

    @property
    def project_id(self) -> str | None:
        return self._inner.project_id

    @property
    def requested_secrets(self) -> list[tuple[str, str]]:
        return self._inner.requested_secrets

    @property
    def requested_services(self):  # noqa: ANN201 - matches WorkspaceTools' own (unannotated-here) type
        return self._inner.requested_services

    @property
    def build_succeeded(self) -> bool | None:
        return self._inner.build_succeeded

    @property
    def last_build_result(self) -> dict | None:
        return getattr(self._inner, "last_build_result", None)

    def _path_allowed(self, path: str) -> bool:
        if self._forbidden_paths == ["*"]:
            return False
        if path_matches_any(path, self._forbidden_paths):
            return False
        if self._allowed_paths and not path_matches_any(path, self._allowed_paths):
            return False
        return True

    def call(self, name: str, arguments: dict) -> ToolExecutionResult:
        if name not in self._allowed_tools:
            return ToolExecutionResult(
                ok=False, summary=f"Инструмент {name!r} недоступен для этой роли/задачи"
            )
        if name in self._PATH_ARG_TOOLS:
            # forbidden_paths == ["*"] is compute_write_scope's NONE-ceiling signal (no *write*
            # scope at all) - read-only roles like SolutionArchitect/QAReviewer still need full
            # read_file visibility to do their job, so that sentinel must not also blind reads.
            # Any role with a real write scope (SCOPED_PATHS/FULL_WORKSPACE) gets reads confined
            # the same way writes already are.
            if name != "read_file" or self._forbidden_paths != ["*"]:
                path = str(arguments.get("path", ""))
                if not self._path_allowed(path):
                    return ToolExecutionResult(
                        ok=False,
                        summary=(
                            f"Путь {path!r} вне разрешённой области (allowed_paths) для этой задачи"
                        ),
                    )
        return self._inner.call(name, arguments)


def _role_system_prompt(contract: TaskContract) -> str:
    return get_role_policy(contract.role).system_prompt


def _contract_to_user_message(contract: TaskContract) -> str:
    """The self-sufficient prompt body - every TaskContract field that matters for a small
    agent to act *without the full chat history or project history* is spelled out here
    explicitly (spec section 5's core requirement), not left implicit."""
    lines = [
        f"Цель проекта: {contract.project_goal}",
        f"Ценность для пользователя: {contract.user_value}",
        f"Цель этой конкретной задачи: {contract.task_goal}",
        f"Почему эта задача нужна: {contract.reason}",
        "",
        f"Проект: {contract.current_state.project_name} (тип: {contract.current_state.project_type})",
    ]
    if contract.current_state.recent_changes_summary:
        lines.append(f"Недавние изменения: {contract.current_state.recent_changes_summary}")
    if contract.current_state.secrets_inventory:
        lines.append(
            "Уже зарезервированные секреты (только имена, значения тебе недоступны): "
            + ", ".join(contract.current_state.secrets_inventory)
        )
    if contract.current_state.services_inventory:
        lines.append(
            "Подключённые сервисы: " + ", ".join(contract.current_state.services_inventory)
        )

    if contract.dependency_results:
        lines.append("\nРезультаты задач, от которых зависит эта (не переделывай их):")
        for dep in contract.dependency_results:
            lines.append(f"- [{dep.local_id}] {dep.title}: {dep.status} - {dep.summary}")

    if contract.relevant_files:
        lines.append("\nРелевантные файлы:")
        for file_ref in contract.relevant_files:
            lines.append(f"--- {file_ref.path} ({file_ref.reason}) ---")
            if file_ref.content is not None:
                lines.append(file_ref.content[:4000])

    if contract.relevant_context:
        lines.append("\nДополнительный контекст:")
        for item in contract.relevant_context:
            lines.append(f"[{item.kind}] {item.title}:\n{item.content[:2000]}")

    lines.append(
        "\nРазрешённые пути для изменений: "
        + (", ".join(contract.allowed_paths) or "(нет - роль read-only)")
    )
    if contract.forbidden_paths:
        lines.append("ЗАПРЕЩЕНО менять: " + ", ".join(contract.forbidden_paths))

    if contract.constraints:
        lines.append("\nОграничения:")
        lines.extend(f"- {c}" for c in contract.constraints)
    if contract.architectural_rules:
        lines.append("\nАрхитектурные правила:")
        lines.extend(f"- {r}" for r in contract.architectural_rules)

    if contract.acceptance_criteria:
        lines.append("\nКритерии приёмки (проверяются сервером по факту, не по твоим словам):")
        for criterion in contract.acceptance_criteria:
            lines.append(
                f"- [{criterion.id}] {criterion.description} (метод: {criterion.verification_method})"
            )

    lines.append(
        "\nПо завершении опиши текстом: что сделано, какие файлы менял, какие риски или "
        "нерешённые вопросы остались. Если цель ещё не закрыта — обязательно измени файлы: "
        "сервер отклонит пустой git diff. Пустой diff принимается только когда проект уже "
        "существует и сборка/preview проходят без блокирующих ошибок."
    )
    return "\n".join(lines)


def _extract_changed_file_hints(tool_events: list[ToolCallResult]) -> list[str]:
    hints: list[str] = []
    for event in tool_events:
        if event.name in ("write_file", "edit_file", "delete_file") and event.ok:
            # WorkspaceTools' own summary text embeds the path (tools.py) - best-effort scrape;
            # the AUTHORITATIVE list is evidence.py's real git diff, never this heuristic.
            hints.append(event.summary)
    return hints


class _BaseAgentTurnExecutor:
    """Shared drive-the-turn-loop-and-normalize-the-result logic for the two executors that
    wrap a real coding-agent session (Codex / HTTP tool-calling)."""

    async def _drive(
        self,
        events,  # AsyncIterator[TextDelta | ToolCallRequested | ToolCallResult | AgentDone]
        *,
        workspace: ScopedWorkspaceTools | None = None,
    ) -> AgentExecutionResult:
        text_parts: list[str] = []
        tool_results: list[ToolCallResult] = []
        done: AgentDone | None = None
        async for event in events:
            if isinstance(event, TextDelta):
                if event.text:
                    text_parts.append(event.text)
            elif isinstance(event, ToolCallResult):
                tool_results.append(event)
            elif isinstance(event, ToolCallRequested):
                continue
            elif isinstance(event, AgentDone):
                done = event

        summary = "".join(text_parts).strip() or "(агент не оставил текстового отчёта)"
        if done is None:
            return AgentExecutionResult(
                task_result=TaskResult(
                    status="failed",
                    summary=summary,
                    unresolved=["agent stream ended without AgentDone"],
                ),
                error="no AgentDone received",
            )

        status_map = {
            "stop": "completed",
            "max_iterations": "partial",
            "error": "failed",
            "cancelled": "waiting_for_user",
        }
        status = status_map.get(done.reason, "failed")
        checks = (
            [ClaimedCheck(name="tool_calls_ok", passed=all(t.ok for t in tool_results))]
            if tool_results
            else []
        )

        task_result = TaskResult(
            status=status,  # type: ignore[arg-type]
            summary=summary,
            claimed_changed_files=_extract_changed_file_hints(tool_results),
            checks=checks,
            # Populated from the SAME ScopedWorkspaceTools the turn actually ran against (a
            # request_secret/request_service tool call records itself there as a side effect) -
            # without this, a task that asked for a missing credential would silently lose that
            # signal the moment _drive() returns, and engine.py could never route it to
            # FailureClass.MISSING_SECRET/MISSING_SERVICE -> FailureDecision.WAIT_FOR_USER.
            requested_secrets=[key for key, _reason in workspace.requested_secrets]
            if workspace is not None
            else [],
            requested_services=[s.kind for s in workspace.requested_services]
            if workspace is not None
            else [],
            unresolved=[done.error] if done.error and status != "completed" else [],
        )
        # Prefer the full worker payload when the agent called build_project; fall back to the
        # bool flag alone so validation still sees a build_result rather than treating a mid-turn
        # build as "never attempted".
        build_result: dict | None = None
        if workspace is not None:
            if workspace.last_build_result is not None:
                build_result = workspace.last_build_result
            elif workspace.build_succeeded is not None:
                build_result = {"ok": bool(workspace.build_succeeded)}
        return AgentExecutionResult(
            task_result=task_result,
            usage=done.usage,
            build_result=build_result,
            error=done.error if status == "failed" else None,
        )


class CodexContainerExecutor(_BaseAgentTurnExecutor):
    """Real coding-agent turn via the Codex CLI in its own per-task Docker container (spec
    section 16's "Codex container executor"). `correlation_id=str(contract.task_id)` is what
    makes cancellation.cancel_codex_run able to find and stop this exact container."""

    async def execute(
        self, contract: TaskContract, context: TaskContext, cancellation: CancellationToken
    ) -> AgentExecutionResult:
        inner = WorkspaceTools(
            context.workspace_root, project_id=context.project_id, api_key=context.api_key
        )
        scoped = ScopedWorkspaceTools(inner, contract=contract)
        session = CodexAgentSession(
            model=context.model,
            workspace=scoped,  # duck-typed: CodexAgentSession only reads .root/.project_id and
            # calls _collect_requests(workspace), which only touches .root/.requested_secrets/
            # .requested_services - all present on ScopedWorkspaceTools.
            system_prompt=_role_system_prompt(contract),
            correlation_id=str(contract.task_id),
            reasoning_effort=context.reasoning_effort,
            api_key=context.api_key,
            openai_base_url=resolve_codex_base_url(
                provider_name=context.provider_name, api_key=context.api_key
            ),
        )
        events = session.run(
            history=context.history,
            user_message=_contract_to_user_message(contract),
            images=context.images or None,
            cancellation=cancellation,
        )
        return await self._drive(events, workspace=scoped)


class HttpProviderExecutor(_BaseAgentTurnExecutor):
    """Real coding-agent turn via a direct provider HTTP call (agent/providers.py) instead of
    Codex - spec section 16's "HTTP tool-calling executor". Used for read-only/analysis roles
    (role_policy.py's default_execution_kind=SPECIALIST_AGENT) so they work with any configured
    provider, not just OpenAI, and so their tool surface can be genuinely narrowed (Codex's
    shell access can't be tool-filtered the same way - see ScopedWorkspaceTools)."""

    async def execute(
        self, contract: TaskContract, context: TaskContext, cancellation: CancellationToken
    ) -> AgentExecutionResult:
        inner = WorkspaceTools(
            context.workspace_root, project_id=context.project_id, api_key=context.api_key
        )
        scoped = ScopedWorkspaceTools(inner, contract=contract)
        session = CodingAgentSession(
            provider_name=context.provider_name,
            model=context.model,
            api_key=context.api_key,
            workspace=scoped,  # type: ignore[arg-type] - see CodexContainerExecutor's note
            system_prompt=_role_system_prompt(contract),
            correlation_id=str(contract.task_id),
        )
        events = session.run(
            history=context.history,
            user_message=_contract_to_user_message(contract),
            images=context.images or None,
            cancellation=cancellation,
        )
        return await self._drive(events, workspace=scoped)


class DeterministicExecutor:
    """No LLM call at all - a PlatformToolCapabilityProvider capability invoked directly
    (spec's "deterministic platform operation" routing branch)."""

    def __init__(self, provider: PlatformToolCapabilityProvider | None = None) -> None:
        self._provider = provider or PlatformToolCapabilityProvider()

    async def execute(
        self, contract: TaskContract, context: TaskContext, cancellation: CancellationToken
    ) -> AgentExecutionResult:
        capability_id = contract.allowed_capabilities[0] if contract.allowed_capabilities else None
        if capability_id is None:
            return AgentExecutionResult(
                task_result=TaskResult(
                    status="failed", summary="no capability_id resolved for deterministic task"
                )
            )
        cap_context = CapabilityContext(
            project_id=context.project_id,
            run_id=contract.run_id,
            task_id=contract.task_id,
            role=contract.role,
        )
        result = await self._provider.invoke(
            capability_id, {"workspace_root": str(context.workspace_root)}, cap_context
        )
        status = "completed" if result.status == "completed" else "failed"
        return AgentExecutionResult(
            task_result=TaskResult(
                status=status, summary=f"platform capability {capability_id}: {result.status}"
            ),
            build_result=result.output if capability_id.endswith("build_check") else None,
            error=result.error,
        )


class _Cancelled(Exception):
    """Internal signal that a capability call was abandoned because the run was cancelled."""


async def _await_or_cancel(coro, cancellation: CancellationToken | None):
    """Run `coro`, but abandon it the moment `cancellation` fires.

    Skill and MCP calls are plain awaits with no internal cancellation checkpoints, so without
    this a Stop pressed mid-call would sit blocked until the call finished on its own (an MCP
    server's full 20s timeout + retries, say). Racing the two and cancelling the loser turns
    Stop into an actually-prompt stop for these executors too, matching the Codex/HTTP paths.

    `None` means "no cancellation wired for this call" - just await it plainly.
    """
    if cancellation is None:
        return await coro
    if cancellation.is_cancelled:
        raise _Cancelled
    work = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(cancellation.wait())
    try:
        await asyncio.wait({work, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if work.done():
            return work.result()
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await work
        raise _Cancelled
    finally:
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter


class SkillExecutor:
    def __init__(self, provider: SkillCapabilityProvider) -> None:
        self._provider = provider

    async def execute(
        self, contract: TaskContract, context: TaskContext, cancellation: CancellationToken
    ) -> AgentExecutionResult:
        skill_id = contract.allowed_skills[0] if contract.allowed_skills else None
        if skill_id is None:
            return AgentExecutionResult(
                task_result=TaskResult(
                    status="failed", summary="no skill_id resolved for skill task"
                )
            )
        cap_context = CapabilityContext(
            project_id=context.project_id,
            run_id=contract.run_id,
            task_id=contract.task_id,
            role=contract.role,
        )
        started = time.monotonic()
        criteria_text = "\n".join(
            f"- [{criterion.id}] {criterion.description}"
            for criterion in contract.acceptance_criteria
        )
        dependency_text = "\n".join(
            f"- [{item.local_id}] {item.title}: {item.summary}"
            for item in contract.dependency_results
        )
        review_brief = (
            f"Цель проекта: {contract.project_goal}\n"
            f"Ценность для пользователя: {contract.user_value}\n"
            f"Задача review: {contract.task_goal}\n"
            f"Критерии приёмки:\n{criteria_text or '- (не заданы)'}\n"
            f"Результаты реализации:\n{dependency_text or '- (нет)'}"
        )
        try:
            result = await _await_or_cancel(
                self._provider.invoke(
                    f"skill:{skill_id}",
                    {
                        "workspace_root": str(context.workspace_root),
                        "project_type": contract.current_state.project_type,
                        "provider_name": context.provider_name,
                        "model": context.model,
                        "api_key": context.api_key,
                        "brief_text": review_brief,
                    },
                    cap_context,
                ),
                cancellation,
            )
        except _Cancelled:
            return AgentExecutionResult(
                task_result=TaskResult(status="failed", summary=f"skill {skill_id}: отменено"),
                usage={"duration_seconds": time.monotonic() - started},
                error=cancellation.reason or "cancelled",
            )
        status = "completed" if result.status == "completed" else "partial"
        output = result.output if isinstance(result.output, dict) else {}
        review = output.get("review") if isinstance(output.get("review"), dict) else {}
        review_issues = [
            *review.get("todos", []),
            *review.get("recommended_fixes", []),
            *review.get("critical_issues", []),
            *review.get("major_issues", []),
        ]
        review_issues = [str(item) for item in review_issues if str(item).strip()]
        review_verdict = review.get("verdict")
        summary = f"skill {skill_id}: {result.status}"
        if review_verdict:
            summary += f"; review={review_verdict}"
        if review_issues:
            summary += "; " + " | ".join(review_issues[:5])
        usage = output.get("usage") if isinstance(output.get("usage"), dict) else {}
        usage = {**usage, "duration_seconds": time.monotonic() - started}
        return AgentExecutionResult(
            task_result=TaskResult(
                status=status,
                summary=summary,
                unresolved=review_issues[:10] if status != "completed" else [],
            ),
            usage=usage,
            build_result=output.get("build_result"),
            preview_result=output.get("preview"),
            runtime_health_result=output if skill_id == "runtime_health_check" else None,
            error=result.error,
        )


class McpExecutor:
    def __init__(self, provider: McpCapabilityProvider) -> None:
        self._provider = provider

    async def execute(
        self, contract: TaskContract, context: TaskContext, cancellation: CancellationToken
    ) -> AgentExecutionResult:
        capability_id = next(
            (c for c in contract.allowed_capabilities if c.startswith("mcp:")), None
        )
        if capability_id is None:
            return AgentExecutionResult(
                task_result=TaskResult(status="failed", summary="no MCP capability_id resolved")
            )
        cap_context = CapabilityContext(
            project_id=context.project_id,
            run_id=contract.run_id,
            task_id=contract.task_id,
            role=contract.role,
        )
        try:
            result = await _await_or_cancel(
                self._provider.invoke(capability_id, {}, cap_context), cancellation
            )
        except _Cancelled:
            return AgentExecutionResult(
                task_result=TaskResult(status="failed", summary=f"mcp {capability_id}: отменено"),
                error=cancellation.reason or "cancelled",
            )
        status = "completed" if result.status == "completed" else "failed"
        return AgentExecutionResult(
            task_result=TaskResult(status=status, summary=f"mcp {capability_id}: {result.status}"),
            error=result.error,
        )
