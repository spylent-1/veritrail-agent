"""Official SDK stdio lifecycle. Legacy framing avoids high-level automatic tool retries."""

import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TextIO

from mcp import Client, StdioServerParameters, stdio_client, types

from repopilot.config import McpServer
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject
from repopilot.process import child_environment


class McpConnection:
    def __init__(self, settings: McpServer, cwd: Path) -> None:
        self.settings = settings
        self.cwd = cwd
        self.stack = AsyncExitStack()
        self.client: Client | None = None
        self.active = False
        self.errlog: TextIO | None = None

    async def open(self) -> list[types.Tool]:
        executable = Path(self.settings.executable)
        if (
            not executable.is_absolute()
            or not executable.is_file()
            or executable.suffix.lower() in (".bat", ".cmd")
        ):
            raise RepoPilotError("MCP_EXECUTABLE", "Configure an absolute native MCP executable.")
        self.errlog = self.stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
        server = StdioServerParameters(
            command=str(executable), args=self.settings.args, env=child_environment(), cwd=self.cwd
        )
        try:
            self.client = await self.stack.enter_async_context(
                Client(
                    stdio_client(server, errlog=self.errlog),
                    mode="legacy",
                    cache=None,
                    read_timeout_seconds=self.settings.timeout,
                )
            )
            self.active = True
            tools: list[types.Tool] = []
            cursor: str | None = None
            for _ in range(10):
                result = await self.client.list_tools(cursor=cursor)
                tools.extend(result.tools)
                cursor = result.next_cursor
                if not cursor:
                    return tools
            raise RepoPilotError("MCP_TOOL_LIMIT", "Too many MCP tool listing pages.")
        except Exception:
            self.active = False
            await self.close()
            raise RepoPilotError("MCP_UNAVAILABLE", "MCP server could not initialize.") from None

    async def call(self, name: str, arguments: JsonObject) -> types.CallToolResult:
        if not self.active or self.client is None:
            raise RepoPilotError(
                "MCP_DISABLED", "MCP connection is unavailable; no automatic retry."
            )
        try:
            # Session API sends one call; no high-level re-list or input-required replay.
            return await self.client.session.call_tool(
                name, arguments, read_timeout_seconds=self.settings.timeout
            )
        except Exception:
            self.active = False
            raise RepoPilotError(
                "MCP_OUTCOME_UNKNOWN", "MCP call failed; outcome is unknown."
            ) from None

    async def close(self) -> None:
        self.active = False
        await self.stack.aclose()
        self.client = None
