import pytest
from pydantic import BaseModel

from src.services.agent import pipeline_llm
from src.services.agent.events import TextDelta, TurnFinished
from src.services.file_context import ImageAttachment


class _Widget(BaseModel):
    name: str
    count: int = 0


def test_strip_code_fence_removes_markdown_fence():
    fenced = '```json\n{"a": 1}\n```'
    assert pipeline_llm._strip_code_fence(fenced) == '{"a": 1}'


def test_strip_code_fence_leaves_plain_json_untouched():
    plain = '{"a": 1}'
    assert pipeline_llm._strip_code_fence(plain) == plain


@pytest.mark.asyncio
async def test_complete_structured_success_first_try(monkeypatch):
    async def fake_raw_complete(**kwargs):
        return '{"name": "hero", "count": 3}'

    monkeypatch.setattr(pipeline_llm, "_raw_complete", fake_raw_complete)

    result = await pipeline_llm.complete_structured(
        provider_name="anthropic",
        model="claude-sonnet-5",
        api_key="test-key",
        system_prompt="sys",
        user_text="user",
        response_model=_Widget,
    )
    assert result == _Widget(name="hero", count=3)


@pytest.mark.asyncio
async def test_complete_structured_repairs_malformed_json(monkeypatch):
    calls = []

    async def fake_raw_complete(**kwargs):
        calls.append(kwargs["user_text"])
        if len(calls) == 1:
            return "not json at all"
        return '{"name": "fixed", "count": 1}'

    monkeypatch.setattr(pipeline_llm, "_raw_complete", fake_raw_complete)

    result = await pipeline_llm.complete_structured(
        provider_name="anthropic",
        model="m",
        api_key="k",
        system_prompt="sys",
        user_text="original request",
        response_model=_Widget,
    )
    assert result == _Widget(name="fixed", count=1)
    assert len(calls) == 2
    assert "original request" in calls[1]
    assert "не прошёл валидацию" in calls[1]


@pytest.mark.asyncio
async def test_complete_structured_schema_mismatch_triggers_repair(monkeypatch):
    calls = []

    async def fake_raw_complete(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return '{"count": "not-a-number"}'  # missing name, wrong type for count
        return '{"name": "ok", "count": 2}'

    monkeypatch.setattr(pipeline_llm, "_raw_complete", fake_raw_complete)

    result = await pipeline_llm.complete_structured(
        provider_name="openai",
        model="m",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        response_model=_Widget,
    )
    assert result == _Widget(name="ok", count=2)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_complete_structured_gives_up_after_repair_fails(monkeypatch):
    async def always_broken(**kwargs):
        return "still not json"

    monkeypatch.setattr(pipeline_llm, "_raw_complete", always_broken)

    result = await pipeline_llm.complete_structured(
        provider_name="anthropic",
        model="m",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        response_model=_Widget,
    )
    assert result is None


@pytest.mark.asyncio
async def test_complete_structured_empty_response_fails_open(monkeypatch):
    async def empty(**kwargs):
        return ""

    monkeypatch.setattr(pipeline_llm, "_raw_complete", empty)

    result = await pipeline_llm.complete_structured(
        provider_name="anthropic",
        model="m",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        response_model=_Widget,
    )
    assert result is None


@pytest.mark.asyncio
async def test_raw_complete_dispatches_codex_for_openai(monkeypatch):
    calls = {"codex": 0, "provider": 0}

    async def fake_codex_simple_complete(**kwargs):
        calls["codex"] += 1
        return '{"name": "codex-path", "count": 0}'

    def fake_get_agent_provider(name):
        calls["provider"] += 1
        raise AssertionError("should not build an HTTP provider for the openai/Codex path")

    monkeypatch.setattr(pipeline_llm, "codex_simple_complete", fake_codex_simple_complete)
    monkeypatch.setattr(pipeline_llm, "get_agent_provider", fake_get_agent_provider)

    raw = await pipeline_llm._raw_complete(
        provider_name="openai",
        model="gpt-5.4-mini",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        timeout_seconds=30,
    )
    assert raw == '{"name": "codex-path", "count": 0}'
    assert calls["codex"] == 1
    assert calls["provider"] == 0


@pytest.mark.asyncio
async def test_raw_complete_dispatches_provider_stream_for_non_openai(monkeypatch):
    class _FakeProvider:
        def build_messages(self, history, user_message, *, images=()):
            return [{"role": "user", "content": user_message}]

        async def stream_turn(self, **kwargs):
            yield TextDelta(text="hello ")
            yield TextDelta(text="world")
            yield TurnFinished(stop_reason="stop")

    codex_called = {"n": 0}

    async def fake_codex_simple_complete(**kwargs):
        codex_called["n"] += 1
        return ""

    monkeypatch.setattr(pipeline_llm, "codex_simple_complete", fake_codex_simple_complete)
    monkeypatch.setattr(pipeline_llm, "get_agent_provider", lambda name: _FakeProvider())

    raw = await pipeline_llm._raw_complete(
        provider_name="anthropic",
        model="claude-sonnet-5",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        timeout_seconds=30,
    )
    assert raw == "hello world"
    assert codex_called["n"] == 0


@pytest.mark.asyncio
async def test_raw_complete_returns_empty_on_provider_error(monkeypatch):
    class _FailingProvider:
        def build_messages(self, history, user_message, *, images=()):
            return [{"role": "user", "content": user_message}]

        async def stream_turn(self, **kwargs):
            yield TurnFinished(stop_reason="error", error="401 unauthorized")

    monkeypatch.setattr(pipeline_llm, "get_agent_provider", lambda name: _FailingProvider())

    raw = await pipeline_llm._raw_complete(
        provider_name="gemini",
        model="m",
        api_key="bad-key",
        system_prompt="sys",
        user_text="u",
        timeout_seconds=30,
    )
    assert raw == ""


@pytest.mark.asyncio
async def test_raw_complete_embeds_images_via_provider_build_messages(monkeypatch):
    seen_messages = []

    class _FakeProvider:
        def build_messages(self, history, user_message, *, images=()):
            built = [{"role": "user", "content": user_message, "_images": list(images)}]
            seen_messages.append(built)
            return built

        async def stream_turn(self, **kwargs):
            assert kwargs["messages"] is seen_messages[-1]
            yield TextDelta(text="ok")
            yield TurnFinished(stop_reason="stop")

    monkeypatch.setattr(pipeline_llm, "get_agent_provider", lambda name: _FakeProvider())
    image = ImageAttachment(filename="a.png", content_type="image/png", data_base64="Zm9v")

    raw = await pipeline_llm._raw_complete(
        provider_name="anthropic",
        model="m",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        timeout_seconds=30,
        images=[image],
    )
    assert raw == "ok"
    assert seen_messages[-1][0]["_images"] == [image]


@pytest.mark.asyncio
async def test_raw_complete_ignores_images_on_codex_path_without_dropping_the_call(monkeypatch):
    async def fake_codex_simple_complete(**kwargs):
        assert kwargs["images"] is None
        assert kwargs["workspace_root"] is None
        return '{"name": "codex", "count": 0}'

    monkeypatch.setattr(pipeline_llm, "codex_simple_complete", fake_codex_simple_complete)
    image = ImageAttachment(filename="a.png", content_type="image/png", data_base64="Zm9v")

    raw = await pipeline_llm._raw_complete(
        provider_name="openai",
        model="gpt-5.4-mini",
        api_key="k",
        system_prompt="sys",
        user_text="u",
        timeout_seconds=30,
        images=[image],
    )
    assert raw == '{"name": "codex", "count": 0}'


@pytest.mark.asyncio
async def test_raw_complete_forwards_images_to_screenshot_only_codex_workspace(
    monkeypatch, tmp_path
):
    seen = {}

    async def fake_codex_simple_complete(**kwargs):
        seen.update(kwargs)
        return '{"name": "codex-vision", "count": 2}'

    monkeypatch.setattr(pipeline_llm, "codex_simple_complete", fake_codex_simple_complete)
    image = ImageAttachment(filename="desktop.png", content_type="image/png", data_base64="Zm9v")

    raw = await pipeline_llm._raw_complete(
        provider_name="openai",
        model="gpt-5.6-sol",
        api_key="k",
        system_prompt="sys",
        user_text="review",
        timeout_seconds=30,
        images=[image],
        codex_workspace_root=tmp_path,
        codex_project_id="project-1",
    )

    assert raw == '{"name": "codex-vision", "count": 2}'
    assert seen["images"] == [image]
    assert seen["workspace_root"] == tmp_path
    assert seen["project_id"] == "project-1"
