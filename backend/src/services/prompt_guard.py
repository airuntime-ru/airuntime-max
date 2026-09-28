import re

# Hard ceiling for API/DB acceptance - large enough for pasted deploy/runtime logs.
MAX_USER_MESSAGE_CHARS = 500_000
# What we actually send to the coding agent (prefer the useful tail of logs).
MAX_AGENT_INPUT_CHARS = 100_000
# Per-turn budget when replaying older chat history into the LLM context.
MAX_HISTORY_MESSAGE_CHARS = 16_000

INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"disregard\s+(the\s+)?system\s+prompt",
    r"reveal\s+(the\s+)?(system|hidden)\s+prompt",
    r"print\s+(all\s+)?secrets",
)


def sanitize_user_message(content: str) -> str:
    """Validate and accept a user message for storage (full text up to MAX_USER_MESSAGE_CHARS)."""
    trimmed = content.strip()
    if not trimmed:
        raise ValueError("Message cannot be empty")
    if len(trimmed) > MAX_USER_MESSAGE_CHARS:
        raise ValueError(
            f"Сообщение слишком длинное ({len(trimmed)} символов). "
            f"Максимум — {MAX_USER_MESSAGE_CHARS}."
        )
    lowered = trimmed.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            raise ValueError("Message contains disallowed instruction patterns")
    return trimmed


def prepare_agent_user_message(content: str, *, max_chars: int = MAX_AGENT_INPUT_CHARS) -> str:
    """Trim oversized paste for the LLM, keeping the tail (where deploy errors usually are).

    The full sanitized text remains stored in the messages table for the UI.
    """
    trimmed = (content or "").strip()
    if not trimmed:
        return trimmed
    if len(trimmed) <= max_chars:
        return trimmed
    kept = trimmed[-max_chars:]
    note = (
        f"[Лог обрезан до последних {max_chars} символов из {len(trimmed)}; "
        "полный текст сохранён в чате.]\n\n"
    )
    return note + kept


def clip_history_message(content: str, *, max_chars: int = MAX_HISTORY_MESSAGE_CHARS) -> str:
    """Keep history turns from blowing the prompt after a large log paste."""
    return prepare_agent_user_message(content, max_chars=max_chars)


def redact_secrets(text: str) -> str:
    """Best-effort scrub for secret-looking values in text that may end up in an LLM prompt
    (e.g. runtime/build error excerpts - see deployment_check.py's `_collect_error_excerpt` and
    orchestration/context_engine.py's `redact()`, its main callers). Two independent patterns:

    1. `KEY: value` / `KEY=value` where KEY names a credential (api_key/token/secret/password).
    2. `scheme://user:password@host` - the shape project_services.py's generated
       DATABASE_URL/REDIS_URL/MONGO_URL/RABBITMQ_URL env vars take, which (1) alone misses
       entirely since "DATABASE_URL" doesn't contain any of those four keywords.

    Not a substitute for keeping secret values out of prompts in the first place (they already
    are - request_secret/request_service never pass a value through the LLM) - this guards
    against a *different* value entering log/error text some other way, e.g. a crash or a
    debug print in agent-generated code that echoes `os.environ`.
    """
    scrubbed = re.sub(
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    return re.sub(r"://([^:/\s@]+):([^@/\s]+)@", r"://\1:[REDACTED]@", scrubbed)
