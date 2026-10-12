"""Fault-injection server, only used by integration tests."""

import asyncio
import os

from mcp.server import MCPServer

server = MCPServer("Fault fixture")


@server.tool()
async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "done"


@server.tool()
def die() -> str:
    os._exit(3)


if __name__ == "__main__":
    server.run(transport="stdio")
