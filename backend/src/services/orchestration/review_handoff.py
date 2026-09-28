"""Turn an independent QA verdict into a short implementer TODO list.

The old Ralph-style loop treated ``revise`` as "reject and replan the whole site". Models are
strong enough to close a concrete punch list, so the engine hands that list to one implementer
pass (optionally one more judge pass) and then ships even if the second judge is still picky.
"""

from __future__ import annotations

import json
from typing import Any

from src.services.orchestration.schemas import (
    AcceptanceCriterion,
    PlannedTask,
    SpecialistRole,
    WriteScope,
)

JUDGE_FIX_PREFIX = "judge_fix_"
JUDGE_QA_PREFIX = "judge_qa_"
_MAX_TODOS = 10


def _clean_item(item: object) -> str:
    text = " ".join(str(item).split()).strip()
    return text


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.rstrip(".").lower()
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= _MAX_TODOS:
            break
    return out


def collect_review_todos(
    review: dict[str, Any] | None,
    *,
    extra: list[str] | None = None,
) -> list[str]:
    """Prefer explicit ``todos``, then recommended fixes, then critical/major issues."""
    payload = review if isinstance(review, dict) else {}
    buckets = (
        payload.get("todos"),
        payload.get("recommended_fixes"),
        payload.get("critical_issues"),
        payload.get("major_issues"),
        extra,
    )
    collected: list[str] = []
    for bucket in buckets:
        if not bucket:
            continue
        if not isinstance(bucket, list):
            continue
        collected.extend(_clean_item(item) for item in bucket if _clean_item(item))
        if collected:
            break
    todos = _dedupe(collected)
    verdict = str(payload.get("verdict") or "").strip().lower()
    if not todos and verdict in {"revise", "blocked"}:
        return [
            "Закрой замечания независимого ревьюера точечными правками, не переписывая проект с нуля."
        ]
    return todos


def verdict_from_task_result(claimed: dict[str, Any] | None) -> str | None:
    payload = claimed if isinstance(claimed, dict) else {}
    summary = str(payload.get("summary") or "")
    for token in ("review=pass", "review=revise", "review=blocked"):
        if token in summary:
            return token.split("=", 1)[1]
    if payload.get("unresolved"):
        return "revise"
    if payload.get("status") == "completed":
        return "pass"
    return None


def collect_todos_from_task_result(claimed: dict[str, Any] | None) -> list[str]:
    payload = claimed if isinstance(claimed, dict) else {}
    unresolved = [
        _clean_item(item) for item in (payload.get("unresolved") or []) if _clean_item(item)
    ]
    if unresolved:
        return _dedupe(unresolved)
    summary = str(payload.get("summary") or "")
    extras: list[str] = []
    if "review=" in summary:
        extras.extend(
            part.strip()
            for part in summary.split(";")
            if part.strip() and not part.strip().startswith("review=") and "skill " not in part
        )
    return collect_review_todos(
        {"verdict": verdict_from_task_result(payload), "todos": extras},
    )


def parse_task_result_json(result_json: str | None) -> dict[str, Any]:
    if not result_json:
        return {}
    try:
        payload = json.loads(result_json)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def format_judge_todos_for_implementer(todos: list[str]) -> str:
    lines = [f"{index}. {item}" for index, item in enumerate(todos, start=1)]
    return "TODO судьи:\n" + "\n".join(lines)


def judge_fix_round_count(tasks: list[Any]) -> int:
    return sum(
        1 for task in tasks if str(getattr(task, "local_id", "")).startswith(JUDGE_FIX_PREFIX)
    )


def can_enqueue_judge_fix(tasks: list[Any], *, max_rounds: int) -> bool:
    if max_rounds <= 0:
        return False
    return judge_fix_round_count(tasks) < max_rounds


def build_judge_fix_planned_tasks(
    *,
    todos: list[str],
    qa_local_ids: list[str],
    relevant_paths: list[str],
    suggested_skills: list[str],
    round_number: int,
    include_followup_qa: bool,
) -> list[PlannedTask]:
    todos_text = format_judge_todos_for_implementer(todos)
    fix_id = f"{JUDGE_FIX_PREFIX}{round_number}"
    planned: list[PlannedTask] = [
        PlannedTask(
            local_id=fix_id,
            title="Исправить замечания судьи",
            role=SpecialistRole.IMPLEMENTER,
            goal=(
                "Исправь только пункты из TODO-списка независимого ревьюера. "
                "Не переписывай проект с нуля и не открывай новый объём работ.\n\n"
                f"{todos_text}"
            ),
            reason="Вердикт QA-судьи отдаётся исполнителю один раз вместо полного перепланирования.",
            dependencies=list(qa_local_ids),
            relevant_paths=relevant_paths,
            acceptance_criteria=[
                AcceptanceCriterion(
                    id=f"judge_todos_{round_number}",
                    description="Каждый пункт TODO судьи закрыт в файлах, проект собирается.",
                    verification_method="build",
                )
            ],
            write_scope=WriteScope.SCOPED_PATHS,
        )
    ]
    if not include_followup_qa:
        return planned
    planned.append(
        PlannedTask(
            local_id=f"{JUDGE_QA_PREFIX}{round_number}",
            title="Повторная проверка судьи",
            role=SpecialistRole.QA_REVIEWER,
            goal=(
                "Проверь, что TODO предыдущего ревью закрыты. "
                "Не открывай новый широкий редизайн: оставшиеся вкусовые замечания не блокируют поставку."
            ),
            reason="Короткий второй прогон судьи после точечных правок.",
            dependencies=[fix_id],
            relevant_paths=relevant_paths,
            suggested_skills=suggested_skills,
            acceptance_criteria=[
                AcceptanceCriterion(
                    id=f"judge_recheck_{round_number}",
                    description="TODO закрыты либо оставшиеся замечания не блокируют поставку.",
                    verification_method="llm_review",
                )
            ],
            write_scope=WriteScope.NONE,
        )
    )
    return planned
