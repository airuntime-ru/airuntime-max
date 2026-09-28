"""Assertions on website/mixed agent system prompts (architecture + design + bot secrets)."""

import uuid

from src.db.models.project import Project
from src.services.agent.prompt import build_system_prompt


def _project(project_type: str) -> Project:
    return Project(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        type=project_type,
        name="Prompt Check",
        description="",
    )


def test_website_prompt_requires_multipage_for_rich_products():
    prompt = build_system_prompt(_project("website"))
    assert "public/index.html" in prompt
    assert "Feature-rich" in prompt or "feature-rich" in prompt.lower()
    assert "несколько страниц" in prompt
    assert "бесконечный index.html" in prompt
    assert "Лендинг ≠ кабинет" in prompt or "лендинг" in prompt.lower()


def test_website_prompt_steers_premium_visual_not_ai_cliche():
    prompt = build_system_prompt(_project("website"))
    assert "Inter/Roboto" in prompt or "Inter" in prompt
    assert "full-bleed" in prompt
    assert "purple" in prompt.lower()
    assert "продаваемый" in prompt


def test_website_and_mixed_prompt_ban_bot_settings_ui_on_public_site():
    for project_type in ("website", "mixed"):
        prompt = build_system_prompt(_project(project_type))
        assert "request_secret" in prompt
        assert "TELEGRAM_BOT_TOKEN" in prompt
        assert "настройка бота" in prompt


def test_simple_landing_may_stay_one_page():
    prompt = build_system_prompt(_project("website"))
    assert "Простой лендинг" in prompt
    assert "один public/index.html" in prompt
    assert "статический HTML" in prompt or "HTML/CSS/JS" in prompt


def test_website_prompt_defaults_rich_sites_to_postgres_not_sqlite():
    prompt = build_system_prompt(_project("website"))
    assert "request_service(kind='postgres')" in prompt or "request_service" in prompt
    assert "postgres" in prompt.lower()
    assert "не SQLite-в-контейнере" in prompt or "SQLite-в-контейнере" in prompt
    assert "по умолчанию достаточно SQLite" not in prompt
    assert "FastAPI" in prompt or "бэкенд" in prompt.lower()
    assert "React/Vue" in prompt or "Vue, React" in prompt
    assert "не фиксирует Python/HTML/SQLite" in prompt or "не требует React/Vue/FastAPI" in prompt


def test_quality_rules_forbid_endless_index_for_rich_sites():
    prompt = build_system_prompt(_project("website"))
    assert "бесконечный" in prompt
    assert "index.html" in prompt
