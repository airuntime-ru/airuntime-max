"""Tests for services/orchestration/cancellation.py and the cancellation wiring it enables in
docker_control_actions.py (real container stop, via a fake Docker client matching
test_deployment_adapter.py's own `_FakeContainer` shape) and loop.py (cooperative HTTP-path
cancellation, via a minimal fake provider)."""

from __future__ import annotations

import asyncio

import pytest

from src.services.orchestration import cancellation


class TestCancellationToken:
    def test_starts_not_cancelled(self) -> None:
        token = cancellation.CancellationToken()
        assert token.is_cancelled is False
        assert token.reason is None

    def test_cancel_sets_flag_and_reason(self) -> None:
        token = cancellation.CancellationToken()
        token.cancel("user clicked stop")
        assert token.is_cancelled is True
        assert token.reason == "user clicked stop"

    def test_default_reason(self) -> None:
        token = cancellation.CancellationToken()
        token.cancel()
        assert token.reason == "Cancelled by user"

    @pytest.mark.asyncio
    async def test_wait_unblocks_on_cancel(self) -> None:
        token = cancellation.CancellationToken()

        async def _canceller():
            await asyncio.sleep(0.05)
            token.cancel("go")

        waiter = asyncio.ensure_future(token.wait())
        asyncio.ensure_future(_canceller())
        await asyncio.wait_for(waiter, timeout=2)
        assert token.is_cancelled is True


@pytest.mark.asyncio
class TestCancelCodexRun:
    async def test_success_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cancellation, "submit_control_job", lambda **kwargs: {"ok": True, "found": True}
        )  # noqa: ANN003
        result = await cancellation.cancel_codex_run(project_id="p1", correlation_id="abc")
        assert result is True

    async def test_worker_unreachable_returns_false_not_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cancellation, "submit_control_job", lambda **kwargs: None)  # noqa: ANN003
        result = await cancellation.cancel_codex_run(project_id="p1", correlation_id="abc")
        assert result is False

    async def test_passes_correlation_id_through_to_rpc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = {}

        def _fake_submit(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"ok": True}

        monkeypatch.setattr(cancellation, "submit_control_job", _fake_submit)
        await cancellation.cancel_codex_run(project_id="proj-1", correlation_id="task-xyz")
        assert captured["action"] == "cancel_codex_run"
        assert captured["project_id"] == "proj-1"
        assert captured["extra"] == {"correlation_id": "task-xyz"}


class _FakeCancelContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.stopped = False

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True


class _FakeCancelContainers:
    def __init__(self, existing: dict[str, _FakeCancelContainer]) -> None:
        self._existing = existing

    def get(self, name: str) -> _FakeCancelContainer:
        if name not in self._existing:
            from docker.errors import NotFound

            raise NotFound(f"no such container: {name}")
        return self._existing[name]

    def list(self, all: bool = False, filters: dict | None = None) -> list[_FakeCancelContainer]:
        label = (filters or {}).get("label", "")
        if not label.startswith("airuntime.codex_correlation_id="):
            return []
        correlation_id = label.split("=", 1)[1]
        return [
            container
            for name, container in self._existing.items()
            if name.startswith(f"attempt:{correlation_id}:")
        ]


class _FakeCancelDockerClient:
    def __init__(self, existing: dict[str, _FakeCancelContainer]) -> None:
        self.containers = _FakeCancelContainers(existing)


class TestCancelCodexRunControlAction:
    def test_stops_the_matching_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services import docker_control_actions

        container = _FakeCancelContainer("airuntime-codex-unique-attempt")
        fake_client = _FakeCancelDockerClient({"attempt:task-xyz:1": container})
        monkeypatch.setattr(
            docker_control_actions.DockerDeploymentAdapter,
            "client",
            property(lambda self: fake_client),
        )

        result = docker_control_actions.run_control_action(
            action="cancel_codex_run", project_id="p1", extra={"correlation_id": "task-xyz"}
        )
        assert result == {"ok": True, "found": True}
        assert container.stopped is True

    def test_stops_all_overlapping_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services import docker_control_actions

        first = _FakeCancelContainer("airuntime-codex-attempt-1")
        second = _FakeCancelContainer("airuntime-codex-attempt-2")
        fake_client = _FakeCancelDockerClient(
            {"attempt:task-xyz:1": first, "attempt:task-xyz:2": second}
        )
        monkeypatch.setattr(
            docker_control_actions.DockerDeploymentAdapter,
            "client",
            property(lambda self: fake_client),
        )

        result = docker_control_actions.run_control_action(
            action="cancel_codex_run", project_id="p1", extra={"correlation_id": "task-xyz"}
        )

        assert result == {"ok": True, "found": True}
        assert first.stopped is True
        assert second.stopped is True

    def test_missing_container_is_not_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services import docker_control_actions

        fake_client = _FakeCancelDockerClient({})
        monkeypatch.setattr(
            docker_control_actions.DockerDeploymentAdapter,
            "client",
            property(lambda self: fake_client),
        )

        result = docker_control_actions.run_control_action(
            action="cancel_codex_run", project_id="p1", extra={"correlation_id": "already-gone"}
        )
        assert result == {"ok": True, "found": False}

    def test_missing_correlation_id_fails_cleanly(self) -> None:
        from src.services import docker_control_actions

        result = docker_control_actions.run_control_action(
            action="cancel_codex_run", project_id="p1", extra={}
        )
        assert result["ok"] is False


@pytest.mark.asyncio
class TestHttpPathCooperativeCancellation:
    async def test_pre_cancelled_token_stops_before_any_provider_call(self) -> None:
        from src.services.agent.events import AgentDone
        from src.services.agent.loop import CodingAgentSession

        class _BoomProvider:
            def supports_tools(self) -> bool:
                raise AssertionError("must not be called once cancellation is already set")

        session = CodingAgentSession(
            provider_name="anthropic", model="m", api_key="k", workspace=None, system_prompt="s"
        )
        session.provider = _BoomProvider()
        token = cancellation.CancellationToken()
        token.cancel("stop before starting")

        events = [
            event async for event in session.run(history=[], user_message="hi", cancellation=token)
        ]
        assert len(events) == 1
        assert isinstance(events[0], AgentDone)
        assert events[0].reason == "cancelled"
