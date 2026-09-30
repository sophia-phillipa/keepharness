"""Synthetic stdio MCP server: no network, credentials, or external effects."""

import json
import sys
import time


def respond(request):
    method = request.get("method")
    if method == "initialize":
        return {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "synthetic-effects", "version": "1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "prepare",
                    "description": "Return a synthetic pending request ID immediately. No effects.",
                    "inputSchema": {"type": "object", "properties": {}},
                    "annotations": {"readOnlyHint": True, "destructiveHint": False},
                },
                {
                    "name": "block",
                    "description": "Wait 95 seconds then return a synthetic result. No effects.",
                    "inputSchema": {"type": "object", "properties": {}},
                    "annotations": {"readOnlyHint": True, "destructiveHint": False},
                },
            ]
        }
    if method in ("resources/list", "resources/templates/list", "prompts/list"):
        return {
            {
                "resources/list": "resources",
                "resources/templates/list": "resourceTemplates",
                "prompts/list": "prompts",
            }[method]: []
        }
    if method == "tools/call":
        name = request["params"]["name"]
        if name not in ("prepare", "block"):
            raise ValueError("Unknown synthetic tool")
        started = time.monotonic()
        if name == "block":
            time.sleep(95)
        payload = {
            "request_id": "synthetic-request-001",
            "status": "pending" if name == "prepare" else "finished",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
    raise ValueError("Unsupported synthetic method")


def main():
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            response = {"jsonrpc": "2.0", "id": request["id"], "result": respond(request)}
        except (KeyError, ValueError):
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": "Unsupported synthetic request"},
            }
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
