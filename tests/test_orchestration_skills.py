"""Tests for services/orchestration/skills/. Deterministic skills (analysis.py) are tested
against real tmp_path workspaces with no mocking. DB-backed skills (provisioning, telegram,
secret_setup, release) use the real test DB + a real tmp_path git repo. RPC-backed skills
(runtime_health, repair) monkeypatch submit_control_job/the wrapped service function at their
import site in the skill module, matching this repo's existing convention."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration.schemas import PlannedTask, SpecialistRole
from src.services.orchestration.skills import registry as default_registry
from src.services.orchestration.skills.analysis import (
    DatabaseMigrationsSkill,
    DependencyHealthCheckSkill,
    ProjectStructureReviewSkill,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.provisioning import build_provisioning_skills
from src.services.orchestration.skills.release import ReleaseCheckpointSkill, RollbackReleaseSkill
from src.services.orchestration.skills.secret_setup import SecretSlotSetupSkill
from src.services.orchestration.skills.telegram import TelegramBotSetupSkill


@pytest.fixture()
def project(db: Session) -> Project:
    user = User(email=f"{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, type="website", name="Skill test")
    db.add(project)
    db.flush()
    return project


def _ctx(project: Project, db: Session, workspace_root, **arguments) -> SkillContext:  # noqa: ANN001
    return SkillContext(
        project_id=str(project.id),
        run_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        workspace_root=str(workspace_root),
        project_type=project.type,
        arguments=arguments,
        db=db,
    )


def test_default_registry_has_every_skill_supporting_at_least_one_role_and_type() -> None:
    for skill in default_registry.all_skills():
        assert skill.definition.supported_roles, skill.definition.id
        assert skill.definition.supported_project_types, skill.definition.id
    assert "product_quality_review" in default_registry.all_ids()


class TestDatabaseMigrationsSkill:
    @pytest.mark.asyncio
    async def test_finds_alembic_versions_dir(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        (tmp_path / "alembic" / "versions").mkdir(parents=True)
        (tmp_path / "alembic" / "versions" / "0001_init.py").write_text("", encoding="utf-8")

        result = await DatabaseMigrationsSkill().execute(_ctx(project, db, tmp_path))
        assert result.output["found"] is True
        assert result.output["migration_count"] == 1

    @pytest.mark.asyncio
    async def test_no_migrations_dir(self, project: Project, db: Session, tmp_path) -> None:
        result = await DatabaseMigrationsSkill().execute(_ctx(project, db, tmp_path))
        assert result.output["found"] is False


class TestDependencyHealthCheckSkill:
    @pytest.mark.asyncio
    async def test_flags_unpinned_requirement(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi\nflask>=2.0\n", encoding="utf-8")
        result = await DependencyHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "partial"
        assert len(result.output["findings"]) == 2

    @pytest.mark.asyncio
    async def test_pinned_requirements_clean(self, project: Project, db: Session, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi==0.116.0\n", encoding="utf-8")
        result = await DependencyHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_package_json_without_lockfile_flagged(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"react": "18.0.0"}}', encoding="utf-8"
        )
        result = await DependencyHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert any("lockfile" in f for f in result.output["findings"])

    @pytest.mark.asyncio
    async def test_no_manifest_at_all(self, project: Project, db: Session, tmp_path) -> None:
        result = await DependencyHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "completed"
        assert result.output["findings"] == []


class TestProjectStructureReviewSkill:
    @pytest.mark.asyncio
    async def test_website_missing_index(self, project: Project, db: Session, tmp_path) -> None:
        result = await ProjectStructureReviewSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "partial"
        assert "public/index.html" in result.output["missing"]

    @pytest.mark.asyncio
    async def test_website_with_index_passes(self, project: Project, db: Session, tmp_path) -> None:
        (tmp_path / "public").mkdir()
        (tmp_path / "public" / "index.html").write_text("<html></html>", encoding="utf-8")
        result = await ProjectStructureReviewSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "completed"
        assert result.output["missing"] == []


class TestProvisioningSkills:
    @pytest.mark.asyncio
    async def test_provision_postgres_creates_service_row(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        postgres_skill = next(
            s for s in build_provisioning_skills() if s.definition.id == "provision_postgres"
        )
        result = await postgres_skill.execute(_ctx(project, db, tmp_path, reason="need a DB"))
        assert result.status == "completed"
        assert result.output["created"] is True
        assert "postgres" in result.output["hostname"]

    @pytest.mark.asyncio
    async def test_provision_is_idempotent(self, project: Project, db: Session, tmp_path) -> None:
        skill = next(s for s in build_provisioning_skills() if s.definition.id == "provision_redis")
        first = await skill.execute(_ctx(project, db, tmp_path))
        second = await skill.execute(_ctx(project, db, tmp_path))
        assert first.output["created"] is True
        assert second.output["created"] is False
        assert first.output["hostname"] == second.output["hostname"]

    @pytest.mark.asyncio
    async def test_custom_service_requires_image(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        skill = next(
            s for s in build_provisioning_skills() if s.definition.id == "provision_custom_service"
        )
        result = await skill.execute(_ctx(project, db, tmp_path))
        assert result.status == "failed"


class TestTelegramBotSetupSkill:
    @pytest.mark.asyncio
    async def test_reserves_token_slot(self, db: Session, tmp_path) -> None:
        user = User(email=f"{uuid.uuid4().hex}@example.com")
        db.add(user)
        db.flush()
        project = Project(user_id=user.id, type="telegram_bot", name="Bot")
        db.add(project)
        db.flush()

        result = await TelegramBotSetupSkill().execute(_ctx(project, db, tmp_path))
        assert result.output["key"] == "TELEGRAM_BOT_TOKEN"
        assert result.output["already_filled"] is False

    @pytest.mark.asyncio
    async def test_matches_on_telegram_mention_even_without_suggestion(self) -> None:
        task = PlannedTask(
            local_id="t",
            title="t",
            role=SpecialistRole.IMPLEMENTER,
            goal="настрой telegram бота",
            reason="r",
        )
        outcome = await TelegramBotSetupSkill().match(task, role=SpecialistRole.IMPLEMENTER)
        assert outcome.matched is True

    @pytest.mark.asyncio
    async def test_does_not_match_unrelated_goal(self) -> None:
        task = PlannedTask(
            local_id="t",
            title="t",
            role=SpecialistRole.IMPLEMENTER,
            goal="add a contact form",
            reason="r",
        )
        outcome = await TelegramBotSetupSkill().match(task, role=SpecialistRole.IMPLEMENTER)
        assert outcome.matched is False


class TestSecretSlotSetupSkill:
    @pytest.mark.asyncio
    async def test_requires_key(self, project: Project, db: Session, tmp_path) -> None:
        result = await SecretSlotSetupSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_reserves_arbitrary_key(self, project: Project, db: Session, tmp_path) -> None:
        result = await SecretSlotSetupSkill().execute(
            _ctx(project, db, tmp_path, key="STRIPE_SECRET_KEY", reason="payments")
        )
        assert result.status == "completed"
        assert result.output["key"] == "STRIPE_SECRET_KEY"


class TestReleaseSkills:
    @pytest.mark.asyncio
    async def test_checkpoint_then_rollback_round_trip(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        from src.services.project_git import init_repo_if_needed

        init_repo_if_needed(tmp_path)
        (tmp_path / "a.txt").write_text("v1", encoding="utf-8")

        checkpoint = await ReleaseCheckpointSkill().execute(
            _ctx(project, db, tmp_path, message="v1")
        )
        assert checkpoint.status == "completed"
        first_sha = checkpoint.output["commit_sha"]
        assert first_sha

        (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
        await ReleaseCheckpointSkill().execute(_ctx(project, db, tmp_path, message="v2"))

        rollback = await RollbackReleaseSkill().execute(
            _ctx(project, db, tmp_path, commit_hash=first_sha)
        )
        assert rollback.status == "completed"
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"

    @pytest.mark.asyncio
    async def test_rollback_without_commit_hash_fails_cleanly(
        self, project: Project, db: Session, tmp_path
    ) -> None:
        result = await RollbackReleaseSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "failed"


class TestRuntimeHealthCheckSkill:
    @pytest.mark.asyncio
    async def test_reports_ok_when_rpc_says_healthy(
        self, project: Project, db: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.services.orchestration.skills.runtime_health as module

        monkeypatch.setattr(
            module,
            "submit_control_job",
            lambda **kwargs: {  # noqa: ANN003
                "ok": True,
                "container_found": True,
                "container_status": "running",
                "restart_count": 0,
                "restarting": False,
                "restart_loop_suspected": False,
                "port_80_listening": True,
            },
        )
        result = await module.RuntimeHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_reports_partial_on_suspected_restart_loop(
        self, project: Project, db: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.services.orchestration.skills.runtime_health as module

        monkeypatch.setattr(
            module,
            "submit_control_job",
            lambda **kwargs: {  # noqa: ANN003
                "ok": False,
                "container_found": True,
                "container_status": "running",
                "restart_count": 7,
                "restarting": True,
                "restart_loop_suspected": True,
                "port_80_listening": False,
            },
        )
        result = await module.RuntimeHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "partial"
        assert "crash loop" in result.summary

    @pytest.mark.asyncio
    async def test_rpc_unreachable_reported_as_failed(
        self, project: Project, db: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.services.orchestration.skills.runtime_health as module

        monkeypatch.setattr(module, "submit_control_job", lambda **kwargs: None)  # noqa: ANN003
        result = await module.RuntimeHealthCheckSkill().execute(_ctx(project, db, tmp_path))
        assert result.status == "failed"
