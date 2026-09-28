"""Tests for services/orchestration/mcp/. The client/transport tests spawn the real
fake_server.py as a subprocess and speak real stdio JSON-RPC to it - this exercises the actual
StdioTransport code path, not a mocked interface. Registry tests use the real test DB."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from src.db.models.mcp_server import McpServer
from src.services.orchestration.mcp import registry
from src.services.orchestration.mcp.client import (
    McpCircuitOpenError,
    McpClient,
    McpError,
    StdioTransport,
)
from src.services.orchestration.repository import McpServerRepository
from src.services.orchestration.schemas import SpecialistRole

_FAKE_SERVER_PATH = str(
    Path(__file__).resolve().parents[1]
    / "backend"
    / "src"
    / "services"
    / "orchestration"
    / "mcp"
    / "fake_server.py"
)


def _fake_client(**kwargs) -> McpClient:  # noqa: ANN003
    transport = StdioTransport(f"{sys.executable} {_FAKE_SERVER_PATH}")
    return McpClient(name="fake", transport=transport, **kwargs)


@pytest.mark.asyncio
class TestMcpClientAgainstRealFakeServer:
    async def test_initialize_and_list_tools(self) -> None:
        client = _fake_client()
        try:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert names == {"echo", "fail_once", "slow"}
        finally:
            await client.close()

    async def test_call_tool_echo_round_trips_arguments(self) -> None:
        client = _fake_client()
        try:
            result = await client.call_tool("echo", {"x": 1, "y": "hello"})
            assert result.ok is True
            assert result.content == {"echo": {"x": 1, "y": "hello"}}
        finally:
            await client.close()

    async def test_unknown_tool_reports_error_content_not_exception(self) -> None:
        client = _fake_client()
        try:
            result = await client.call_tool("does_not_exist", {})
            assert result.ok is False
        finally:
            await client.close()

    async def test_retries_transparently_recover_from_transient_failure(self) -> None:
        client = _fake_client(max_retries=2)
        try:
            # fake_server's "fail_once" errors on the very first call in the process, then
            # succeeds - McpClient must retry and return the eventual success, not the error.
            result = await client.call_tool("fail_once", {})
            assert result.ok is True
        finally:
            await client.close()

    async def test_timeout_is_reported_as_a_failed_result_not_an_exception(self) -> None:
        # call_tool() deliberately never raises for a failed/timed-out tool call - it reports
        # ok=False so capability_provider.py doesn't need try/except around every invocation.
        # list_tools()/initialize() (below) DO raise, since there's no partial-success shape
        # for those.
        client = _fake_client(timeout_seconds=0.5, max_retries=0)
        try:
            result = await client.call_tool("slow", {"seconds": 3})
            assert result.ok is False
            assert "timed out" in (result.error or "")
        finally:
            await client.close()

    async def test_list_tools_raises_on_timeout(self) -> None:
        client = _fake_client(timeout_seconds=0.001, max_retries=0)
        try:
            with pytest.raises(McpError):
                await client.list_tools()
        finally:
            await client.close()

    async def test_circuit_opens_after_repeated_failures(self) -> None:
        transport = StdioTransport(f'{sys.executable} -c "import sys; sys.exit(1)"')
        client = McpClient(name="broken", transport=transport, timeout_seconds=0.4, max_retries=0)
        for _ in range(5):
            with pytest.raises(McpError):
                await client.list_tools()
        with pytest.raises(McpCircuitOpenError):
            await client.list_tools()

    async def test_result_over_size_limit_is_truncated(self) -> None:
        client = _fake_client()
        try:
            import src.services.orchestration.mcp.client as client_module

            original = client_module.MAX_RESULT_BYTES
            client_module.MAX_RESULT_BYTES = 10
            try:
                result = await client.call_tool("echo", {"payload": "x" * 1000})
            finally:
                client_module.MAX_RESULT_BYTES = original
            assert result.truncated is True
        finally:
            await client.close()


@pytest.fixture(autouse=True)
def _clear_mcp_cache():
    # Not awaiting close_all_clients() here on purpose: this fixture also wraps plain sync
    # tests in this file, and pytest-asyncio's strict mode needs `pytest_asyncio.fixture` for
    # a fixture body to await anything. The resulting "unclosed transport" ResourceWarning at
    # interpreter/GC time for leftover test subprocesses is cosmetic - registry.py's real
    # lifecycle (worker shutdown) calls close_all_clients() explicitly; see engine.py.
    registry.clear_cache()
    yield
    registry.clear_cache()


class TestMcpRegistry:
    def test_disabled_server_is_never_listed(self, db: Session) -> None:
        db.add(McpServer(name="s1", transport="stdio", enabled=False))
        db.flush()
        repo = McpServerRepository(db)
        assert repo.list_enabled() == []

    def test_allowed_roles_defaults_to_every_role_when_unset(self, db: Session) -> None:
        server = McpServer(name="s1", transport="stdio", enabled=True)
        assert registry.allowed_roles(server) == set(SpecialistRole)

    def test_allowed_roles_respects_explicit_list(self, db: Session) -> None:
        server = McpServer(
            name="s1",
            transport="stdio",
            enabled=True,
            allowed_roles_json='["implementer", "qa_reviewer"]',
        )
        assert registry.allowed_roles(server) == {
            SpecialistRole.IMPLEMENTER,
            SpecialistRole.QA_REVIEWER,
        }

    @pytest.mark.asyncio
    async def test_list_capabilities_for_role_uses_real_fake_server(self, db: Session) -> None:
        server = McpServer(
            name="fake",
            transport="stdio",
            endpoint=f"{sys.executable} {_FAKE_SERVER_PATH}",
            enabled=True,
            allowed_roles_json='["implementer"]',
        )
        db.add(server)
        db.flush()
        repo = McpServerRepository(db)

        for_implementer = await registry.list_capabilities_for_role(
            repo, role=SpecialistRole.IMPLEMENTER
        )
        assert {cap.id for _srv, cap in for_implementer} == {
            "mcp:fake:echo",
            "mcp:fake:fail_once",
            "mcp:fake:slow",
        }

        for_reviewer = await registry.list_capabilities_for_role(
            repo, role=SpecialistRole.QA_REVIEWER
        )
        assert for_reviewer == []

    @pytest.mark.asyncio
    async def test_discovery_failure_for_one_server_does_not_block_others(
        self, db: Session
    ) -> None:
        broken = McpServer(
            name="broken", transport="stdio", endpoint="nonexistent-binary-xyz", enabled=True
        )
        working = McpServer(
            name="fake2",
            transport="stdio",
            endpoint=f"{sys.executable} {_FAKE_SERVER_PATH}",
            enabled=True,
        )
        db.add_all([broken, working])
        db.flush()
        repo = McpServerRepository(db)

        results = await registry.list_capabilities_for_role(repo, role=SpecialistRole.IMPLEMENTER)
        server_names = {srv.name for srv, _cap in results}
        assert server_names == {"fake2"}


class TestRateLimiter:
    """Distinct from the circuit breaker: caps call VOLUME even when every call succeeds, so a
    runaway retry/replan loop cannot hammer a third-party server (spec section 11)."""

    def test_allows_calls_up_to_the_cap_then_refuses(self) -> None:
        from src.services.orchestration.mcp.client import McpRateLimitedError, _RateLimiter

        limiter = _RateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            limiter.before_call()
        with pytest.raises(McpRateLimitedError):
            limiter.before_call()

    def test_window_rollover_restores_the_budget(self, monkeypatch) -> None:
        from src.services.orchestration.mcp import client as client_module
        from src.services.orchestration.mcp.client import McpRateLimitedError, _RateLimiter

        now = [1000.0]
        monkeypatch.setattr(client_module.time, "monotonic", lambda: now[0])
        limiter = _RateLimiter(max_calls=1, window_seconds=60)
        limiter.before_call()
        with pytest.raises(McpRateLimitedError):
            limiter.before_call()
        now[0] += 61
        limiter.before_call()  # new window - must not raise


class TestCredentialResolution:
    """credential_ref is a KEY into the admin settings store, never the value - and the resolved
    value must never leave the transport (spec section 11's "секрет передаётся capability через
    secure reference, а не LLM")."""

    def test_resolves_ref_through_system_settings(self, monkeypatch) -> None:
        from src.db.models.mcp_server import McpServer
        from src.services.orchestration.mcp import registry

        monkeypatch.setattr(
            registry,
            "get_system_setting_value",
            lambda key: "s3cr3t-value" if key == "mcp:github:token" else None,
        )
        server = McpServer(
            name="github", transport="http", endpoint="https://x", credential_ref="mcp:github:token"
        )
        assert registry.resolve_credential(server) == "s3cr3t-value"

    def test_missing_ref_is_none_not_an_error(self, monkeypatch) -> None:
        from src.db.models.mcp_server import McpServer
        from src.services.orchestration.mcp import registry

        monkeypatch.setattr(registry, "get_system_setting_value", lambda key: None)
        server = McpServer(name="x", transport="http", endpoint="https://x", credential_ref=None)
        assert registry.resolve_credential(server) is None

    def test_unresolvable_ref_degrades_instead_of_crashing(self, monkeypatch) -> None:
        from src.db.models.mcp_server import McpServer
        from src.services.orchestration.mcp import registry

        monkeypatch.setattr(registry, "get_system_setting_value", lambda key: None)
        server = McpServer(
            name="x", transport="http", endpoint="https://x", credential_ref="mcp:x:absent"
        )
        assert registry.resolve_credential(server) is None

    def test_http_transport_sends_the_credential_as_a_bearer_header(self) -> None:
        from src.services.orchestration.mcp.client import HttpTransport

        plain = HttpTransport("https://x")
        authed = HttpTransport("https://x", auth_token="tok")
        assert plain._auth_token is None
        assert authed._auth_token == "tok"
