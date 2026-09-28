"""Tests for workspace_isolation.py + git_transaction.py against real tmp_path git repos and
the real test DB - these modules are all real subprocess/filesystem/DB operations, no mocking
needed or wanted here. Covers spec section 31's "parallel isolated write", "merge conflicts",
and "double execution prevention" requirements at the module level (engine.py-level end-to-end
coverage is in test_orchestration_engine_e2e.py)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from src.db.models.agent_task import AgentTask
from src.db.models.chat import Chat
from src.db.models.orchestration_plan import OrchestrationPlan
from src.db.models.orchestration_run import OrchestrationRun
from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration.git_transaction import GitTransactionManager
from src.services.orchestration.schemas import (
    ProjectStateSummary,
    SpecialistRole,
    TaskBudget,
    TaskContract,
    TaskResult,
    ValidationStep,
)
from src.services.orchestration.workspace_isolation import WorkspaceIsolationManager, make_holder_id
from src.services.project_git import (
    ProjectGitError,
    init_repo_if_needed,
    list_versions,
    merge_worktree_branch,
)


@pytest.fixture()
def project(db: Session) -> Project:
    user = User(email=f"{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, type="website", name="Txn test")
    db.add(project)
    db.flush()
    return project


@pytest.fixture()
def run(db: Session, project: Project) -> OrchestrationRun:
    chat = Chat(project_id=project.id)
    db.add(chat)
    db.flush()
    run = OrchestrationRun(project_id=project.id, chat_id=chat.id, user_id=project.user_id)
    db.add(run)
    db.flush()
    return run


@pytest.fixture()
def plan(db: Session, run: OrchestrationRun) -> OrchestrationPlan:
    plan = OrchestrationPlan(run_id=run.id, version=1, status="active", graph_json="{}")
    db.add(plan)
    db.flush()
    return plan


def _make_task(
    db: Session, run, plan, *, local_id="t", workspace_mode="shared_sequential", role="implementer"
) -> AgentTask:
    task = AgentTask(
        run_id=run.id,
        plan_id=plan.id,
        local_id=local_id,
        title=f"Task {local_id}",
        role=role,
        execution_kind="codex_task",
        status="running",
        max_attempts=3,
        workspace_mode=workspace_mode,
    )
    db.add(task)
    db.flush()
    return task


def _contract(task: AgentTask, *, allowed_paths=None, forbidden_paths=None) -> TaskContract:
    return TaskContract(
        task_id=task.id,
        run_id=task.run_id,
        role=SpecialistRole(task.role),
        project_goal="g",
        user_value="u",
        task_goal="t",
        reason="r",
        current_state=ProjectStateSummary(project_type="website", project_name="p"),
        allowed_paths=allowed_paths if allowed_paths is not None else ["*"],
        forbidden_paths=forbidden_paths if forbidden_paths is not None else [],
        budget=TaskBudget(),
    )


class TestWorkspaceIsolationSharedLease:
    def test_second_shared_acquire_is_blocked(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        manager = WorkspaceIsolationManager(db)
        task_a = _make_task(db, run, plan, local_id="a")
        task_b = _make_task(db, run, plan, local_id="b")

        first = manager.acquire_for_task(
            task=task_a,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
        )
        assert first is not None

        second = manager.acquire_for_task(
            task=task_b,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
        )
        assert second is None, (
            "double execution: a second shared acquire on the same project must be rejected"
        )

        manager.release(first)
        third = manager.acquire_for_task(
            task=task_b,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
        )
        assert third is not None

    def test_read_only_needs_no_lease_and_never_blocks(
        self, db: Session, project: Project, run, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        manager = WorkspaceIsolationManager(db)
        task = _make_task(db, run, plan, local_id="ro", workspace_mode="parallel_read_only")
        acquired = manager.acquire_for_task(
            task=task,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
        )
        assert acquired is not None
        assert acquired.lease is None
        assert acquired.workspace_root == tmp_path


class TestGitTransactionHappyPath:
    def test_successful_transaction_commits_with_expected_message_and_releases_lease(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)
        task = _make_task(db, run, plan, local_id="impl")
        contract = _contract(task)

        handle = txn.begin(
            task=task,
            contract=contract,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        assert handle is not None

        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

        outcome = txn.complete(
            handle, result=TaskResult(status="completed", summary="done"), build_result={"ok": True}
        )
        assert outcome.committed is True
        assert outcome.accepted_commit_sha is not None
        assert outcome.validation_result.accepted is True

        versions = list_versions(tmp_path)
        assert (
            f"run:{str(run.id)[:8]} task:{str(task.id)[:8]} role:implementer" in versions[0].message
        )

        # lease released -> a new transaction can begin immediately
        task2 = _make_task(db, run, plan, local_id="impl2")
        handle2 = txn.begin(
            task=task2,
            contract=_contract(task2),
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        assert handle2 is not None

    def test_failed_validation_never_commits_and_discards_changes(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        (tmp_path / "existing.txt").write_text("original", encoding="utf-8")
        from src.services.project_git import commit_snapshot

        commit_snapshot(tmp_path, message="seed")

        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)
        task = _make_task(db, run, plan, local_id="scoped")
        contract = _contract(task, allowed_paths=["public/"], forbidden_paths=[".env"])

        handle = txn.begin(
            task=task,
            contract=contract,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")  # forbidden path touched

        outcome = txn.complete(handle, result=TaskResult(status="completed", summary="done"))
        assert outcome.committed is False
        assert outcome.validation_result.accepted is False
        assert not (tmp_path / ".env").exists(), (
            "an unaccepted change must be discarded, not left dangling"
        )
        assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "original"

    def test_preview_failure_keeps_written_files_for_repair(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        from src.services.project_git import commit_snapshot

        (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
        commit_snapshot(tmp_path, message="seed")

        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)
        task = _make_task(db, run, plan, local_id="preview-keep")
        contract = _contract(task, allowed_paths=["public/"])
        contract = contract.model_copy(
            update={
                "validation_steps": [
                    ValidationStep(kind="preview", description="must preview", required=True)
                ]
            }
        )
        handle = txn.begin(
            task=task,
            contract=contract,
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        public = tmp_path / "public"
        public.mkdir()
        (public / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")

        outcome = txn.complete(
            handle,
            result=TaskResult(status="completed", summary="done"),
            preview_result={
                "status": "issues_found",
                "fatal_errors": [],
                "pages": [
                    {
                        "url": "/",
                        "console_errors": ["Uncaught TypeError"],
                        "network_errors": [],
                        "overflow_elements": [],
                        "broken_images": [],
                    }
                ],
            },
        )
        assert outcome.committed is False
        assert outcome.validation_result.accepted is False
        assert (public / "index.html").exists(), (
            "preview-only failures must keep the working tree so repair can iterate"
        )

    def test_abort_discards_and_releases(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)
        task = _make_task(db, run, plan, local_id="cancelled")
        handle = txn.begin(
            task=task,
            contract=_contract(task),
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        (tmp_path / "partial.txt").write_text("half-done", encoding="utf-8")

        txn.abort(handle)
        assert not (tmp_path / "partial.txt").exists()

        task2 = _make_task(db, run, plan, local_id="after-cancel")
        handle2 = txn.begin(
            task=task2,
            contract=_contract(task2),
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        assert handle2 is not None, "abort must release the lease"


class TestIsolatedWorktreeAndMerge:
    def test_isolated_task_writes_do_not_touch_shared_checkout(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        from src.services.project_git import commit_snapshot

        (tmp_path / "base.txt").write_text("base", encoding="utf-8")
        base_sha = commit_snapshot(tmp_path, message="seed")

        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)
        task = _make_task(db, run, plan, local_id="isolated-a", workspace_mode="isolated_worktree")
        task.base_commit_sha = base_sha
        db.flush()

        handle = txn.begin(
            task=task,
            contract=_contract(task),
            project_id=project.id,
            run_id=run.id,
            project_root=tmp_path,
            holder=make_holder_id(),
            ttl_seconds=60,
        )
        assert handle.acquired.mode == "isolated"
        assert handle.acquired.workspace_root != tmp_path

        (handle.acquired.workspace_root / "feature_a.txt").write_text("feature a", encoding="utf-8")
        outcome = txn.complete(
            handle, result=TaskResult(status="completed", summary="done"), build_result={"ok": True}
        )
        assert outcome.committed is True

        # the SHARED checkout must be completely unaffected by the isolated task's commit
        assert not (tmp_path / "feature_a.txt").exists()

    def test_two_independent_isolated_branches_both_merge_cleanly(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        from src.services.project_git import commit_snapshot

        (tmp_path / "base.txt").write_text("base", encoding="utf-8")
        base_sha = commit_snapshot(tmp_path, message="seed")

        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)

        branches = []
        for local_id in ("iso-x", "iso-y"):
            task = _make_task(db, run, plan, local_id=local_id, workspace_mode="isolated_worktree")
            task.base_commit_sha = base_sha
            db.flush()
            handle = txn.begin(
                task=task,
                contract=_contract(task),
                project_id=project.id,
                run_id=run.id,
                project_root=tmp_path,
                holder=make_holder_id(),
                ttl_seconds=60,
            )
            (handle.acquired.workspace_root / f"{local_id}.txt").write_text(
                local_id, encoding="utf-8"
            )
            outcome = txn.complete(
                handle,
                result=TaskResult(status="completed", summary="done"),
                build_result={"ok": True},
            )
            assert outcome.committed is True
            branches.append(handle.acquired.branch_name)

        for branch in branches:
            merge_worktree_branch(tmp_path, branch_name=branch, message=f"merge {branch}")

        assert (tmp_path / "iso-x.txt").exists()
        assert (tmp_path / "iso-y.txt").exists()

    def test_conflicting_isolated_edits_do_not_silently_auto_resolve(
        self, db: Session, project: Project, run: OrchestrationRun, plan, tmp_path
    ) -> None:
        init_repo_if_needed(tmp_path)
        from src.services.project_git import commit_snapshot

        (tmp_path / "shared.txt").write_text("original\n", encoding="utf-8")
        base_sha = commit_snapshot(tmp_path, message="seed")

        isolation = WorkspaceIsolationManager(db)
        txn = GitTransactionManager(db, isolation)

        branches = []
        for local_id, content in (("iso-1", "version one\n"), ("iso-2", "version two\n")):
            task = _make_task(db, run, plan, local_id=local_id, workspace_mode="isolated_worktree")
            task.base_commit_sha = base_sha
            db.flush()
            handle = txn.begin(
                task=task,
                contract=_contract(task),
                project_id=project.id,
                run_id=run.id,
                project_root=tmp_path,
                holder=make_holder_id(),
                ttl_seconds=60,
            )
            (handle.acquired.workspace_root / "shared.txt").write_text(content, encoding="utf-8")
            outcome = txn.complete(
                handle,
                result=TaskResult(status="completed", summary="done"),
                build_result={"ok": True},
            )
            assert outcome.committed is True
            branches.append(handle.acquired.branch_name)

        merge_worktree_branch(tmp_path, branch_name=branches[0], message="merge first")
        with pytest.raises(ProjectGitError):
            merge_worktree_branch(
                tmp_path, branch_name=branches[1], message="merge second - should conflict"
            )

        # the conflicting merge must have been aborted, not left half-applied
        assert tmp_path.joinpath("shared.txt").read_text(encoding="utf-8") == "version one\n"
