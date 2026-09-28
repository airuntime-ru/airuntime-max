"""visual_preview_review / accessibility_review - generalizes product_pipeline.py's
preview+review loop into a reusable skill instead of code duplication: same network-isolated
headless-browser preview (preview_runner.py, reached only via the docker_control_queue RPC -
this module never touches Docker directly), same independent-reviewer-gets-no-code-access
contract, same deterministic pre-gate before spending an LLM call on an obviously-broken
preview.

accessibility_review is the same underlying flow with the review prompt narrowed to
accessibility-specific findings (contrast/semantics/aria) rather than full product quality -
still one preview run, just a different system prompt and which ReviewScore field the
completeness check leans on.
"""

from __future__ import annotations

import base64
from pathlib import Path

from src.services.agent.pipeline_llm import complete_structured
from src.services.agent.pipeline_models import PreviewResult, ReviewResult
from src.services.docker_control_queue import submit_control_job
from src.services.file_context import MAX_IMAGE_BYTES, ImageAttachment
from src.services.orchestration.preview_gate import (
    blocking_network_errors,
    blocking_overflow_elements,
)
from src.services.orchestration.schemas import (
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill, aggregate_usage

_REVIEW_SYSTEM_PROMPT = """Ты - независимый арт-директор и ревьюер качества продукта на
платформе AIRuntime. Тебе НЕ показывают код или рассуждения агента, который делал сайт:
только бриф, машинные данные браузера и реальные скриншоты desktop/mobile.

Проверяй строго, как работу коммерческой дизайн-студии:
- первый экран за 3 секунды объясняет конкретную ценность и имеет один доминирующий CTA;
- арт-дирекшн специфичен для продукта, а не generic SaaS/шаблон из карточек;
- типографика имеет выразительную display/body-пару, ясную иерархию и читаемые длины строк;
- палитра, изображения/графика, сетка, радиусы и отступы образуют одну систему;
- секции различаются композицией и ритмом, есть воздух и осмысленная асимметрия;
- весь копирайт предметный, без lorem, метатекста о создании сайта и пустых обещаний;
- mobile 390px не является ужатым desktop: CTA доступен, ничего не обрезано и не переполнено;
- нет битых изображений, 4xx/5xx, console errors и горизонтального скролла.

Не ставь pass из вежливости. Generic, визуально бедный или недоказанный результат = revise.
Ставь высокие баллы только когда это подтверждается скриншотами обоих viewport.

При revise заполни todos 3-8 конкретными пунктами: файл или область + что сделать
(например «public/index.html: подними CTA выше сгиба», «public/styles.css: убери
горизонтальный overflow на 390px»). Не предлагай переписать сайт с нуля. Платформа
отдаст этот список исполнителю один раз — не устраивай бесконечный цикл правок."""

_ACCESSIBILITY_SYSTEM_PROMPT = """Ты - независимый ревьюер доступности (accessibility) на
платформе AIRuntime. Тебе НЕ показывают код - только результат автоматической проверки в
браузере и реальные скриншоты desktop/mobile. Сфокусируйся на: контрасте, семантической
разметке, alt-тексте изображений, доступности с клавиатуры, aria-атрибутах, размере touch
targets и отсутствии обрезания на мобильном. Не оценивай общий дизайн вне доступности."""

_INFRA_FAILURE_MARKERS = (
    "worker may be unavailable",
    "is not built yet",
    "could not reach docker",
    "timed out after",
)


def _is_infra_failure(preview: PreviewResult) -> bool:
    if preview.status != "failed":
        return False
    return any(
        any(marker in err.lower() for marker in _INFRA_FAILURE_MARKERS)
        for err in preview.fatal_errors
    )


def _deterministic_gate(preview: PreviewResult) -> ReviewResult | None:
    if preview.status == "failed" or preview.fatal_errors:
        return ReviewResult(
            verdict="blocked", critical_issues=list(preview.fatal_errors) or ["preview failed"]
        )
    if any(page.broken_images for page in preview.pages):
        return ReviewResult(
            verdict="revise",
            major_issues=["broken images detected in preview"],
            todos=[
                "Почини битые изображения: поправь src/пути или замени ассеты, чтобы preview не показывал broken images."
            ],
        )
    deterministic_issues: list[str] = []
    deterministic_todos: list[str] = []
    if any(page.console_errors for page in preview.pages):
        deterministic_issues.append("browser console errors detected")
        deterministic_todos.append("Убери ошибки в browser console, которые видны в preview.")
    if any(blocking_network_errors(page.network_errors) for page in preview.pages):
        deterministic_issues.append("4xx/5xx or failed network requests detected")
        deterministic_todos.append(
            "Убери 4xx/5xx и failed network requests из preview (локальные ассеты, без внешних CDN)."
        )
    if any(blocking_overflow_elements(page.overflow_elements) for page in preview.pages):
        deterministic_issues.append("horizontal overflow detected")
        deterministic_todos.append(
            "Убери горизонтальный overflow документа на desktop и mobile (ширина контента не должна превышать viewport)."
        )
    if deterministic_issues:
        return ReviewResult(
            verdict="revise",
            major_issues=deterministic_issues,
            todos=deterministic_todos,
        )
    return None


def _load_screenshots(
    preview: PreviewResult, *, workspace_root: str
) -> tuple[list[ImageAttachment], Path | None]:
    """Load only preview-owned PNGs and return their common run directory.

    That directory, rather than the project checkout, is mounted for an OpenAI/Codex visual
    review. The reviewer can inspect screenshots but cannot read the implementation it judges.
    """
    root = Path(workspace_root).resolve()
    images: list[ImageAttachment] = []
    screenshot_dir: Path | None = None
    for page in preview.pages:
        if not page.screenshot_ref:
            continue
        candidate = (root / page.screenshot_ref).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.suffix.lower() != ".png" or not candidate.is_file():
            continue
        if candidate.stat().st_size > MAX_IMAGE_BYTES:
            continue
        if screenshot_dir is None:
            screenshot_dir = candidate.parent
        elif candidate.parent != screenshot_dir:
            continue
        images.append(
            ImageAttachment(
                filename=candidate.name,
                content_type="image/png",
                data_base64=base64.b64encode(candidate.read_bytes()).decode("ascii"),
            )
        )
    return images, screenshot_dir


def _apply_score_gate(review: ReviewResult, *, floors: dict[str, int]) -> ReviewResult:
    failed = [
        f"{field}={getattr(review.score, field)} (minimum {minimum})"
        for field, minimum in floors.items()
        if getattr(review.score, field) < minimum
    ]
    hard_issues = bool(review.critical_issues or review.major_issues)
    if not failed and not hard_issues and review.verdict == "pass":
        return review
    additions = [f"quality threshold not met: {item}" for item in failed]
    score_todos = [
        f"Подтяни {item.split('=')[0]} по скриншотам: конкретная композиция, типографика или copy, не общий редизайн."
        for item in failed
    ]
    return review.model_copy(
        update={
            "verdict": "blocked" if review.verdict == "blocked" else "revise",
            "major_issues": [*review.major_issues, *additions],
            "recommended_fixes": [
                *review.recommended_fixes,
                *[f"Raise {item.split('=')[0]} above the review threshold" for item in failed],
            ],
            "todos": review.todos or score_todos,
        }
    )


class _PreviewReviewSkillBase(BaseSkill):
    _system_prompt: str
    _score_floors: dict[str, int] = {}

    async def execute(self, context: SkillContext) -> SkillResult:
        targets = context.arguments.get("targets") or [
            {"path": "/", "viewport": {"width": 1440, "height": 900}},
            {"path": "/", "viewport": {"width": 390, "height": 844}},
        ]
        raw = await self._run_preview(context, targets)
        if raw is None:
            return SkillResult(
                status="failed", summary="preview RPC timed out or worker unreachable"
            )

        try:
            preview = PreviewResult.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            return SkillResult(
                status="failed", summary=f"preview result did not match expected shape: {exc}"
            )

        if _is_infra_failure(preview):
            return SkillResult(
                status="failed",
                summary="preview infrastructure unavailable (not a content problem)",
                output={"preview": preview.model_dump(), "infra_failure": True},
            )

        gated = _deterministic_gate(preview)
        images, screenshot_dir = _load_screenshots(preview, workspace_root=context.workspace_root)
        brief_text = context.arguments.get("brief_text", "")
        user_text = (
            f"Бриф (цель и критерии приёмки):\n{brief_text}\n\n"
            "Результат автоматической проверки в браузере:\n"
            f"{preview.model_dump_json(indent=2)}\n\n"
            f"К ответу приложено скриншотов: {len(images)}. Сопоставь имя каждого PNG с "
            "screenshot_ref и viewport в JSON. Если скриншотов нет или одного из двух "
            "viewport не хватает, не считай визуальное качество доказанным."
        )

        usage_records: list[dict] = []
        review = await complete_structured(
            provider_name=context.arguments["provider_name"],
            model=context.arguments["model"],
            api_key=context.arguments["api_key"],
            system_prompt=self._system_prompt,
            user_text=user_text,
            response_model=ReviewResult,
            timeout_seconds=90,
            images=images,
            codex_workspace_root=screenshot_dir,
            codex_project_id=context.project_id,
            usage_sink=usage_records.append,
        )
        if review is None:
            review = gated or ReviewResult(
                verdict="revise", major_issues=["review call failed - treating as needs-revision"]
            )
        elif gated is not None:
            # Deterministic findings are merged into, never overridden by, the LLM verdict -
            # same rule product_pipeline.py's own gate follows.
            review = review.model_copy(
                update={
                    "critical_issues": [*review.critical_issues, *gated.critical_issues],
                    "major_issues": [*review.major_issues, *gated.major_issues],
                    "verdict": "blocked" if gated.verdict == "blocked" else review.verdict,
                }
            )
        review = _apply_score_gate(review, floors=self._score_floors)

        needs_fix = review.verdict in ("revise", "blocked") or bool(review.critical_issues)
        return SkillResult(
            status="partial" if needs_fix else "completed",
            summary=f"review verdict={review.verdict}",
            output={
                "preview": preview.model_dump(),
                "review": review.model_dump(),
                "usage": aggregate_usage(usage_records),
            },
        )

    async def _run_preview(self, context: SkillContext, targets: list[dict]) -> dict | None:
        import asyncio

        return await asyncio.to_thread(
            submit_control_job,
            action="preview",
            project_id=context.project_id,
            timeout_seconds=120,
            extra={"targets": targets},
        )


class VisualPreviewReviewSkill(_PreviewReviewSkillBase):
    _system_prompt = _REVIEW_SYSTEM_PROMPT
    _score_floors = {
        "product_completeness": 80,
        "visual_hierarchy": 82,
        "originality": 75,
        "content_quality": 80,
        "responsive_quality": 80,
        "accessibility": 75,
        "functional_honesty": 85,
    }
    definition = SkillDefinition(
        id="visual_preview_review",
        version="1.0",
        title="Visual preview review",
        description="Render the project in an isolated headless browser and have an independent reviewer assess it against the brief.",
        supported_roles=[SpecialistRole.QA_REVIEWER, SpecialistRole.UI_UX_SPECIALIST],
        supported_project_types=["website", "mixed"],
        input_schema={
            "targets": "list[{path,viewport}]",
            "brief_text": "string",
            "provider_name": "string",
            "model": "string",
            "api_key": "string",
        },
        output_schema={"preview": "object", "review": "object"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=2),
    )


class AccessibilityReviewSkill(_PreviewReviewSkillBase):
    _system_prompt = _ACCESSIBILITY_SYSTEM_PROMPT
    _score_floors = {
        "responsive_quality": 80,
        "accessibility": 85,
        "functional_honesty": 80,
    }
    definition = SkillDefinition(
        id="accessibility_review",
        version="1.0",
        title="Accessibility review",
        description="Same isolated preview as visual_preview_review, reviewed specifically for accessibility issues.",
        supported_roles=[SpecialistRole.QA_REVIEWER, SpecialistRole.UI_UX_SPECIALIST],
        supported_project_types=["website", "mixed"],
        input_schema={
            "targets": "list[{path,viewport}]",
            "brief_text": "string",
            "provider_name": "string",
            "model": "string",
            "api_key": "string",
        },
        output_schema={"preview": "object", "review": "object"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=2),
    )
