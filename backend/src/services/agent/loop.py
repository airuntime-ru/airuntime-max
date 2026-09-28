"""The coding agent's turn loop: think out loud, call tools, repeat.

This is what replaces the old "one JSON blob wipes the whole project" flow.
On every user message the agent can look at the files that already exist,
read the ones relevant to the request, and make targeted edits - the same
loop shape used by tool-using coding assistants (read -> edit -> verify).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.services.agent.codex_runtime import (
    CODEX_ELIGIBLE_PROVIDERS,
    CodexAgentSession,
    resolve_codex_base_url,
)
from src.services.agent.events import AgentDone, TextDelta, ToolCallRequested, ToolCallResult
from src.services.agent.providers import get_agent_provider
from src.services.agent.tools import TOOL_DEFS, WorkspaceTools
from src.services.file_context import ImageAttachment
from src.services.orchestration.cancellation import CancellationToken

MAX_ITERATIONS = 100_000


def _normalize_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse consecutive same-role turns and drop a leading assistant turn.

    Some providers (Anthropic in particular) require strictly alternating
    user/assistant turns starting with "user". Chat history read back from
    the database should already alternate, but we normalize defensively so a
    stray duplicate role never breaks the wire format.
    """

    normalized: list[dict[str, str]] = []
    for item in history:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["content"] += f"\n\n{content}"
        else:
            normalized.append({"role": role, "content": content})
    if normalized and normalized[0]["role"] == "assistant":
        normalized = normalized[1:]
    return normalized


class CodingAgentSession:
    def __init__(
        self,
        *,
        provider_name: str,
        model: str,
        api_key: str,
        workspace: WorkspaceTools,
        system_prompt: str,
        correlation_id: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        # Plan-scoped reasoning effort (see services/model_access.py). None = global default.
        self.reasoning_effort = reasoning_effort
        self.provider_name = provider_name
        self.model = model
        self.api_key = api_key
        self.workspace = workspace
        self.system_prompt = system_prompt
        # Passed straight through to CodexAgentSession - see that class's own docstring on
        # correlation_id for what it enables (deterministic container naming for cancellation).
        self.correlation_id = correlation_id
        # Codex CLI (running in its own Docker container - see codex_runtime.py) replaces the
        # HTTP provider loop below for the providers it can drive. Anything else (an explicit
        # anthropic/gemini/openrouter pick) keeps using the old per-provider streaming adapters.
        self.use_codex = provider_name in CODEX_ELIGIBLE_PROVIDERS
        if not self.use_codex:
            self.provider = get_agent_provider(provider_name)

    async def run(
        self,
        *,
        history: list[dict[str, str]],
        user_message: str,
        images: list[ImageAttachment] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[TextDelta | ToolCallRequested | ToolCallResult | AgentDone]:
        if cancellation is not None and cancellation.is_cancelled:
            # A turn cancelled before it even started (e.g. the user hit Stop while the request
            # was still queued) must not touch the provider or Codex at all - checked before the
            # use_codex branch too, since CodexAgentSession.run() itself only starts checking
            # once its own polling loop begins.
            yield AgentDone(reason="cancelled", error=cancellation.reason or "Cancelled")
            return

        if self.use_codex:
            session = CodexAgentSession(
                model=self.model,
                workspace=self.workspace,
                system_prompt=self.system_prompt,
                correlation_id=self.correlation_id,
                reasoning_effort=self.reasoning_effort,
                api_key=self.api_key,
                openai_base_url=resolve_codex_base_url(
                    provider_name=self.provider_name, api_key=self.api_key
                ),
            )
            async for event in session.run(
                history=_normalize_history(history),
                user_message=user_message,
                images=images,
                cancellation=cancellation,
            ):
                yield event
            return

        tools = TOOL_DEFS if self.provider.supports_tools() else []
        wire_messages = self.provider.build_messages(
            _normalize_history(history), user_message, images=images or []
        )

        final_text_parts: list[str] = []
        iterations = 0

        while iterations < MAX_ITERATIONS:
            if cancellation is not None and cancellation.is_cancelled:
                # Cooperative only: an already-dispatched tool call below (e.g. a build_project
                # RPC blocked in a real Docker build via asyncio.to_thread) is not force-killed -
                # Python threads can't be, and the RPC itself has no cancel primitive of its own
                # (unlike the Codex path's real docker-stop above). It's simply left to finish
                # server-side with its result discarded, same as an abandoned HTTP disconnect
                # always did. Checking here still stops any *further* iteration/tool call from
                # starting once cancellation is requested, which is the achievable half of "Stop"
                # for this path - see cancellation.py's module docstring for the honest scope.
                yield AgentDone(reason="cancelled", error=cancellation.reason or "Cancelled")
                return
            iterations += 1
            turn_final = None
            async for event in self.provider.stream_turn(
                system_prompt=self.system_prompt,
                messages=wire_messages,
                tools=tools,
                model=self.model,
                api_key=self.api_key,
            ):
                if isinstance(event, TextDelta):
                    if event.text:
                        final_text_parts.append(event.text)
                        yield event
                else:
                    turn_final = event

            if turn_final is None:
                yield AgentDone(reason="error", error="Provider stream ended without a result")
                return

            if turn_final.stop_reason == "error":
                yield AgentDone(reason="error", error=turn_final.error or "Unknown provider error")
                return

            if turn_final.wire_message is not None:
                wire_messages.append(turn_final.wire_message)

            if not turn_final.tool_calls:
                yield AgentDone(reason="stop")
                return

            results: list[ToolCallResult] = []
            for call in turn_final.tool_calls:
                yield call
                # Off the event loop: some tools (build_project) block on a real Docker build
                # via a synchronous Redis round-trip that can take minutes - a plain call here
                # would freeze every other request this process is serving for that long.
                outcome = await asyncio.to_thread(self.workspace.call, call.name, call.arguments)
                result = ToolCallResult(
                    call_id=call.call_id,
                    name=call.name,
                    ok=outcome.ok,
                    summary=outcome.summary,
                    content=outcome.content,
                )
                results.append(result)
                yield result

            wire_messages.extend(self.provider.build_tool_result_messages(results))

        yield AgentDone(reason="max_iterations")
