"""Event types streamed out of the coding agent loop.

These are provider-agnostic: whatever LLM is driving the turn, the rest of
the system (SSE encoding, chat persistence, UI) only ever sees these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class TextDelta:
    """A fragment of the assistant's visible reply, to be streamed live."""

    text: str


@dataclass
class ToolCallRequested:
    """The model asked to run a workspace tool."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallResult:
    """Result of executing a tool call, surfaced to the UI and fed back to the model."""

    call_id: str
    name: str
    ok: bool
    summary: str
    content: str = ""


@dataclass
class TurnFinished:
    """Internal signal from a provider adapter: one model turn is complete."""

    stop_reason: Literal["tool_use", "stop", "max_tokens", "error"]
    wire_message: dict[str, Any] | None = None
    tool_calls: list[ToolCallRequested] = field(default_factory=list)
    error: str | None = None


@dataclass
class AgentDone:
    reason: Literal["stop", "max_iterations", "error", "cancelled"]
    error: str | None = None
    # Raw provider usage payload (token counts etc.), when the provider reports one - e.g.
    # Codex's turn.completed event. Shape is provider-specific and not normalized here; callers
    # that want to display it decide how. None when the provider didn't report anything.
    usage: dict[str, Any] | None = None


AgentEvent = TextDelta | ToolCallRequested | ToolCallResult | AgentDone
