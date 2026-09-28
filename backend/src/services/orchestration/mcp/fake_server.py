"""Standalone (stdlib-only, no `src.*` imports) fake MCP server for integration tests -
speaks the exact stdio JSON-RPC wire protocol `client.py`'s `StdioTransport` implements, so
tests exercise the REAL client/transport code end to end, not a mocked interface.

Run directly: `python fake_server.py` (or spawned by StdioTransport via
`sys.executable <this file>`). Deliberately has zero dependency on the rest of the codebase so
it can be spawned as a subprocess without PYTHONPATH gymnastics.

Exposes three tools for tests to target:
  - "echo": always succeeds, returns {"echo": <arguments>}.
  - "fail_once": fails with a JSON-RPC error on the first call in the process's lifetime,
    succeeds on every call after - exercises McpClient's retry path.
  - "slow": sleeps for `arguments["seconds"]` before responding - exercises timeout handling.
"""

from __future__ import annotations

import json
import sys
import time

_fail_once_state = {"called": False}

_TOOLS = [
    {
        "name": "echo",
        "description": "Echoes back the given arguments",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "fail_once",
        "description": "Fails the first call, succeeds afterward",
        "inputSchema": {"type": "object"},
    },
    {"name": "slow", "description": "Sleeps before responding", "inputSchema": {"type": "object"}},
]


def _handle(request: dict) -> dict:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "airuntime-fake-mcp", "version": "1.0"},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": _TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": {"echo": arguments}, "isError": False},
            }
        if name == "fail_once":
            if not _fail_once_state["called"]:
                _fail_once_state["called"] = True
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "simulated failure"},
                }
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": {"ok": True}, "isError": False},
            }
        if name == "slow":
            time.sleep(float(arguments.get("seconds", 5)))
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": {"ok": True}, "isError": False},
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": {"error": f"unknown tool {name!r}"}, "isError": True},
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"unknown method {method!r}"},
    }


def main() -> None:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        response = _handle(request)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
