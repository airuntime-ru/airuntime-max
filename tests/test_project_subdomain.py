import pytest
from fastapi import HTTPException

from src.db.models.project import Project
from src.services.project_subdomain import (
    allocate_unique_subdomain,
    default_deploy_subdomain,
    ensure_deploy_subdomain,
    normalize_deploy_subdomain,
    resolve_deploy_subdomain,
    slugify,
    suggest_deploy_base,
    transliterate,
)


def test_normalize_deploy_subdomain_accepts_valid_value():
    assert normalize_deploy_subdomain("My-Landing") == "my-landing"


def test_normalize_deploy_subdomain_rejects_invalid_value():
    with pytest.raises(HTTPException):
        normalize_deploy_subdomain("bad_sub")


def test_normalize_deploy_subdomain_rejects_reserved():
    with pytest.raises(HTTPException):
        normalize_deploy_subdomain("api")


def test_resolve_deploy_subdomain_prefers_custom_value():
    project = Project(
        id="11111111-1111-4111-8111-111111111111",
        user_id="22222222-2222-4222-8222-222222222222",
        type="website",
        name="NTCN",
        description="",
        deploy_subdomain="ntcn",
    )
    assert resolve_deploy_subdomain(project) == "ntcn"
    assert default_deploy_subdomain(project).startswith("ntcn-")


def test_slugify_transliterates_cyrillic():
    assert slugify("Кофейня Утро") == "kofeynya-utro"
    assert transliterate("Щука") == "schuka"


def test_suggest_deploy_base_prefers_project_name():
    assert suggest_deploy_base(project_name="Morning Coffee") == "morning-coffee"


def test_suggest_deploy_base_from_prompt_when_name_generic():
    base = suggest_deploy_base(
        project_name="Новый проект",
        prompt="Сделай сайт для кофейни «Утренний эспрессо» с меню",
    )
    assert base == "utrenniy-espresso"


def test_suggest_deploy_base_from_prompt_tokens():
    base = suggest_deploy_base(
        project_name="Untitled",
        prompt="Сделай сайт студии йоги Lotus Flow",
    )
    assert base == "studii-yogi-lotus"


def test_allocate_unique_subdomain_tries_suffixes(monkeypatch):
    taken = {"coffee", "coffee-2"}

    def fake_available(db, subdomain, *, exclude_project_id=None):
        return subdomain not in taken

    monkeypatch.setattr("src.services.project_subdomain.is_subdomain_available", fake_available)
    result = allocate_unique_subdomain(object(), "coffee")
    assert result == "coffee-3"


def test_ensure_deploy_subdomain_persists_friendly_slug(monkeypatch):
    project = Project(
        id="11111111-1111-4111-8111-111111111111",
        user_id="22222222-2222-4222-8222-222222222222",
        type="website",
        name="Кофейня",
        description="",
        deploy_subdomain=None,
    )

    class _EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class _FakeDb:
        def query(self, model):
            return _EmptyQuery()

        def add(self, obj):
            self.added = obj

    db = _FakeDb()
    allocated = ensure_deploy_subdomain(db, project, prompt="сайт кофейни")
    assert allocated == "kofeynya"
    assert project.deploy_subdomain == "kofeynya"


def test_ensure_deploy_subdomain_skips_bots():
    project = Project(
        id="11111111-1111-4111-8111-111111111111",
        user_id="22222222-2222-4222-8222-222222222222",
        type="telegram_bot",
        name="My Bot",
        description="",
        deploy_subdomain=None,
    )

    class _FakeDb:
        def query(self, model):
            raise AssertionError("should not query")

        def add(self, obj):
            raise AssertionError("should not add")

    assert ensure_deploy_subdomain(_FakeDb(), project) is None
    assert project.deploy_subdomain is None
