from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .context import get_client_context

TOOL_NAME = "get_client_context"


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any], db_path: Path) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _response(request_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "corridor-context", "version": "0.1.0"},
        })
    if method == "tools/list":
        return _response(request_id, {"tools": [tool_contract()]})
    if method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") != TOOL_NAME:
            return _error(request_id, -32602, f"Unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        try:
            context = get_client_context(db_path, args.get("client", ""), bool(args.get("includeHistory", False)))
        except ValueError as exc:
            return _error(request_id, -32602, str(exc))
        except KeyError as exc:
            return _error(request_id, -32004, str(exc))
        return _response(request_id, {
            "content": [{"type": "text", "text": json.dumps(context, indent=2, sort_keys=True)}],
            "isError": False,
        })
    return _error(request_id, -32601, f"Unsupported method: {method}")


def tool_contract() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Read the current persisted client context, including provenance and optional history.",
        "inputSchema": {
            "type": "object",
            "required": ["client"],
            "properties": {
                "client": {"type": "string", "description": "Client name or normalized key."},
                "includeHistory": {"type": "boolean", "default": False},
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Corridor client context MCP stdio server.")
    parser.add_argument("--db", type=Path, default=Path("data/client_context.sqlite3"))
    args = parser.parse_args()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle(request, args.db)
        except Exception as exc:
            response = _error(None, -32700, str(exc))
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
