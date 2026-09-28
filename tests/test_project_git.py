import io
import shutil
import uuid
import zipfile

import pytest

from src.core.config import settings
from src.services.project_git import (
    archive_version_stream,
    changed_files_by_status,
    commit_snapshot,
    init_repo_if_needed,
    list_version_tree,
    list_versions,
    project_repo_dir,
    read_version_file,
    rollback_to,
)


@pytest.fixture()
def project_dir(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git is not available")
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))
    project_id = uuid.uuid4()
    repo_dir = project_repo_dir(project_id)
    repo_dir.mkdir(parents=True, exist_ok=True)
    return repo_dir


def _write(repo_dir, filename: str, content: str) -> None:
    (repo_dir / filename).write_text(content, encoding="utf-8")


def test_git_commit_list_versions(project_dir):
    _write(project_dir, "Dockerfile", "FROM nginx:1\n")
    commit1 = commit_snapshot(project_dir, message="first")
    assert commit1 is not None

    _write(project_dir, "Dockerfile", "FROM nginx:2\n")
    commit2 = commit_snapshot(project_dir, message="second")
    assert commit2 is not None
    assert commit2 != commit1

    versions = list_versions(project_dir, limit=10)
    assert versions
    assert versions[0].commit_hash == commit2
    assert any(v.commit_hash == commit1 for v in versions)


def test_list_versions_on_repo_with_no_commits_returns_empty_not_raises(project_dir):
    # A freshly `init_repo_if_needed()`-ed repo has zero commits until the first task actually
    # commits something - engine.py's context summary is built before that point, so `git log`
    # exiting 128 ("does not have any commits yet") must read as "no history", not an error.
    init_repo_if_needed(project_dir)
    assert list_versions(project_dir, limit=10) == []


def test_platform_preview_artifacts_are_not_committed_or_reported_as_project_changes(project_dir):
    init_repo_if_needed(project_dir)
    preview_dir = project_dir / ".airuntime" / "preview" / "run-1"
    preview_dir.mkdir(parents=True)
    (preview_dir / "home_1440x900.png").write_bytes(b"png")
    (preview_dir / "result.json").write_text('{"status":"passed"}', encoding="utf-8")
    (project_dir / "public").mkdir()
    _write(project_dir, "public/index.html", "<h1>Hello</h1>\n")

    status = changed_files_by_status(project_dir, base_sha=None, head=None)
    assert status["added"] == ["public/index.html"]

    commit_hash = commit_snapshot(project_dir, message="site")
    assert commit_hash is not None
    names = {
        entry.name for entry in list_version_tree(project_dir, commit_hash=commit_hash, rel_path="")
    }
    assert names == {"public"}
    assert (preview_dir / "result.json").exists()


def test_git_archive_contains_files(project_dir):
    _write(project_dir, "Dockerfile", "FROM nginx:1\n")
    commit1 = commit_snapshot(project_dir, message="first")
    assert commit1 is not None

    zip_bytes = archive_version_stream(project_dir, commit_hash=commit1)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        assert any(name.endswith("Dockerfile") for name in names)


def test_git_rollback_restores_tree(project_dir):
    _write(project_dir, "Dockerfile", "FROM nginx:1\n")
    commit1 = commit_snapshot(project_dir, message="first")
    assert commit1 is not None

    _write(project_dir, "Dockerfile", "FROM nginx:2\n")
    commit2 = commit_snapshot(project_dir, message="second")
    assert commit2 is not None
    assert commit2 != commit1

    rollback_head = rollback_to(project_dir, commit_hash=commit1, message="Rollback test")
    assert rollback_head
    assert rollback_head != commit2

    assert (project_dir / "Dockerfile").read_text(encoding="utf-8") == "FROM nginx:1\n"

    versions = list_versions(project_dir, limit=10)
    assert versions and versions[0].commit_hash == rollback_head


def test_git_tree_and_file_read(project_dir):
    _write(project_dir, "Dockerfile", "FROM nginx:1\n")
    (project_dir / "public").mkdir()
    _write(project_dir, "public/index.html", "<h1>Hello</h1>\n")
    commit1 = commit_snapshot(project_dir, message="first")
    assert commit1 is not None

    entries = list_version_tree(project_dir, commit_hash=commit1, rel_path="")
    assert any(e.name == "Dockerfile" and e.entry_type == "blob" for e in entries)
    assert any(e.name == "public" and e.entry_type == "tree" for e in entries)

    nested_entries = list_version_tree(project_dir, commit_hash=commit1, rel_path="public")
    assert any(e.name == "index.html" and e.entry_type == "blob" for e in nested_entries)
    assert all(not e.name.startswith("public/") for e in nested_entries)

    file_payload = read_version_file(project_dir, commit_hash=commit1, rel_path="Dockerfile")
    assert file_payload["is_binary"] is False
    assert "FROM nginx:1" in file_payload["content"]
