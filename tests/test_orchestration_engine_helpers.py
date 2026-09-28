"""Unit tests for small orchestration engine helpers (no LLM, minimal DB)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.db.models.chat import Chat
from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration import engine
from src.services.orchestration.repository import OrchestrationRunRepository
from src.services.orchestration.schemas import TaskEvidence


def _task(*, role: str, status: str, evidence: TaskEvidence | None = None) -> SimpleNamespace:
    evidence_json = evidence.model_dump_json() if evidence is not None else None
    return SimpleNamespace(role=role, status=status, evidence_json=evidence_json)


class TestInheritEvidenceFromDependencies:
    def test_pulls_preview_from_completed_dependency(self) -> None:
        dep = _task(
            role="implementer",
            status="completed",
            evidence=TaskEvidence(
                build_result={"ok": True},
                preview_result={"preview_status": "ok", "pages_checked": 1},
            ),
        )
        build, preview, runtime = engine._inherit_evidence_from_dependencies([dep])
        assert build == {"ok": True}
        assert preview == {"preview_status": "ok", "pages_checked": 1}
        assert runtime is None

    def test_skips_incomplete_and_malformed(self) -> None:
        bad = _task(role="implementer", status="completed")
        bad.evidence_json = "{not-json"
        pending = _task(role="implementer", status="pending")
        build, preview, runtime = engine._inherit_evidence_from_dependencies([bad, pending])
        assert build is None and preview is None and runtime is None


class TestTryRecoverQaDeadlock:
    def test_skips_blocked_qa_after_deliverable_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = SimpleNamespace(id=uuid.uuid4())
        plan_id = uuid.uuid4()
        plan_tasks = [
            _task(role="implementer", status="completed"),
            _task(role="qa_reviewer", status="blocked"),
            _task(role="security_reviewer", status="pending"),
        ]
        non_terminal = [t for t in plan_tasks if t.status != "completed"]
        task_repo = MagicMock()
        emit = MagicMock()
        monkeypatch.setattr(engine.events_bus, "emit", emit)

        recovered = engine._try_recover_qa_deadlock(
            MagicMock(),
            run=run,
            plan_id=plan_id,
            plan_tasks=plan_tasks,
            non_terminal=non_terminal,
            task_repo=task_repo,
        )

        assert recovered is True
        assert task_repo.transition.call_count == 2
        task_repo.refresh_readiness.assert_called_once_with(plan_id)
        emit.assert_called_once()

    def test_skips_looped_waiting_qa_after_deliverable_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = SimpleNamespace(id=uuid.uuid4())
        plan_id = uuid.uuid4()
        plan_tasks = [
            _task(role="implementer", status="completed"),
            _task(role="qa_reviewer", status="waiting_for_user"),
        ]
        task_repo = MagicMock()
        monkeypatch.setattr(engine.events_bus, "emit", MagicMock())

        recovered = engine._try_recover_qa_deadlock(
            MagicMock(),
            run=run,
            plan_id=plan_id,
            plan_tasks=plan_tasks,
            non_terminal=[plan_tasks[1]],
            task_repo=task_repo,
        )

        assert recovered is True
        task_repo.transition.assert_called_once()

    def test_no_recovery_without_completed_deliverable(self) -> None:
        plan_tasks = [
            _task(role="implementer", status="running"),
            _task(role="qa_reviewer", status="blocked"),
        ]
        recovered = engine._try_recover_qa_deadlock(
            MagicMock(),
            run=SimpleNamespace(id=uuid.uuid4()),
            plan_id=uuid.uuid4(),
            plan_tasks=plan_tasks,
            non_terminal=plan_tasks,
            task_repo=MagicMock(),
        )
        assert recovered is False


class TestPrepareRunForResume:
    def test_clears_replan_limit_blocker(self, db) -> None:
        user = User(email=f"{uuid.uuid4().hex}@example.com", credits_balance=1000)
        db.add(user)
        db.flush()
        project = Project(user_id=user.id, type="website", name="helpers")
        db.add(project)
        db.flush()
        chat = Chat(project_id=project.id)
        db.add(chat)
        db.flush()
        run = OrchestrationRunRepository(db).create(
            project_id=project.id,
            chat_id=chat.id,
            user_id=user.id,
            original_request="test",
        )
        run.status = "waiting_for_user"
        run.error_code = "replan_limit_reached"
        run.error_message = "too many replans"
        db.commit()

        engine.prepare_run_for_resume(db, run)
        db.commit()
        db.refresh(run)

        assert run.error_code is None
        assert run.error_message is None

    def test_clears_loop_detected_blocker(self, db) -> None:
        user = User(email=f"{uuid.uuid4().hex}@example.com", credits_balance=1000)
        db.add(user)
        db.flush()
        project = Project(user_id=user.id, type="website", name="helpers")
        db.add(project)
        db.flush()
        chat = Chat(project_id=project.id)
        db.add(chat)
        db.flush()
        run = OrchestrationRunRepository(db).create(
            project_id=project.id,
            chat_id=chat.id,
            user_id=user.id,
            original_request="test",
        )
        run.status = "waiting_for_user"
        run.error_code = "loop_detected"
        run.error_message = "stuck in loop"
        db.commit()

        engine.prepare_run_for_resume(db, run)
        db.commit()
        db.refresh(run)

        assert run.error_code is None
        assert run.error_message is None


class TestFormatEngineError:
    def test_includes_exception_type_and_message(self) -> None:
        formatted = engine._format_engine_error(RuntimeError("codex container died"))
        assert formatted.startswith("RuntimeError:")
        assert "codex container died" in formatted

    def test_collapses_whitespace_and_truncates(self) -> None:
        formatted = engine._format_engine_error(ValueError("a" * 500), limit=40)
        assert formatted.startswith("ValueError:")
        assert len(formatted) <= 40
