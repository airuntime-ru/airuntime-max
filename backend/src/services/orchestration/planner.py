"""Builds a versioned ExecutionPlan (a bounded DAG, not the old orchestrator.py's flat
{"subtasks":[...]} list) and structurally validates it before anything downstream ever sees it.

Reuses `pipeline_llm.complete_structured` verbatim for the actual model call - it already does
exactly what a planner call needs (JSON-schema-constrained one-shot completion, one repair
round-trip on a bad response, `None` on total failure) for the product pipeline's brief/UX/
visual/review calls, and `ExecutionPlan` is just another Pydantic response_model to it.

Never returns "no plan" - a call that fails outright, or whose result never becomes
structurally valid even after one LLM repair round-trip, degrades to a single-task plan
(role=Implementer, goal=the raw request) rather than blocking the run. Every request, however
small, ends up going through the same plan -> contract -> validation pipeline (spec section 4:
"Для маленького запроса система может создать одну задачу, но она всё равно должна проходить
через единый execution contract и validation").

The planner never assigns tools/skills/paths directly to a task beyond *suggestions*
(PlannedTask.suggested_skills, .relevant_paths, .required_capabilities) - role_policy.py and
contract_builder.py are what actually compute a task's rights, always as a subset of what the
plan asked for, never a superset.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from src.core.config import settings
from src.services.agent.pipeline_llm import complete_structured
from src.services.orchestration.role_policy import ROLE_REGISTRY
from src.services.orchestration.schemas import (
    AcceptanceCriterion,
    Complexity,
    ExecutionBudget,
    ExecutionPlan,
    PlannedTask,
    SpecialistRole,
)
from src.services.orchestration.trace import emit_trace

logger = logging.getLogger(__name__)

PlanSource = Literal[
    "heuristic_simple", "llm", "llm_repaired", "fallback_llm_failed", "fallback_invalid"
]

# Below this length, skip the planning call entirely - same philosophy as the old
# orchestrator.py's _MIN_MESSAGE_CHARS_FOR_PLANNING gate (120), a little more generous since a
# short request still gets a *real* single-task plan now, not just "don't decompose".
_TRIVIAL_MESSAGE_CHARS = 160

_VISUAL_WEBSITE_TERMS = (
    "website",
    "landing",
    "site",
    "frontend",
    "homepage",
    "dashboard",
    "сайт",
    "лендинг",
    "страниц",
    "интерфейс",
    "дизайн",
    "витрин",
    "каталог",
    "дашборд",
    "кабинет",
)
_PRODUCT_ACTION_TERMS = (
    "build",
    "create",
    "develop",
    "implement",
    "add",
    "fix",
    "создай",
    "сделай",
    "разработ",
    "реализ",
    "добав",
    "исправ",
    "передел",
    "обнов",
    "запусти",
    "почин",
    "настрой",
    "перепиш",
    "замени",
    "удали",
)
_NONVISUAL_PRODUCT_TERMS = (
    "api",
    "backend",
    "database",
    "telegram",
    "bot",
    "webhook",
    "auth",
    "payment",
    "бэк",
    "база",
    "бот",
    "телеграм",
    "вебхук",
    "авторизац",
    "регистрац",
    "оплат",
    "интеграц",
    "парсер",
    "очеред",
)

_PLANNING_SYSTEM_PROMPT_TEMPLATE = """Ты - планировщик платформы AIRuntime. По запросу
пользователя и текущему состоянию проекта построй ExecutionPlan - ограниченный граф задач, а
НЕ линейный список.

Доступные роли (SpecialistRole) и их назначение:
{role_catalog}

Правила:
- Каждая задача (PlannedTask) должна иметь уникальный local_id (короткая ASCII-строка вроде
  "backend_api", "landing_page").
- execution_preference is a capability-routing hint only. Use exactly deterministic_skill,
  specialist_agent, or either. Never use parallel/sequential here; scheduling is derived from
  dependencies and relevant_paths.
- dependencies - список local_id других задач ЭТОГО плана, от которых зависит задача. Не
  создавай циклов.
- Задачи без общих file-путей и с execution_preference, допускающим параллельность, можно
  оставить независимыми (пустой dependencies) - это разрешит платформе выполнить их параллельно
  (read-only задачи всегда параллельны; write-задачи - только через изолированный worktree,
  который платформа создаст сама, если докажет независимость путей).
- НЕ назначай инструменты (tools) напрямую - это делает платформа на основе роли.
- Не создавай более {max_tasks} задач.
- Если запрос по сути один маленький кусок работы - создай ОДНУ задачу, это нормально.
- Поля title/goal/reason/description объектов пиши по-русски (это текст для пользователя и для
  исполнителя), но имена JSON-полей и enum-значения (role, execution_preference, write_scope,
  risk_level, verification_method) оставляй ровно как в схеме - только на английском.
- complexity: "simple" для одной задачи, "compound" для нескольких связанных задач одного
  проекта, "large" для многомодульной работы (например сайт + отдельный Telegram-бот).
- risks - список объектов {{"description": "...", "severity": "low|medium|high|critical"}},
  никогда не список строк. Пустой список [] допустим.
- final_acceptance_criteria - список объектов AcceptanceCriterion (id/description/
  verification_method), не строки.
- Каждая задача должна нести хотя бы один acceptance_criterion, проверяемый одним из методов:
  build, test, preview, runtime, manual, llm_review.
- Every acceptance_criterion must have a non-empty unique ASCII id.
- Для нового website/mixed-проекта обязательно включай в relevant_paths как минимум
  "Dockerfile" и "public/index.html" (а также остальные нужные файлы). Платформа публикует
  сайты из public/index.html и собирает каждый проект через Dockerfile; одного корневого
  index.html недостаточно.
- Для проверки ещё не задеплоенного сайта используй verification_method="preview": платформа
  сама поднимет собранный образ во временной изолированной сети. verification_method="runtime"
  оставляй только для задач, проверяющих уже существующий production-деплой.
- Новый сайт, лендинг или заметная UI-переделка НЕ являются trivial-задачей, даже если запрос
  короткий. План обязан включать: (1) реализацию с явным арт-дирекшном/дизайн-системой и
  проверяемыми критериями для hero, типографики, контента и mobile; (2) зависимую read-only
  задачу QAReviewer с suggested_skills=["visual_preview_review"], которая смотрит реальные
  скриншоты 1440x900 и 390x844. QA нельзя объединять с автором реализации.
- В acceptance criteria визуальной реализации фиксируй не "красиво", а наблюдаемые свойства:
  специфичный для индустрии concept, display/body typography, палитра-токены, один главный CTA
  выше сгиба, осмысленный контент, композиционный ритм секций, отсутствие generic card-grid,
  корректный mobile и preview без ошибок.
- Для Telegram-бота/backend/API/mixed после задач записи добавляй зависимую QAReviewer-задачу
  с suggested_skills=["product_quality_review"]. Она независимо сверяет реальные файлы и
  результаты build/test с acceptance criteria, сценариями, состояниями ошибок, архитектурой,
  секретами и functional honesty. Автор реализации не может быть своим QA.
- Mixed-проект требует ОБА контура, когда меняются обе поверхности: visual_preview_review для
  сайта и product_quality_review для бота/backend/интеграции.
- Не планируй циклы «QA отклонил → переписать всё → снова QA». Судья вернёт короткий TODO-список,
  платформа сама отдаст его исполнителю на один-два прогона и поставит результат.

Минимальный пример валидного JSON (одна задача):
{{
  "goal": "Кратко цель",
  "complexity": "simple",
  "tasks": [
    {{
      "local_id": "implement",
      "title": "Сделать запрошенное",
      "role": "implementer",
      "goal": "Реализовать запрос пользователя",
      "reason": "Основная работа",
      "execution_preference": "either",
      "dependencies": [],
      "relevant_paths": ["public/index.html"],
      "suggested_skills": [],
      "required_capabilities": [],
      "acceptance_criteria": [
        {{
          "id": "done",
          "description": "Изменения на месте и проверяемы",
          "verification_method": "manual"
        }}
      ],
      "risk_level": "low",
      "write_scope": "scoped_paths"
    }}
  ],
  "final_acceptance_criteria": [],
  "risks": [],
  "estimated_budget": {{}}
}}
"""


def _role_catalog_text() -> str:
    lines = []
    for role in SpecialistRole:
        policy = ROLE_REGISTRY[role]
        lines.append(
            f"- {role.value}: {policy.title} - {policy.system_prompt.splitlines()[0][:140]}"
        )
    return "\n".join(lines)


def build_planning_system_prompt(*, max_tasks: int) -> str:
    return _PLANNING_SYSTEM_PROMPT_TEMPLATE.format(
        role_catalog=_role_catalog_text(), max_tasks=max_tasks
    )


def derive_complexity_from_task_count(task_count: int) -> Complexity:
    if task_count <= 1:
        return "simple"
    if task_count <= 4:
        return "compound"
    return "large"


def _is_visual_website_request(user_message: str, project_context_summary: str) -> bool:
    context = project_context_summary.lower()
    is_website = "(website)" in context or "(mixed)" in context
    message = user_message.lower()
    return is_website and any(term in message for term in _VISUAL_WEBSITE_TERMS)


def _project_type_from_context(project_context_summary: str) -> str | None:
    context = project_context_summary.lower()
    for project_type in ("telegram_bot", "website", "mixed"):
        if f"({project_type})" in context:
            return project_type
    return None


def _is_product_build_request(user_message: str, project_context_summary: str) -> bool:
    if _project_type_from_context(project_context_summary) is None:
        return False
    message = user_message.lower()
    has_action = any(term in message for term in _PRODUCT_ACTION_TERMS)
    # The project context already identifies the product surface. Requiring the user to repeat
    # "bot/site/backend" here made terse follow-ups such as "добавь /help" bypass planning and
    # independent QA precisely when regressions are most likely.
    return has_action


def _preview_criterion(task: PlannedTask) -> AcceptanceCriterion:
    existing_ids = {criterion.id for criterion in task.acceptance_criteria}
    base = f"{task.local_id}_visual_preview"
    criterion_id = base
    suffix = 2
    while criterion_id in existing_ids:
        criterion_id = f"{base}_{suffix}"
        suffix += 1
    return AcceptanceCriterion(
        id=criterion_id,
        description=(
            "Реальные desktop 1440x900 и mobile 390x844 preview подтверждают цельный "
            "арт-дирекшн, ясную визуальную иерархию, один доминирующий CTA, предметный "
            "контент и отсутствие ошибок, битых изображений и horizontal overflow"
        ),
        verification_method="preview",
    )


def ensure_website_quality_plan(plan: ExecutionPlan, *, max_tasks: int) -> ExecutionPlan:
    """Server-side quality invariant for visible website work.

    The planner may suggest a good workflow, but production must not depend on it remembering
    to separate author and judge. Add a real preview criterion to the last writing task and a
    dependent visual-review task whenever the plan has room.
    """
    tasks = list(plan.tasks)
    write_roles = {
        SpecialistRole.IMPLEMENTER,
        SpecialistRole.UI_UX_SPECIALIST,
        SpecialistRole.INTEGRATION_AGENT,
    }
    write_tasks = [task for task in tasks if task.role in write_roles]
    if not write_tasks:
        return plan

    implementation = write_tasks[-1]
    if not any(c.verification_method == "preview" for c in implementation.acceptance_criteria):
        updated = implementation.model_copy(
            update={
                "acceptance_criteria": [
                    *implementation.acceptance_criteria,
                    _preview_criterion(implementation),
                ]
            }
        )
        tasks[tasks.index(implementation)] = updated
        implementation = updated

    has_visual_qa = any(
        task.role == SpecialistRole.QA_REVIEWER and "visual_preview_review" in task.suggested_skills
        for task in tasks
    )
    if has_visual_qa or len(tasks) >= max_tasks:
        return plan.model_copy(
            update={"tasks": tasks, "complexity": derive_complexity_from_task_count(len(tasks))}
        )

    local_id = "visual_qa"
    suffix = 2
    existing_ids = {task.local_id for task in tasks}
    while local_id in existing_ids:
        local_id = f"visual_qa_{suffix}"
        suffix += 1
    dependency_ids = [task.local_id for task in write_tasks]
    tasks.append(
        PlannedTask(
            local_id=local_id,
            title="Независимая визуальная приёмка desktop/mobile",
            role=SpecialistRole.QA_REVIEWER,
            goal=(
                "Проверить готовый интерфейс по реальным скриншотам 1440x900 и 390x844 как "
                "коммерческий арт-директор; вернуть FAIL/правки при шаблонности, слабой "
                "иерархии, плохом контенте, переполнениях или браузерных ошибках"
            ),
            reason="Автор интерфейса не должен сам принимать собственный визуальный результат",
            execution_preference="deterministic_skill",
            dependencies=dependency_ids,
            suggested_skills=["visual_preview_review"],
            relevant_paths=[],
            write_scope="none",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id=f"{local_id}_desktop_mobile",
                    description=(
                        "Независимый visual review видит оба viewport и подтверждает пороги "
                        "visual hierarchy, originality, content, responsiveness и accessibility"
                    ),
                    verification_method="preview",
                )
            ],
        )
    )
    return plan.model_copy(
        update={"tasks": tasks, "complexity": derive_complexity_from_task_count(len(tasks))}
    )


def ensure_product_quality_plan(
    plan: ExecutionPlan,
    *,
    max_tasks: int,
    project_type: str,
    visual_quality: bool,
    user_message: str,
) -> ExecutionPlan:
    """Apply author/judge separation to every product surface.

    Websites get screenshot-based art direction review. Bots, backends and integrations get a
    redacted source/evidence review. Mixed or feature-rich website requests may receive both.
    """
    quality_plan = (
        ensure_website_quality_plan(plan, max_tasks=max_tasks) if visual_quality else plan
    )
    tasks = list(quality_plan.tasks)
    write_roles = {
        SpecialistRole.IMPLEMENTER,
        SpecialistRole.UI_UX_SPECIALIST,
        SpecialistRole.INTEGRATION_AGENT,
    }
    write_tasks = [task for task in tasks if task.role in write_roles]
    if not write_tasks:
        return quality_plan

    message = user_message.lower()
    needs_product_review = (
        project_type in ("telegram_bot", "mixed")
        or not visual_quality
        or any(term in message for term in _NONVISUAL_PRODUCT_TERMS)
    )
    has_product_qa = any(
        task.role == SpecialistRole.QA_REVIEWER
        and "product_quality_review" in task.suggested_skills
        for task in tasks
    )
    if not needs_product_review or has_product_qa or len(tasks) >= max_tasks:
        return quality_plan

    local_id = "product_qa"
    suffix = 2
    existing_ids = {task.local_id for task in tasks}
    while local_id in existing_ids:
        local_id = f"product_qa_{suffix}"
        suffix += 1
    tasks.append(
        PlannedTask(
            local_id=local_id,
            title="Независимая функциональная приёмка продукта",
            role=SpecialistRole.QA_REVIEWER,
            goal=(
                "Независимо проверить реализацию по брифу и acceptance criteria: реальные "
                "сценарии, архитектуру, валидацию, error states, секреты и отсутствие "
                "TODO/stub/fake functionality"
            ),
            reason="Функциональную полноту и честность должен проверять не автор реализации",
            execution_preference="deterministic_skill",
            dependencies=[task.local_id for task in write_tasks],
            suggested_skills=["product_quality_review"],
            relevant_paths=[],
            write_scope="none",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id=f"{local_id}_acceptance",
                    description=(
                        "Независимый product review подтверждает completeness, architecture, "
                        "error handling и functional honesty без критических/major issues"
                    ),
                    verification_method="llm_review",
                )
            ],
        )
    )
    return quality_plan.model_copy(
        update={"tasks": tasks, "complexity": derive_complexity_from_task_count(len(tasks))}
    )


def build_single_task_plan(
    user_message: str,
    *,
    reason: str,
    website_quality: bool = False,
    project_type: str | None = None,
    max_tasks: int | None = None,
) -> ExecutionPlan:
    goal = user_message.strip()[:2000] or "Выполнить запрос пользователя"
    plan = ExecutionPlan(
        goal=goal,
        complexity="simple",
        tasks=[
            PlannedTask(
                local_id="main",
                title=goal[:120],
                role=SpecialistRole.IMPLEMENTER,
                goal=goal,
                reason=reason,
                execution_preference="either",
                dependencies=[],
                relevant_paths=["Dockerfile", "public/index.html"] if website_quality else [],
                write_scope="full_workspace",
                # Without at least one criterion, contract_builder.score_task_contract() can
                # never pass this task's contract (it hard-requires acceptance_criteria to be
                # non-empty) - every heuristic/fallback plan would dead-end into a replan loop
                # before ever reaching an executor. "build" is the one verification method every
                # website/bot task can be checked against regardless of what the request asked.
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="main_goal_met",
                        description=f"Запрос пользователя выполнен: {goal[:200]}",
                        verification_method="build",
                    ),
                ],
            )
        ],
        final_acceptance_criteria=[],
        risks=[],
        estimated_budget=ExecutionBudget(
            max_attempts_per_task=settings.orchestration_max_task_attempts
        ),
    )
    if website_quality:
        return ensure_product_quality_plan(
            plan,
            max_tasks=max_tasks or settings.orchestration_max_plan_tasks,
            project_type=project_type or "website",
            visual_quality=True,
            user_message=user_message,
        )
    if project_type is not None:
        return ensure_product_quality_plan(
            plan,
            max_tasks=max_tasks or settings.orchestration_max_plan_tasks,
            project_type=project_type,
            visual_quality=False,
            user_message=user_message,
        )
    return plan


def find_dependency_cycle(tasks: list[PlannedTask]) -> list[str] | None:
    """DFS-based cycle detection. Returns the cyclic path (local_ids) if one exists, else None.
    Dangling dependency references (pointing at a local_id that doesn't exist) are ignored here
    and reported separately by `validate_plan_structure`."""
    graph = {t.local_id: t.dependencies for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if color[dep] == GRAY:
                cycle_start = path.index(dep)
                return [*path[cycle_start:], dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for local_id in graph:
        if color[local_id] == WHITE:
            found = visit(local_id)
            if found:
                return found
    return None


def validate_plan_structure(plan: ExecutionPlan, *, max_tasks: int) -> list[str]:
    """Structural validation only (graph shape) - role/tool/path *rights* are re-derived by
    role_policy.py regardless of what the plan says, so this does not need to (and must not)
    try to enforce policy itself."""
    errors: list[str] = []

    if len(plan.tasks) > max_tasks:
        errors.append(f"plan has {len(plan.tasks)} tasks, exceeding the limit of {max_tasks}")

    seen_ids: set[str] = set()
    for task in plan.tasks:
        if task.local_id in seen_ids:
            errors.append(f"duplicate local_id: {task.local_id!r}")
        seen_ids.add(task.local_id)

    for task in plan.tasks:
        for dep in task.dependencies:
            if dep not in seen_ids:
                errors.append(f"task {task.local_id!r} depends on unknown local_id {dep!r}")

    cycle = find_dependency_cycle(plan.tasks)
    if cycle:
        errors.append(f"dependency cycle detected: {' -> '.join(cycle)}")

    if plan.complexity == "simple" and len(plan.tasks) > 1:
        errors.append("complexity=simple but plan declares more than one task")

    if not plan.tasks:
        errors.append("plan has no tasks")

    return errors


@dataclass
class PlanGenerationResult:
    plan: ExecutionPlan
    source: PlanSource
    errors: list[str] = field(default_factory=list)


def _emit_plan_trace(
    db: Session | None,
    run_id: object | None,
    result: PlanGenerationResult,
    *,
    duration_ms: int,
) -> None:
    if db is None or run_id is None:
        return
    error_summary = "; ".join(result.errors[:5]) if result.errors else "none"
    emit_trace(
        db,
        run_id=run_id,
        category="planning",
        action="generate_plan",
        summary=(
            f"plan source={result.source} tasks={len(result.plan.tasks)} "
            f"errors={len(result.errors)}"
        ),
        duration_ms=duration_ms,
        details={
            "source": result.source,
            "task_count": len(result.plan.tasks),
            "errors": result.errors[:10],
            "error_summary": error_summary,
        },
    )


async def generate_plan(
    *,
    user_message: str,
    project_context_summary: str,
    provider_name: str,
    model: str,
    api_key: str,
    max_tasks: int | None = None,
    timeout_seconds: int | None = None,
    usage_sink: Callable[[dict[str, Any]], None] | None = None,
    db: Session | None = None,
    run_id: object | None = None,
) -> PlanGenerationResult:
    started = time.monotonic()
    max_tasks = max_tasks or settings.orchestration_max_plan_tasks
    # Codex cold-starts a container per planning call - 90s was cutting off before any JSON
    # arrived, which forced endless single-task fallbacks and then "replan limit reached".
    timeout_seconds = timeout_seconds or settings.codex_simple_timeout_seconds
    project_type = _project_type_from_context(project_context_summary)
    website_quality = _is_visual_website_request(user_message, project_context_summary)
    product_quality = _is_product_build_request(user_message, project_context_summary)

    if (
        len(user_message.strip()) < _TRIVIAL_MESSAGE_CHARS
        and not website_quality
        and not product_quality
    ):
        result = PlanGenerationResult(
            plan=build_single_task_plan(user_message, reason="short request - planning skipped"),
            source="heuristic_simple",
        )
        _emit_plan_trace(db, run_id, result, duration_ms=int((time.monotonic() - started) * 1000))
        return result

    system_prompt = build_planning_system_prompt(max_tasks=max_tasks)
    user_text = (
        f"Состояние проекта:\n{project_context_summary}\n\nЗапрос пользователя:\n{user_message}"
    )
    logger.info(
        "planner: starting LLM plan generation (timeout=%ss model=%s)",
        timeout_seconds,
        model,
    )

    raw_plan = await complete_structured(
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        system_prompt=system_prompt,
        user_text=user_text,
        response_model=ExecutionPlan,
        timeout_seconds=timeout_seconds,
        usage_sink=usage_sink,
        trace_db=db,
        trace_run_id=run_id,
        trace_category="planning",
    )
    if raw_plan is None:
        logger.info("planner: LLM call produced no usable plan, falling back to single-task plan")
        result = PlanGenerationResult(
            plan=build_single_task_plan(
                user_message,
                reason="planner call failed",
                website_quality=website_quality,
                project_type=project_type if product_quality else None,
                max_tasks=max_tasks,
            ),
            source="fallback_llm_failed",
            errors=["planner_call_failed"],
        )
        _emit_plan_trace(db, run_id, result, duration_ms=int((time.monotonic() - started) * 1000))
        return result

    errors = validate_plan_structure(raw_plan, max_tasks=max_tasks)
    if not errors:
        quality_plan = (
            ensure_product_quality_plan(
                raw_plan,
                max_tasks=max_tasks,
                project_type=project_type,
                visual_quality=website_quality,
                user_message=user_message,
            )
            if project_type is not None
            else raw_plan
        )
        result = PlanGenerationResult(plan=quality_plan, source="llm")
        _emit_plan_trace(db, run_id, result, duration_ms=int((time.monotonic() - started) * 1000))
        return result

    logger.info(
        "planner: LLM plan failed structural validation (%s), asking for one repair", errors
    )
    repair_text = (
        f"{user_text}\n\n--- Твой предыдущий план не прошёл структурную проверку ---\n"
        f"Ошибки: {'; '.join(errors)}\nИсправь именно граф зависимостей/local_id и верни план заново."
    )
    repaired_plan = await complete_structured(
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        system_prompt=system_prompt,
        user_text=repair_text,
        response_model=ExecutionPlan,
        timeout_seconds=timeout_seconds,
        usage_sink=usage_sink,
        trace_db=db,
        trace_run_id=run_id,
        trace_category="planning",
    )
    if repaired_plan is not None:
        repaired_errors = validate_plan_structure(repaired_plan, max_tasks=max_tasks)
        if not repaired_errors:
            quality_plan = (
                ensure_product_quality_plan(
                    repaired_plan,
                    max_tasks=max_tasks,
                    project_type=project_type,
                    visual_quality=website_quality,
                    user_message=user_message,
                )
                if project_type is not None
                else repaired_plan
            )
            result = PlanGenerationResult(plan=quality_plan, source="llm_repaired")
            _emit_plan_trace(
                db, run_id, result, duration_ms=int((time.monotonic() - started) * 1000)
            )
            return result
        errors = repaired_errors

    logger.warning(
        "planner: plan invalid even after repair (%s), falling back to single-task plan", errors
    )
    result = PlanGenerationResult(
        plan=build_single_task_plan(
            user_message,
            reason="plan validation failed twice",
            website_quality=website_quality,
            project_type=project_type if product_quality else None,
            max_tasks=max_tasks,
        ),
        source="fallback_invalid",
        errors=errors,
    )
    _emit_plan_trace(db, run_id, result, duration_ms=int((time.monotonic() - started) * 1000))
    return result
