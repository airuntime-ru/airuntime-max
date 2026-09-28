"""Real streaming + tool-calling adapters for each LLM provider.

Replaces the old fake-streaming provider clients (which made one blocking
HTTP call and then chopped the finished response into words). Every adapter
here opens a genuine server-sent-events stream to the provider and yields
text as the model actually produces it, and - for providers that support it -
accumulates structured tool calls so the coding agent can read/edit real
project files instead of generating the whole project in one shot.

Wire formats implemented against each vendor's public streaming API:
- OpenAI: POST /v1/chat/completions with stream=true, tool_calls deltas
  keyed by index.
- Anthropic: POST /v1/messages with stream=true, content_block_delta events
  (text_delta / input_json_delta).
- OpenRouter: OpenAI-compatible wire format, different base URL.
- Gemini: POST .../streamGenerateContent?alt=sse - text streaming only in
  this version (no tool calling yet - see supports_tools()).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from src.core.config import ROUTERAI_DEFAULT_BASE_URL, settings
from src.services.agent.events import TextDelta, ToolCallRequested, ToolCallResult, TurnFinished
from src.services.file_context import ImageAttachment

REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class AgentProvider(Protocol):
    def supports_tools(self) -> bool: ...

    def build_messages(
        self,
        history: list[dict[str, str]],
        user_message: str,
        *,
        images: list[ImageAttachment] = ...,
    ) -> list[dict[str, Any]]: ...

    def build_tool_result_messages(self, results: list[ToolCallResult]) -> list[dict[str, Any]]: ...

    def stream_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        api_key: str,
    ) -> AsyncIterator[TextDelta | TurnFinished]: ...


def _result_wire_content(result: ToolCallResult) -> str:
    return result.content if result.content else result.summary


class OpenAICompatibleProvider:
    """OpenAI Chat Completions wire format. Also used for OpenRouter."""

    def __init__(self, *, base_url: str, extra_headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url
        self.extra_headers = extra_headers or {}

    def supports_tools(self) -> bool:
        return True

    def build_messages(
        self,
        history: list[dict[str, str]],
        user_message: str,
        *,
        images: list[ImageAttachment] = (),
    ) -> list[dict[str, Any]]:
        msgs = [{"role": h["role"], "content": h["content"]} for h in history]
        if images:
            content: list[dict[str, Any]] = [{"type": "text", "text": user_message}]
            for image in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image.content_type};base64,{image.data_base64}"
                        },
                    }
                )
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({"role": "user", "content": user_message})
        return msgs

    def build_tool_result_messages(self, results: list[ToolCallResult]) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": r.call_id, "content": _result_wire_content(r)}
            for r in results
        ]

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    async def stream_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        api_key: str,
    ) -> AsyncIterator[TextDelta | TurnFinished]:
        if not api_key:
            yield TurnFinished(stop_reason="error", error="API key is not configured")
            return

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "stream": True,
        }
        if tools:
            body["tools"] = self._to_openai_tools(tools)
            body["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        content_acc = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST", self.base_url, headers=headers, json=body
                ) as response:
                    if response.status_code >= 400:
                        raw = await response.aread()
                        yield TurnFinished(
                            stop_reason="error",
                            error=f"HTTP {response.status_code}: {raw.decode(errors='replace')[:800]}",
                        )
                        return
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload_str = line[len("data:") :].strip()
                        if payload_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload_str)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            content_acc += text
                            yield TextDelta(text=text)
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = tool_calls_acc.setdefault(
                                idx, {"id": None, "name": None, "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
        except httpx.HTTPError as exc:
            yield TurnFinished(stop_reason="error", error=f"Network error: {exc}")
            return

        tool_calls: list[ToolCallRequested] = []
        wire_tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tool_calls_acc):
            slot = tool_calls_acc[idx]
            if not slot["name"] or not slot["id"]:
                continue
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCallRequested(call_id=slot["id"], name=slot["name"], arguments=args)
            )
            wire_tool_calls.append(
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
                }
            )

        wire_message: dict[str, Any] | None = None
        if content_acc or wire_tool_calls:
            wire_message = {"role": "assistant", "content": content_acc or None}
            if wire_tool_calls:
                wire_message["tool_calls"] = wire_tool_calls

        stop_reason = "tool_use" if tool_calls else ("stop" if finish_reason else "stop")
        yield TurnFinished(
            stop_reason=stop_reason, wire_message=wire_message, tool_calls=tool_calls
        )


class AnthropicProvider:
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def supports_tools(self) -> bool:
        return True

    def build_messages(
        self,
        history: list[dict[str, str]],
        user_message: str,
        *,
        images: list[ImageAttachment] = (),
    ) -> list[dict[str, Any]]:
        msgs = [
            {"role": h["role"], "content": [{"type": "text", "text": h["content"]}]}
            for h in history
        ]
        content: list[dict[str, Any]] = [{"type": "text", "text": user_message}]
        for image in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.content_type,
                        "data": image.data_base64,
                    },
                }
            )
        msgs.append({"role": "user", "content": content})
        return msgs

    def build_tool_result_messages(self, results: list[ToolCallResult]) -> list[dict[str, Any]]:
        content = [
            {
                "type": "tool_result",
                "tool_use_id": r.call_id,
                "content": _result_wire_content(r),
                "is_error": not r.ok,
            }
            for r in results
        ]
        return [{"role": "user", "content": content}]

    @staticmethod
    def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in tools
        ]

    async def stream_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        api_key: str,
    ) -> AsyncIterator[TextDelta | TurnFinished]:
        if not api_key:
            yield TurnFinished(stop_reason="error", error="API key is not configured")
            return

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = self._to_anthropic_tools(tools)

        headers = {
            "x-api-key": api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        blocks: dict[int, dict[str, Any]] = {}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST", self.API_URL, headers=headers, json=body
                ) as response:
                    if response.status_code >= 400:
                        raw = await response.aread()
                        yield TurnFinished(
                            stop_reason="error",
                            error=f"HTTP {response.status_code}: {raw.decode(errors='replace')[:800]}",
                        )
                        return
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str:
                            continue
                        try:
                            payload = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        ptype = payload.get("type")
                        if ptype == "content_block_start":
                            idx = payload["index"]
                            block = payload["content_block"]
                            if block.get("type") == "text":
                                blocks[idx] = {"type": "text", "text": ""}
                            elif block.get("type") == "tool_use":
                                blocks[idx] = {
                                    "type": "tool_use",
                                    "id": block.get("id"),
                                    "name": block.get("name"),
                                    "json_acc": "",
                                }
                        elif ptype == "content_block_delta":
                            idx = payload["index"]
                            delta = payload.get("delta") or {}
                            block = blocks.get(idx)
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if block is not None:
                                    block["text"] += text
                                if text:
                                    yield TextDelta(text=text)
                            elif delta.get("type") == "input_json_delta" and block is not None:
                                block["json_acc"] += delta.get("partial_json", "")
                        elif ptype == "error":
                            err = payload.get("error") or {}
                            yield TurnFinished(
                                stop_reason="error", error=str(err.get("message") or err)
                            )
                            return
                        elif ptype == "message_stop":
                            break
        except httpx.HTTPError as exc:
            yield TurnFinished(stop_reason="error", error=f"Network error: {exc}")
            return

        content_list: list[dict[str, Any]] = []
        tool_calls: list[ToolCallRequested] = []
        for idx in sorted(blocks):
            block = blocks[idx]
            if block["type"] == "text":
                if block["text"]:
                    content_list.append({"type": "text", "text": block["text"]})
            else:
                raw_json = block.get("json_acc") or ""
                try:
                    args = json.loads(raw_json) if raw_json else {}
                except json.JSONDecodeError:
                    args = {}
                if not block.get("id") or not block.get("name"):
                    continue
                content_list.append(
                    {"type": "tool_use", "id": block["id"], "name": block["name"], "input": args}
                )
                tool_calls.append(
                    ToolCallRequested(call_id=block["id"], name=block["name"], arguments=args)
                )

        wire_message = {"role": "assistant", "content": content_list} if content_list else None
        stop_reason = "tool_use" if tool_calls else "stop"
        yield TurnFinished(
            stop_reason=stop_reason, wire_message=wire_message, tool_calls=tool_calls
        )


class GeminiProvider:
    """Real token streaming for Gemini. Tool-calling not implemented yet."""

    def supports_tools(self) -> bool:
        return False

    def build_messages(
        self,
        history: list[dict[str, str]],
        user_message: str,
        *,
        images: list[ImageAttachment] = (),
    ) -> list[dict[str, Any]]:
        msgs = []
        for h in history:
            role = "model" if h["role"] == "assistant" else "user"
            msgs.append({"role": role, "parts": [{"text": h["content"]}]})
        parts: list[dict[str, Any]] = [{"text": user_message}]
        for image in images:
            parts.append(
                {"inline_data": {"mime_type": image.content_type, "data": image.data_base64}}
            )
        msgs.append({"role": "user", "parts": parts})
        return msgs

    def build_tool_result_messages(self, results: list[ToolCallResult]) -> list[dict[str, Any]]:
        return []

    async def stream_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        api_key: str,
    ) -> AsyncIterator[TextDelta | TurnFinished]:
        if not api_key:
            yield TurnFinished(stop_reason="error", error="API key is not configured")
            return

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
        )
        body = {
            "contents": messages,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
        }
        content_acc = ""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    url,
                    params={"key": api_key, "alt": "sse"},
                    json=body,
                ) as response:
                    if response.status_code >= 400:
                        raw = await response.aread()
                        yield TurnFinished(
                            stop_reason="error",
                            error=f"HTTP {response.status_code}: {raw.decode(errors='replace')[:800]}",
                        )
                        return
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str:
                            continue
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        candidates = chunk.get("candidates") or []
                        if not candidates:
                            continue
                        parts = (candidates[0].get("content") or {}).get("parts") or []
                        for part in parts:
                            text = part.get("text")
                            if text:
                                content_acc += text
                                yield TextDelta(text=text)
        except httpx.HTTPError as exc:
            yield TurnFinished(stop_reason="error", error=f"Network error: {exc}")
            return

        wire_message = {"role": "model", "parts": [{"text": content_acc}]} if content_acc else None
        yield TurnFinished(stop_reason="stop", wire_message=wire_message, tool_calls=[])


def get_agent_provider(provider_name: str) -> AgentProvider:
    if provider_name == "anthropic":
        return AnthropicProvider()
    if provider_name == "openrouter":
        return OpenAICompatibleProvider(
            base_url="https://openrouter.ai/api/v1/chat/completions",
            extra_headers={
                "HTTP-Referer": "https://airuntime.ru",
                "X-Title": "AIRuntime",
            },
        )
    if provider_name == "routerai":
        base = (settings.openai_base_url or ROUTERAI_DEFAULT_BASE_URL).rstrip("/")
        return OpenAICompatibleProvider(base_url=f"{base}/chat/completions")
    if provider_name == "gemini":
        return GeminiProvider()
    return OpenAICompatibleProvider(base_url="https://api.openai.com/v1/chat/completions")
