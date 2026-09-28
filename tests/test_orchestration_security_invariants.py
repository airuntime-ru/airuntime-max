"""Dedicated cross-cutting security-invariant tests for the orchestration system (spec: server-
enforced boundaries a role/plan/model can never expand on its own - role_policy.py's own words,
"Модель не может самостоятельно расширить свои права"). Individual modules already have their
own unit coverage elsewhere (role_policy, mcp/registry, evidence, budget, context_engine) - this
file targets invariants that had no direct coverage anywhere, or were only ever exercised through
a fake/mocked inner layer rather than the real filesystem/security primitive:

  - Path-traversal containment in the REAL WorkspaceTools (agent/tools.py), across every
    file-operation tool, not just write_file (test_agentic_artifacts.py only covers that one).
  - ScopedWorkspaceTools composed with the REAL WorkspaceTools (not _FakeInnerWorkspace, which
    is all test_orchestration_executors.py ever uses) - proving the two independent layers of
    defense actually add up when stacked, and closing a real gap found while writing this file:
    read_file bypassed allowed_paths/forbidden_paths entirely (see executors.py's
    ScopedWorkspaceTools.call - a leftover unused `_WRITE_TOOLS_WITH_PATH_ARG` frozenset already
    listed read_file, suggesting this was the original intent that never got wired up).
  - redact_secrets (prompt_guard.py) - the shared primitive three call sites depend on
    (deployments.py, context_engine.py, evidence.py), with zero direct test coverage before
    this file.
  - compute_write_scope's `.airuntime/requests` always-forbidden entry, the one always_forbidden
    member existing role_policy tests never asserted directly.
"""

from __future__ import annotations

import uuid

import pytest

from src.services.agent.tools import WorkspaceTools
from src.services.orchestration.executors import ScopedWorkspaceTools
from src.services.orchestration.role_policy import compute_write_scope
from src.services.orchestration.schemas import (
    ProjectStateSummary,
    SpecialistRole,
    TaskBudget,
    TaskContract,
)
from src.services.prompt_guard import redact_secrets
from src.services.workspace import WorkspaceError, resolve_in_workspace


def _contract(**overrides) -> TaskContract:
    defaults = dict(
        task_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        role=SpecialistRole.IMPLEMENTER,
        project_goal="goal",
        user_value="value",
        task_goal="task goal",
        reason="reason",
        current_state=ProjectStateSummary(project_type="website", project_name="p"),
        allowed_paths=["public"],
        forbidden_paths=[],
        allowed_tools=["write_file", "read_file"],
        budget=TaskBudget(),
    )
    defaults.update(overrides)
    return TaskContract(**defaults)


class TestWorkspaceToolsPathTraversal:
    """Every file-operation tool must route through resolve_in_workspace's containment check -
    not just write_file (the only one exercised elsewhere today)."""

    @pytest.mark.parametrize(
        "raw_path",
        [
            "../escape.txt",
            "../../etc/passwd",
            "a/../../b/escape.txt",
            "C:/Windows/win.ini",  # colon rejected by safe_relative_path's charset whitelist
            "~/escape.txt",
            ".env",
            ".git/config",
            "node_modules/x",
            ".ssh/id_rsa",  # any hidden (dot) path component, not just the fixed blocklist
            "a/\x00/b",
        ],
    )
    def test_write_rejects_every_escape_shape(self, tmp_path, raw_path) -> None:
        tools = WorkspaceTools(tmp_path)
        result = tools.call("write_file", {"path": raw_path, "content": "pwned"})
        assert result.ok is False

    @pytest.mark.parametrize(
        "raw_path", ["../escape.txt", "../../etc/passwd", ".env", ".git/config"]
    )
    def test_read_rejects_every_escape_shape(self, tmp_path, raw_path) -> None:
        tools = WorkspaceTools(tmp_path)
        result = tools.call("read_file", {"path": raw_path})
        assert result.ok is False

    @pytest.mark.parametrize("raw_path", ["../escape.txt", ".env", ".git/config"])
    def test_edit_rejects_every_escape_shape(self, tmp_path, raw_path) -> None:
        tools = WorkspaceTools(tmp_path)
        result = tools.call("edit_file", {"path": raw_path, "old_text": "x", "new_text": "y"})
        assert result.ok is False

    @pytest.mark.parametrize("raw_path", ["../escape.txt", ".env", ".git/config"])
    def test_delete_rejects_every_escape_shape(self, tmp_path, raw_path) -> None:
        tools = WorkspaceTools(tmp_path)
        result = tools.call("delete_file", {"path": raw_path})
        assert result.ok is False

    def test_legitimate_nested_path_still_works(self, tmp_path) -> None:
        # The containment check must not be so aggressive it blocks ordinary nested writes.
        tools = WorkspaceTools(tmp_path)
        result = tools.call("write_file", {"path": "src/components/Button.tsx", "content": "x"})
        assert result.ok is True
        assert (tmp_path / "src/components/Button.tsx").exists()

    def test_symlink_escape_is_still_contained_by_the_resolve_check(self, tmp_path) -> None:
        # A string-only blocklist (safe_relative_path) can't see through a symlink that looks
        # like an innocent in-workspace name on disk - resolve_in_workspace's second layer
        # (.resolve() + is_relative_to) is what actually catches this. Requires real symlink
        # privilege - skip cleanly where the OS/account doesn't allow creating one.
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("top secret", encoding="utf-8")
        try:
            (workspace / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted in this environment")

        with pytest.raises(WorkspaceError):
            resolve_in_workspace(workspace, "link/secret.txt")

        tools = WorkspaceTools(workspace)
        result = tools.call("read_file", {"path": "link/secret.txt"})
        assert result.ok is False


class TestScopedWorkspaceToolsRealComposition:
    """ScopedWorkspaceTools' preventive allowlist and WorkspaceTools' own containment check are
    two independent layers - test_orchestration_executors.py only ever wraps a fake inner, so
    this proves they still compose correctly when stacked for real."""

    def test_scoped_write_outside_allowed_paths_is_rejected_before_touching_disk(
        self, tmp_path
    ) -> None:
        inner = WorkspaceTools(tmp_path)
        contract = _contract(allowed_tools=["write_file"], allowed_paths=["public"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call(
            "write_file", {"path": "backend/src/core/config.py", "content": "pwned"}
        )
        assert result.ok is False
        assert not (tmp_path / "backend/src/core/config.py").exists()

    def test_full_workspace_role_write_still_blocked_by_the_lower_traversal_check(
        self, tmp_path
    ) -> None:
        # allowed_paths=["*"] (an Implementer-shaped contract) satisfies ScopedWorkspaceTools'
        # OWN path_matches_any check for literally anything, including "../escape.txt" - it's
        # the independent lower WorkspaceTools/resolve_in_workspace layer that must still catch
        # this, not the scoping layer.
        inner = WorkspaceTools(tmp_path)
        contract = _contract(
            allowed_tools=["write_file"], allowed_paths=["*"], forbidden_paths=[".env", ".git"]
        )
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("write_file", {"path": "../escape.txt", "content": "pwned"})
        assert result.ok is False
        assert not (tmp_path.parent / "escape.txt").exists()

    def test_scoped_role_read_is_now_confined_to_allowed_paths(self, tmp_path) -> None:
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "billing.py").write_text("SECRET_LOGIC = 1", encoding="utf-8")
        inner = WorkspaceTools(tmp_path)
        contract = _contract(allowed_tools=["read_file"], allowed_paths=["public"])
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("read_file", {"path": "backend/billing.py"})
        assert result.ok is False

    def test_read_only_ceiling_role_keeps_full_read_visibility(self, tmp_path) -> None:
        # SolutionArchitect/QAReviewer-shaped contract: forbidden_paths=["*"] from
        # compute_write_scope's NONE branch means "no write scope at all", not "no read
        # visibility" - see role_policy._SOLUTION_ARCHITECT_PROMPT ("можешь читать файлы... но
        # не писать"). This must keep working after the read-scoping fix above.
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "billing.py").write_text("SECRET_LOGIC = 1", encoding="utf-8")
        inner = WorkspaceTools(tmp_path)
        contract = _contract(
            role=SpecialistRole.SOLUTION_ARCHITECT,
            allowed_tools=["read_file"],
            allowed_paths=[],
            forbidden_paths=["*"],
        )
        scoped = ScopedWorkspaceTools(inner, contract=contract)

        result = scoped.call("read_file", {"path": "backend/billing.py"})
        assert result.ok is True
        assert result.content == "SECRET_LOGIC = 1"


class TestRedactSecrets:
    """prompt_guard.redact_secrets - shared by deployments.py, context_engine.py and
    evidence.py, each with its own narrow indirect assertion, but never tested directly."""

    def test_redacts_key_equals_value(self) -> None:
        out = redact_secrets("STRIPE_API_KEY=sk_live_abcdef123456")
        assert "sk_live_abcdef123456" not in out
        assert "REDACTED" in out

    def test_redacts_key_colon_value(self) -> None:
        out = redact_secrets("password: hunter2000")
        assert "hunter2000" not in out

    def test_case_insensitive_key_names(self) -> None:
        out = redact_secrets("Authorization Token=abc.def.ghi")
        assert "abc.def.ghi" not in out

    def test_redacts_credentials_in_connection_url(self) -> None:
        # The specific gap this second pattern exists for (see prompt_guard.py's docstring):
        # "DATABASE_URL" itself contains none of api_key/token/secret/password.
        out = redact_secrets("DATABASE_URL=postgres://dbuser:sup3rSecret@db-host:5432/app")
        assert "sup3rSecret" not in out
        assert "dbuser" in out
        assert "db-host" in out

    def test_does_not_mangle_unrelated_text(self) -> None:
        text = "Traceback (most recent call last):\n  File \"app.py\", line 3\nKeyError: 'x'"
        assert redact_secrets(text) == text

    def test_empty_string_is_a_noop(self) -> None:
        assert redact_secrets("") == ""


class TestWriteScopeAlwaysForbidsPlatformPaths:
    """compute_write_scope's always_forbidden triple - existing role_policy tests only assert
    .env/.git directly; .airuntime/requests (the engine's own control-channel directory, used
    for secret/service request bookkeeping) was never asserted on its own."""

    @pytest.mark.parametrize(
        "role",
        [
            SpecialistRole.IMPLEMENTER,
            SpecialistRole.UI_UX_SPECIALIST,
            SpecialistRole.INTEGRATION_AGENT,
        ],
    )
    def test_airuntime_requests_always_forbidden(self, role: SpecialistRole) -> None:
        _allowed, forbidden = compute_write_scope(
            role, requested_paths=["*"], fallback_relevant_paths=[]
        )
        assert ".airuntime/requests" in forbidden
