"""DB-backed MCP server allowlist (db/models/mcp_server.py) + capability discovery/caching.

Two independent gates, both required before any MCP capability is ever invoked (spec section
11: "серверы подключаются только через admin allowlist; capability отдельно разрешается по
project policy"):
  1. `McpServer.enabled` - an admin allowlist entry exists and is turned on. Managed via Django
     admin (reads/writes the same Postgres tables) or a future API - not by any agent/LLM.
  2. `allowed_roles(server)` - which SpecialistRole(s) may use this server at all, further
     narrowed per-task by role_policy.py's own allowed_capabilities computation.

These two gates are the only authorization layer - there is no separate blanket on/off switch;
MCP capabilities are always in the routing mix, individually allowlisted per server and role.
"""

from __future__ import annotations

import json
import logging
import os

from src.db.models.mcp_server import McpServer
from src.services.orchestration.mcp.client import HttpTransport, McpClient, StdioTransport
from src.services.orchestration.repository import McpServerRepository
from src.services.orchestration.schemas import CapabilityDefinition, RiskLevel, SpecialistRole
from src.services.system_settings import get_system_setting_value

logger = logging.getLogger(__name__)

_VALID_RISK_LEVELS = {level.value for level in RiskLevel}

# Process-lifetime caches - a server's tool list rarely changes mid-run, and re-discovering on
# every task would mean an extra round trip per capability lookup. Cleared via clear_cache()
# (tests) or naturally on process restart.
_client_cache: dict[str, McpClient] = {}
_capability_cache: dict[str, list[CapabilityDefinition]] = {}


def resolve_credential(server: McpServer) -> str | None:
    """Turn McpServer.credential_ref into the actual secret value, server-side.

    `credential_ref` is a *key* into the admin-managed system settings store, never the value
    itself (see the model docstring). Resolution happens here, at connection-build time, so the
    real credential only ever exists inside this process and the transport - it is never part of
    a CapabilityDefinition, a TaskContract, tool arguments, or anything an LLM can observe, which
    is the same boundary request_secret enforces for project secrets.
    """
    ref = (server.credential_ref or "").strip()
    if not ref:
        return None
    value = get_system_setting_value(ref)
    if not value:
        logger.warning(
            "MCP server %s declares credential_ref %r but no such system setting is set - "
            "connecting without credentials",
            server.name,
            ref,
        )
        return None
    return value


def _build_client(server: McpServer) -> McpClient:
    credential = resolve_credential(server)
    if server.transport == "stdio":
        # Passed as an env var rather than on the command line: argv is world-readable via
        # /proc on Linux, so an inline secret would leak to any process on the host.
        env = {**os.environ, "MCP_CREDENTIAL": credential} if credential else None
        transport = StdioTransport(server.endpoint or "", env=env)
    elif server.transport == "http":
        transport = HttpTransport(server.endpoint or "", auth_token=credential)
    else:
        raise ValueError(
            f"unsupported MCP transport {server.transport!r} for server {server.name!r}"
        )
    return McpClient(name=server.name, transport=transport)


def get_client(server: McpServer) -> McpClient:
    if server.name not in _client_cache:
        _client_cache[server.name] = _build_client(server)
    return _client_cache[server.name]


async def discover_capabilities(
    server: McpServer, *, force: bool = False
) -> list[CapabilityDefinition]:
    if not force and server.name in _capability_cache:
        return _capability_cache[server.name]
    client = get_client(server)
    tools = await client.list_tools()
    risk = (
        RiskLevel(server.risk_level)
        if server.risk_level in _VALID_RISK_LEVELS
        else RiskLevel.MEDIUM
    )
    definitions = [
        CapabilityDefinition(
            id=f"mcp:{server.name}:{tool.name}",
            title=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema={},
            risk_level=risk,
            side_effects=True,
            source="mcp",
        )
        for tool in tools
    ]
    _capability_cache[server.name] = definitions
    return definitions


def allowed_roles(server: McpServer) -> set[SpecialistRole]:
    if not server.allowed_roles_json:
        return set(SpecialistRole)
    try:
        raw = json.loads(server.allowed_roles_json)
    except (ValueError, TypeError):
        return set()
    return {SpecialistRole(r) for r in raw if r in {role.value for role in SpecialistRole}}


async def list_capabilities_for_role(
    repo: McpServerRepository, *, role: SpecialistRole
) -> list[tuple[McpServer, CapabilityDefinition]]:
    """Every (server, capability) pair `role` is allowed to see - the input to
    capability_router.py's MCP-match step. Discovery failures for one server are logged and
    skipped rather than failing the whole lookup, so one misconfigured server can't block every
    other allowlisted one."""
    results: list[tuple[McpServer, CapabilityDefinition]] = []
    for server in repo.list_enabled():
        if role not in allowed_roles(server):
            continue
        try:
            capabilities = await discover_capabilities(server)
        except Exception:
            logger.warning(
                "mcp: capability discovery failed for server %s", server.name, exc_info=True
            )
            continue
        results.extend((server, capability) for capability in capabilities)
    return results


async def close_all_clients() -> None:
    """Cleanly terminate every cached stdio/http client - call on worker/backend shutdown, and
    from test teardowns (bare `clear_cache()` would otherwise just drop references to live
    subprocesses without terminating them)."""
    for client in _client_cache.values():
        try:
            await client.close()
        except Exception:
            logger.warning("mcp: error closing client %s", client.name, exc_info=True)
    clear_cache()


def clear_cache() -> None:
    _client_cache.clear()
    _capability_cache.clear()
