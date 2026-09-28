"""Tests for services/orchestration/evidence.py against a real tmp_path git repo - no DB, no
LLM, this module only ever touches git/filesystem state."""

from __future__ import annotations

from src.services.orchestration import evidence
from src.services.project_git import commit_snapshot, init_repo_if_needed


class TestScanForSecretLeaks:
    def test_flags_added_password_assignment(self) -> None:
        diff = "+password = 'hunter2hunter2'\n context line\n-old = 1\n"
        findings = evidence.scan_for_secret_leaks(diff)
        assert len(findings) == 1
        assert "hunter2hunter2" not in findings[0]

    def test_flags_connection_string_with_embedded_password(self) -> None:
        diff = "+DATABASE_URL = 'postgres://user:supersecretpw@host/db'\n"
        assert len(evidence.scan_for_secret_leaks(diff)) == 1

    def test_ignores_removed_lines(self) -> None:
        diff = "-password = 'hunter2hunter2'\n"
        assert evidence.scan_for_secret_leaks(diff) == []

    def test_clean_diff_has_no_findings(self) -> None:
        diff = "+def add(a, b):\n+    return a + b\n"
        assert evidence.scan_for_secret_leaks(diff) == []


class TestDetectDependencyChanges:
    def test_flags_known_manifest_files(self) -> None:
        changed = ["backend/requirements.txt", "src/app.py", "frontend/package.json"]
        assert evidence.detect_dependency_changes(changed) == [
            "backend/requirements.txt",
            "frontend/package.json",
        ]

    def test_no_manifests_changed(self) -> None:
        assert evidence.detect_dependency_changes(["src/app.py"]) == []


class TestCollectTaskEvidence:
    def test_end_to_end_against_real_repo(self, tmp_path) -> None:
        init_repo_if_needed(tmp_path)
        (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
        base_sha = commit_snapshot(tmp_path, message="initial")

        (tmp_path / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("flask==3.0\n", encoding="utf-8")
        commit_snapshot(tmp_path, message="add flask")

        result = evidence.collect_task_evidence(
            workspace_root=tmp_path,
            base_commit_sha=base_sha,
            build_result={"ok": True},
            duration_seconds=1.5,
            raw_logs="connecting api_key: sk-should-not-leak-1234",
        )

        assert "app.py" in result.changed_files
        assert "requirements.txt" in result.created_files
        assert result.dependency_changes == ["requirements.txt"]
        assert result.build_result == {"ok": True}
        assert result.duration_seconds == 1.5
        assert "sk-should-not-leak-1234" not in (result.logs_digest or "")

    def test_no_base_sha_diffs_against_empty_tree_not_against_nothing(self, tmp_path) -> None:
        # base_commit_sha=None means "no prior checkpoint" (e.g. this project's very first
        # task) - existing content is correctly reported as new relative to the empty tree,
        # not silently ignored (an earlier version of this diffed against a falsy base_sha by
        # short-circuiting to `[]`, which would have made scope validation blind to a brand
        # new project's very first task).
        init_repo_if_needed(tmp_path)
        (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
        commit_snapshot(tmp_path, message="initial")

        result = evidence.collect_task_evidence(workspace_root=tmp_path, base_commit_sha=None)
        assert result.changed_files == ["app.py"]
        assert result.secret_scan_findings == []

    def test_no_base_sha_and_no_commits_yields_empty_diff(self, tmp_path) -> None:
        init_repo_if_needed(tmp_path)
        result = evidence.collect_task_evidence(workspace_root=tmp_path, base_commit_sha=None)
        assert result.changed_files == []


class TestRunStaticChecks:
    """Server-side parse check - the static-validation layer (spec section 9). Deliberately
    parse-only: the backend must never execute agent-authored project code."""

    def test_returns_none_when_nothing_checkable_changed(self, tmp_path) -> None:
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        assert evidence.run_static_checks(tmp_path, ["index.html"]) is None

    def test_clean_python_and_json_pass(self, tmp_path) -> None:
        (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
        result = evidence.run_static_checks(tmp_path, ["app.py", "package.json"])
        assert result == {"ok": True, "checked_files": 2, "errors": []}

    def test_broken_python_is_reported(self, tmp_path) -> None:
        (tmp_path / "app.py").write_text("def main(\n    return 1\n", encoding="utf-8")
        result = evidence.run_static_checks(tmp_path, ["app.py"])
        assert result["ok"] is False
        assert any("app.py" in e for e in result["errors"])

    def test_malformed_package_json_is_reported(self, tmp_path) -> None:
        (tmp_path / "package.json").write_text('{"name": "x",}', encoding="utf-8")
        result = evidence.run_static_checks(tmp_path, ["package.json"])
        assert result["ok"] is False
        assert any("package.json" in e for e in result["errors"])

    def test_deleted_file_is_skipped_not_an_error(self, tmp_path) -> None:
        assert evidence.run_static_checks(tmp_path, ["gone.py"]) is None

    def test_collect_task_evidence_populates_lint_result_itself(self, tmp_path) -> None:
        """The whole point: lint_result must be server-collected, so validate_static can never
        be satisfied purely by an executor claiming it passed."""
        init_repo_if_needed(tmp_path)
        (tmp_path / "app.py").write_text("def broken(\n", encoding="utf-8")
        result = evidence.collect_task_evidence(workspace_root=tmp_path, base_commit_sha=None)
        assert result.lint_result is not None
        assert result.lint_result["ok"] is False

    def test_executor_claim_cannot_clear_a_real_server_finding(self, tmp_path) -> None:
        init_repo_if_needed(tmp_path)
        (tmp_path / "app.py").write_text("def broken(\n", encoding="utf-8")
        result = evidence.collect_task_evidence(
            workspace_root=tmp_path,
            base_commit_sha=None,
            lint_result={"ok": True, "errors": []},
        )
        assert result.lint_result["ok"] is False
