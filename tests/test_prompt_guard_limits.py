"""Tests for chat paste size limits and agent truncation."""

import pytest

from src.services.prompt_guard import (
    MAX_AGENT_INPUT_CHARS,
    MAX_USER_MESSAGE_CHARS,
    prepare_agent_user_message,
    sanitize_user_message,
)


def test_sanitize_accepts_large_log_paste():
    text = "Traceback\n" + ("line\n" * 5_000)
    assert len(text) > 12_000
    assert len(text) < MAX_USER_MESSAGE_CHARS
    assert sanitize_user_message(text) == text.strip()


def test_prepare_agent_truncates_with_russian_note():
    text = "HEAD\n" + ("x" * (MAX_AGENT_INPUT_CHARS + 500)) + "\nTAIL_ERROR"
    prepared = prepare_agent_user_message(text)
    assert "обрезан" in prepared
    assert prepared.endswith("TAIL_ERROR")
    assert len(prepared) < len(text) + 200


def test_sanitize_rejects_hard_ceiling():
    text = "y" * (MAX_USER_MESSAGE_CHARS + 1)
    with pytest.raises(ValueError, match="слишком длинное"):
        sanitize_user_message(text)
