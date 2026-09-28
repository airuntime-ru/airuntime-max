"""Guards against the exact class of bug found 2026-07-23: settings.generated_projects_volume_name
must equal what Docker Compose actually names the projects-data volume
(`<compose project name>_<volume key>`), or codex_worker.py's per-project mount resolution
silently falls back to a broken bind-mount and every Codex turn runs against an empty throwaway
directory instead of the real project files - the agent reports files written/tests run
truthfully from its own point of view, with no error anywhere, and none of it reaches the actual
project (confirmed live: a real "create project" run produced a fully working site+cabinet+bot
inside Codex's own Docker build/run checks, then failed platform verification with "agent did not
produce the required public/index.html").

A one-character typo (`airruntime` vs `airuntime` - the compose project name has one "r", the
volume key has two) caused exactly this for the entire time the per-project isolation feature
existed (2026-07-19 to 2026-07-23) on both dev and prod, since neither compose file overrides the
default. Parses the real compose files rather than hardcoding the expectation, so this fails loud
again if either file's naming ever changes without updating the setting.
"""

import re
from pathlib import Path

from src.core.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")


def _compose_project_name(compose_path: Path) -> str:
    text = compose_path.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*(\S+)", text, re.MULTILINE)
    assert match, f"no top-level `name:` in {compose_path}"
    return match.group(1)


def _generated_projects_volume_key(compose_path: Path) -> str:
    text = compose_path.read_text(encoding="utf-8")
    # Top-level `volumes:` block lists volume keys as bare `  key:` lines - the projects-data
    # volume is the only one with "projects_data" in its name.
    match = re.search(r"^  (\S*projects_data\S*):\s*$", text, re.MULTILINE)
    assert match, f"no *projects_data volume declared in {compose_path}"
    return match.group(1)


def test_generated_projects_volume_name_matches_compose_naming():
    for compose_file in _COMPOSE_FILES:
        compose_path = _REPO_ROOT / compose_file
        project_name = _compose_project_name(compose_path)
        volume_key = _generated_projects_volume_key(compose_path)
        expected = f"{project_name}_{volume_key}"
        assert settings.generated_projects_volume_name == expected, (
            f"{compose_file}: Compose would name this volume {expected!r}, but "
            f"settings.generated_projects_volume_name is {settings.generated_projects_volume_name!r} "
            "- codex_worker.py's per-project mount resolution will always miss and silently fall "
            "back to a broken bind-mount (see this file's module docstring)."
        )


def test_both_compose_files_agree_on_the_volume_name():
    """dev and prod must resolve to the same volume name - they share one
    settings.generated_projects_volume_name default with no env override in either file."""
    names = {
        f"{_compose_project_name(_REPO_ROOT / f)}_{_generated_projects_volume_key(_REPO_ROOT / f)}"
        for f in _COMPOSE_FILES
    }
    assert len(names) == 1, f"docker-compose.yml and docker-compose.prod.yml disagree: {names}"
