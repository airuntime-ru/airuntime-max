"""Tests for services/orchestration/context_engine.py - all pure/deterministic (no LLM), plus
the few DB- and filesystem-touching pieces against the real test DB / a tmp_path workspace."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from src.db.models.project import Project
from src.db.models.project_service import ProjectService
from src.db.models.secret import Secret
from src.db.models.user import User
from src.services.orchestration.context_engine import (
    ContextCache,
    ContextEngine,
    clip_text,
    estimate_tokens,
    extract_structured_error,
    redact,
    select_relevant_files,
)


def test_estimate_tokens_grows_with_length() -> None:
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 1


def test_clip_text_keeps_tail_by_default() -> None:
    text = "0123456789" * 10
    clipped = clip_text(text, max_chars=20)
    assert clipped.endswith(text[-20:])
    assert "clipped" in clipped


def test_clip_text_noop_when_under_limit() -> None:
    assert clip_text("short", max_chars=100) == "short"


def test_redact_scrubs_secret_looking_values() -> None:
    text = (
        "connecting with DATABASE_URL=postgres://u:sup3rSecret@host/db and api_key: sk-abcdef123456"
    )
    redacted = redact(text)
    assert "sup3rSecret" not in redacted
    assert "sk-abcdef123456" not in redacted
    assert "[REDACTED]" in redacted


class TestExtractStructuredError:
    def test_python_traceback(self) -> None:
        log = (
            "some info line\n"
            "Traceback (most recent call last):\n"
            '  File "app.py", line 12, in <module>\n'
            "    raise ValueError('bad thing')\n"
            "ValueError: bad thing\n"
        )
        result = extract_structured_error(log)
        assert result is not None
        assert result.error_type == "python_traceback"
        assert "ValueError" in result.summary
        assert "app.py" in result.file_hints

    def test_npm_error(self) -> None:
        log = "npm WARN deprecated\nnpm ERR! missing script: build\n"
        result = extract_structured_error(log)
        assert result is not None
        assert result.error_type == "npm_error"
        assert "missing script" in result.summary

    def test_clean_log_returns_none(self) -> None:
        assert extract_structured_error("Server started on port 80\nHealthy\n") is None

    def test_empty_log_returns_none(self) -> None:
        assert extract_structured_error("") is None

    def test_redacts_before_extracting(self) -> None:
        log = "Traceback (most recent call last):\nConnectionError: password=hunter2 refused\n"
        result = extract_structured_error(log)
        assert result is not None
        assert "hunter2" not in result.raw_excerpt


class TestSelectRelevantFiles:
    def test_path_hint_beats_keyword_overlap(self) -> None:
        files = ["src/unrelated.py", "backend/auth/login.py", "backend/auth/logout.py", "readme.md"]
        chosen = select_relevant_files(
            files, goal_text="fix the bug", relevant_path_hints=["backend/auth"], limit=2
        )
        assert set(chosen) == {"backend/auth/login.py", "backend/auth/logout.py"}

    def test_keyword_overlap_when_no_hints(self) -> None:
        files = ["telegram_bot.py", "landing_page.html", "utils.py"]
        chosen = select_relevant_files(files, goal_text="update the telegram bot handler", limit=1)
        assert chosen == ["telegram_bot.py"]

    def test_stable_and_capped(self) -> None:
        files = [f"file_{i}.py" for i in range(50)]
        chosen = select_relevant_files(files, goal_text="", limit=5)
        assert len(chosen) == 5
        assert chosen == select_relevant_files(files, goal_text="", limit=5)


class TestContextCache:
    def test_hit_and_miss(self) -> None:
        cache = ContextCache(max_entries=10)
        assert cache.get("p1", "sha1", "files") is None
        cache.set("p1", "sha1", "files", ["a.py"])
        assert cache.get("p1", "sha1", "files") == ["a.py"]

    def test_new_git_sha_is_automatic_invalidation(self) -> None:
        cache = ContextCache(max_entries=10)
        cache.set("p1", "sha1", "files", ["a.py"])
        assert cache.get("p1", "sha2", "files") is None, "a new commit must be a cache miss"

    def test_lru_eviction(self) -> None:
        cache = ContextCache(max_entries=2)
        cache.set("p1", "sha", "a", 1)
        cache.set("p1", "sha", "b", 2)
        cache.set("p1", "sha", "c", 3)  # evicts "a"
        assert cache.get("p1", "sha", "a") is None
        assert cache.get("p1", "sha", "b") == 2
        assert cache.get("p1", "sha", "c") == 3


@pytest.fixture()
def project(db: Session) -> Project:
    user = User(email=f"{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, type="website", name="Ctx test")
    db.add(project)
    db.flush()
    return project


class TestContextEngineInventories:
    def test_secrets_inventory_returns_keys_only(self, db: Session, project: Project) -> None:
        db.add(
            Secret(project_id=project.id, key="TELEGRAM_BOT_TOKEN", encrypted_value="cipher-bytes")
        )
        db.add(Secret(project_id=project.id, key="STRIPE_SECRET_KEY", encrypted_value=None))
        db.flush()

        engine = ContextEngine(db)
        inventory = engine.secrets_inventory(project.id)
        assert inventory == ["STRIPE_SECRET_KEY", "TELEGRAM_BOT_TOKEN"]
        for key in inventory:
            assert "cipher-bytes" not in key

    def test_unfilled_secret_keys(self, db: Session, project: Project) -> None:
        db.add(Secret(project_id=project.id, key="FILLED", encrypted_value="x"))
        db.add(Secret(project_id=project.id, key="EMPTY", encrypted_value=None))
        db.flush()

        engine = ContextEngine(db)
        assert engine.unfilled_secret_keys(project.id) == ["EMPTY"]

    def test_services_inventory_returns_kinds_only(self, db: Session, project: Project) -> None:
        db.add(
            ProjectService(
                project_id=project.id,
                kind="postgres",
                image="postgres:16-alpine",
                container_name="c1",
                volume_name="v1",
                encrypted_credentials="secret-blob",
            )
        )
        db.flush()

        engine = ContextEngine(db)
        assert engine.services_inventory(project.id) == ["postgres"]


class TestBuildRelevantFiles:
    def test_reads_and_clips_matched_files(self, db: Session, project: Project, tmp_path) -> None:
        (tmp_path / "app.py").write_text("print('hello world')\n" * 5, encoding="utf-8")
        (tmp_path / "unrelated.py").write_text("x = 1\n", encoding="utf-8")

        engine = ContextEngine(db)
        refs = engine.build_relevant_files(
            tmp_path,
            available_files=["app.py", "unrelated.py"],
            goal_text="fix app startup",
            relevant_path_hints=["app.py"],
            limit=1,
            max_chars_per_file=20,
        )
        assert len(refs) == 1
        assert refs[0].path == "app.py"
        assert refs[0].truncated is True
        assert len(refs[0].content) <= 60  # 20 chars + clip marker

    def test_binary_file_marked_unreadable_not_crashed(
        self, db: Session, project: Project, tmp_path
    ) -> None:
        (tmp_path / "image.bin").write_bytes(b"\xff\xd8\xff\xe0\x00\x10\x00\x00")
        engine = ContextEngine(db)
        refs = engine.build_relevant_files(
            tmp_path,
            available_files=["image.bin"],
            goal_text="",
            relevant_path_hints=["image.bin"],
            limit=1,
        )
        assert refs[0].content is None


class TestGlobalContextSummary:
    def test_summary_includes_key_sections_and_redacts(
        self, db: Session, project: Project, tmp_path
    ) -> None:
        from src.services.project_git import commit_snapshot, init_repo_if_needed

        init_repo_if_needed(tmp_path)
        (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
        commit_snapshot(tmp_path, message="initial")

        db.add(Secret(project_id=project.id, key="TELEGRAM_BOT_TOKEN", encrypted_value="x"))
        db.flush()

        engine = ContextEngine(db)
        summary = engine.build_global_context_summary(
            original_request="build me a landing page with token=sk-shouldnotleak",
            project=project,
            workspace_root=tmp_path,
            git_sha=None,
            plan_goal="Ship a landing page",
            task_summaries=["implementer: created index.html"],
        )
        assert "TELEGRAM_BOT_TOKEN" in summary
        assert "Ship a landing page" in summary
        assert "sk-shouldnotleak" not in summary
        assert "implementer: created index.html" in summary
