"""Builds LLM conversation context from the chat's message history.

Previously the assistant's memory of a conversation lived in a *separate*
Redis list (`chat-memory:{chat_id}`) that was written to independently of the
`messages` table the UI reads from. The two could silently diverge (e.g. a
message persisted by one code path but never remembered by the other),
which is a large part of why the chat "didn't hold context" - the model
was sometimes replying from a different history than what the user could
see on screen.

Now `messages` (the table the UI already renders from) is the single source
of truth. Redis is only used as a disposable cache of a *summary* of older
turns, so very long conversations don't blow up the prompt - never as the
primary record.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from src.db.models.message import Message
from src.services.agent.codex_runtime import CODEX_ELIGIBLE_PROVIDERS, codex_simple_complete
from src.services.agent.events import TextDelta, TurnFinished
from src.services.agent.providers import get_agent_provider
from src.services.prompt_guard import clip_history_message

VERBATIM_TURNS = 30
SUMMARY_TTL_SECONDS = 6 * 60 * 60

_SUMMARY_PROMPT = (
    "Сожми историю диалога ниже в краткую сводку на русском (до 200 слов): что за проект, "
    "какие решения уже приняты, что уже сделано, какие открытые вопросы остались. "
    "Пиши только сводку, без вступлений."
)


def _redis_client():
    try:
        from redis import Redis

        from src.core.config import settings

        client = Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def load_plain_history(db: Session, chat_id) -> list[dict[str, str]]:
    rows = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [
        {
            "role": "assistant" if row.role == "assistant" else "user",
            "content": clip_history_message(row.content_markdown),
        }
        for row in rows
        if row.content_markdown and row.content_markdown.strip()
    ]


async def _summarize(
    older: list[dict[str, str]], *, provider_name: str, model: str, api_key: str
) -> str:
    if not api_key:
        return ""
    transcript = "\n\n".join(f"{m['role']}: {m['content']}" for m in older[-80:])

    if provider_name in CODEX_ELIGIBLE_PROVIDERS:
        return await codex_simple_complete(
            system_prompt=_SUMMARY_PROMPT,
            user_text=transcript,
            model=model,
            api_key=api_key,
            provider_name=provider_name,
        )

    provider = get_agent_provider(provider_name)
    text_parts: list[str] = []
    try:
        async for event in provider.stream_turn(
            system_prompt=_SUMMARY_PROMPT,
            messages=provider.build_messages([], transcript),
            tools=[],
            model=model,
            api_key=api_key,
        ):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
            elif isinstance(event, TurnFinished) and event.stop_reason == "error":
                return ""
    except Exception:
        return ""
    return "".join(text_parts).strip()


async def build_llm_context(
    db: Session, chat_id, *, provider_name: str, model: str, api_key: str
) -> tuple[list[dict[str, str]], str]:
    """Returns (recent_verbatim_history, summary_of_older_turns)."""

    history = load_plain_history(db, chat_id)
    if len(history) <= VERBATIM_TURNS:
        return history, ""

    older = history[:-VERBATIM_TURNS]
    recent = history[-VERBATIM_TURNS:]

    cache_key = f"chat-context-summary:{chat_id}:{len(older)}"
    client = _redis_client()
    if client is not None:
        try:
            cached = client.get(cache_key)
            if cached:
                data = json.loads(cached)
                return recent, data.get("summary", "")
        except Exception:
            pass

    summary = await _summarize(older, provider_name=provider_name, model=model, api_key=api_key)
    if client is not None and summary:
        try:
            client.setex(cache_key, SUMMARY_TTL_SECONDS, json.dumps({"summary": summary}))
        except Exception:
            pass
    return recent, summary
