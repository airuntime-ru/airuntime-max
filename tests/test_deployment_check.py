"""Tests for post-deploy repair eligibility and worker-inline docker control."""

from src.services.deployment_check import detect_runtime_errors, is_repairable_app_error
from src.services.docker_control_queue import (
    in_worker_inline_docker,
    submit_control_job,
    worker_inline_docker,
)


def test_detect_runtime_errors_finds_asyncio_nested_loop():
    logs = (
        "INFO starting\n"
        "Traceback (most recent call last):\n"
        '  File "services/scheduler.py", line 10, in start\n'
        "    asyncio.run(main())\n"
        "RuntimeError: asyncio.run() cannot be called from a running event loop\n"
    )
    excerpt = detect_runtime_errors(logs)
    assert excerpt is not None
    assert "RuntimeError: asyncio.run()" in excerpt


def test_is_repairable_app_error_for_runtime_traceback():
    text = (
        "Контейнер запустился, но сразу выдал ошибку в runtime-логах:\n"
        "Traceback (most recent call last):\n"
        '  File "services/scheduler.py", line 10, in start\n'
        "RuntimeError: asyncio.run() cannot be called from a running event loop\n"
    )
    assert is_repairable_app_error(text) is True


def test_is_repairable_skips_infra_timeout():
    assert is_repairable_app_error("Deployment timed out after 120s (stuck in running)") is False
    assert is_repairable_app_error("Timed out waiting for deployment after 300s") is False


def test_is_repairable_skips_docker_daemon_but_keeps_traceback():
    assert (
        is_repairable_app_error(
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
        )
        is False
    )
    mixed = (
        "Cannot connect to the Docker daemon\n"
        "Traceback (most recent call last):\n"
        "ImportError: No module named 'foo'\n"
    )
    # App traceback wins when both are present.
    assert is_repairable_app_error(mixed) is True


def test_submit_control_job_runs_inline_inside_worker(monkeypatch):
    calls: list[tuple] = []

    def fake_run(*, action, project_id, extra=None):
        calls.append((action, project_id, extra))
        return {"ok": True, "log": "inline"}

    monkeypatch.setattr(
        "src.services.docker_control_actions.run_control_action",
        fake_run,
    )

    assert in_worker_inline_docker() is False
    with worker_inline_docker():
        assert in_worker_inline_docker() is True
        result = submit_control_job(action="build_check", project_id="proj-1", timeout_seconds=1)
    assert result == {"ok": True, "log": "inline"}
    assert calls == [("build_check", "proj-1", None)]
