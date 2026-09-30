"""Execution-scoped stdio MCP: prepares requests, never approves or dispatches."""

import asyncio
import json
import sys

from mcp.server.fastmcp import FastMCP


async def prepare_request(config, request):
    async with asyncio.timeout(10):
        reader, writer = await asyncio.open_unix_connection(config["socket"], limit=512 * 1024)
        try:
            writer.write(
                json.dumps(
                    {"token": config["token"], "method": "prepare", "request": request}
                ).encode()
                + b"\n"
            )
            await writer.drain()
            return json.loads(await reader.readline())
        finally:
            writer.close()
            await writer.wait_closed()


def main():
    config = json.loads(sys.argv[1])
    server = FastMCP(
        "harness-effects",
        instructions="Prepare Jira create-issue requests for human review. The harness executes only after single-use human approval. Do not wait for approval in this tool. Other publication paths are unenforced.",
    )

    @server.tool()
    async def prepare(
        integration: str, operation: str, destination: str, arguments: dict, artifact: dict
    ) -> dict:
        """Store an immutable publication request and return its pending gate immediately."""
        try:
            return await prepare_request(
                config,
                dict(
                    integration=integration,
                    operation=operation,
                    destination=destination,
                    arguments=arguments,
                    artifact=artifact,
                ),
            )
        except (OSError, TimeoutError, ValueError):
            return {"error": "effect_transport_unavailable"}

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
