from pathlib import Path

import pytest

from src.services.agent import codex_runtime
from src.services.agent.codex_runtime import _map_event, _relativize
from src.services.agent.events import TextDelta, ToolCallRequested, ToolCallResult


def test_relativize_against_workspace_root():
    # Production always runs this against PosixPath (the app only runs in Linux containers) -
    # normalize separators here so the test itself is portable to a Windows dev machine too.
    root = Path("/data/airruntime-projects/proj-1")
    result = _relativize("/data/airruntime-projects/proj-1/public/index.html", root)
    assert result.replace("\\", "/") == "public/index.html"


def test_relativize_falls_back_to_isolated_workspace_mount():
    """Regression: per-project mount isolation (codex_worker.py) remaps Codex's own view of the
    project to /workspace, which never matches this process's own (differently-rooted) view of
    the same directory - relative_to(root) always raised ValueError for that case and the raw
    in-container path ("/workspace/public/app.js") leaked straight into the chat UI."""
    root = Path("/data/airruntime-projects/proj-1")
    assert _relativize("/workspace/public/app.js", root) == "public/app.js"


def test_relativize_handles_missing_root():
    assert _relativize("/workspace/app.py", None) == "app.py"


def test_relativize_leaves_unrecognized_paths_alone():
    assert (
        _relativize("/some/other/path.py", Path("/data/airruntime-projects/proj-1"))
        == "/some/other/path.py"
    )


def _command_started(command: str) -> dict:
    return {
        "type": "item.started",
        "item": {"id": "item_1", "type": "command_execution", "command": command},
    }


def _command_completed(exit_code: int | None, output: str) -> dict:
    return {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "exit_code": exit_code,
            "aggregated_output": output,
        },
    }


def test_command_execution_success_has_no_raw_exit_code():
    events = _map_event(_command_completed(0, "ok\n"))
    assert len(events) == 1
    result = events[0]
    assert isinstance(result, ToolCallResult)
    assert result.ok is True
    assert "exit" not in result.summary.lower()
    assert result.summary == "Готово"


def test_command_execution_failure_surfaces_last_output_line_in_russian():
    output = "installing...\nbash: eslint: command not found\n"
    events = _map_event(_command_completed(127, output))
    result = events[0]
    assert isinstance(result, ToolCallResult)
    assert result.ok is False
    assert result.summary == "Ошибка: bash: eslint: command not found"


def test_command_execution_failure_without_output_still_readable():
    events = _map_event(_command_completed(1, ""))
    result = events[0]
    assert result.summary == "Команда завершилась с ошибкой"


def test_command_execution_started_carries_raw_command_for_the_frontend_to_label():
    events = _map_event(_command_started("/bin/bash -lc 'rg --files /workspace'"))
    assert len(events) == 1
    requested = events[0]
    assert isinstance(requested, ToolCallRequested)
    assert requested.arguments["command"] == "/bin/bash -lc 'rg --files /workspace'"


def test_file_change_summary_is_russian_and_relativized():
    root = Path("/data/airruntime-projects/proj-1")
    payload = {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "file_change",
            "changes": [
                {"path": "/workspace/public/app.js"},
                {"path": "/workspace/public/index.html"},
            ],
        },
    }
    events = _map_event(payload, workspace_root=root)
    result = events[0]
    assert isinstance(result, ToolCallResult)
    assert result.summary == "Изменено: public/app.js, public/index.html"


def test_agent_message_completed_yields_text_delta():
    payload = {
        "type": "item.completed",
        "item": {"id": "item_3", "type": "agent_message", "text": "Привет"},
    }
    events = _map_event(payload)
    assert events == [TextDelta(text="Привет")]


@pytest.mark.asyncio
async def test_idle_timeout_stops_the_attempt_container(monkeypatch) -> None:
    class _Redis:
        def blpop(self, key, timeout):  # noqa: ANN001, ARG002
            return None

    stopped = []

    async def _fake_cancel(**kwargs):  # noqa: ANN003
        stopped.append(kwargs)
        return True

    monkeypatch.setattr(codex_runtime, "_redis", lambda: _Redis())
    monkeypatch.setattr(codex_runtime, "_MAX_IDLE_SECONDS", -1)
    monkeypatch.setattr(codex_runtime, "cancel_codex_run", _fake_cancel)

    events = [
        event
        async for event in codex_runtime._stream_events(
            "attempt-id",
            timeout_seconds=60,
            project_id="project-id",
        )
    ]

    assert stopped == [{"project_id": "project-id", "correlation_id": "attempt-id"}]
    assert events[0]["type"] == "infra_error"
