"""Structured observability events for the orchestration engine.

Emits durable RunEvent rows via events_bus with event_type ``trace`` or ``llm_call``.
Payloads are intentionally small and never include secrets, API keys, or raw model output.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from src.services.orchestration import events_bus

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token|authorization|credential)", re.I
)
_MAX_SUMMARY_LEN = 500
_MAX_DETAIL_STR_LEN = 500


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_details(value)
    if isinstance(value, str):
        if len(value) > _MAX_DETAIL_STR_LEN:
            return value[:_MAX_DETAIL_STR_LEN] + "..."
        return value
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:50]]
    return value


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    safe: dict[str, Any] = {}
    for key, value in details.items():
        if _SECRET_KEY_PATTERN.search(str(key)):
            continue
        safe[key] = _sanitize_value(value)
    return safe


def _base_payload(
    *,
    category: str,
    action: str,
    summary: str,
    duration_ms: int | None,
    tokens: dict[str, Any] | None,
    cost_credits: int | float | None,
    details: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "category": category,
        "action": action,
        "summary": (summary or "")[:_MAX_SUMMARY_LEN],
        "duration_ms": duration_ms,
        "tokens": _sanitize_details(tokens) if tokens else {},
        "cost_credits": cost_credits,
        "details": _sanitize_details(details),
    }


def emit_trace(
    db: Session,
    *,
    run_id: object,
    category: str,
    action: str,
    summary: str,
    duration_ms: int | None = None,
    tokens: dict[str, Any] | None = None,
    cost_credits: int | float | None = None,
    details: dict[str, Any] | None = None,
    task_id: object | None = None,
) -> dict[str, Any]:
    payload = _base_payload(
        category=category,
        action=action,
        summary=summary,
        duration_ms=duration_ms,
        tokens=tokens,
        cost_credits=cost_credits,
        details=details,
    )
    return events_bus.emit(db, run_id=run_id, event_type="trace", payload=payload, task_id=task_id)


def emit_llm_usage(
    db: Session,
    *,
    run_id: object,
    category: str,
    action: str,
    summary: str,
    duration_ms: int | None = None,
    tokens: dict[str, Any] | None = None,
    cost_credits: int | float | None = None,
    details: dict[str, Any] | None = None,
    task_id: object | None = None,
) -> dict[str, Any]:
    payload = _base_payload(
        category=category,
        action=action,
        summary=summary,
        duration_ms=duration_ms,
        tokens=tokens,
        cost_credits=cost_credits,
        details=details,
    )
    return events_bus.emit(
        db, run_id=run_id, event_type="llm_call", payload=payload, task_id=task_id
    )
