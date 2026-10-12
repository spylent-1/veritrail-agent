import sys

import pytest

from repopilot.config import McpServer
from repopilot.errors import RepoPilotError
from repopilot.mcp_connection import McpConnection


async def test_real_stdio_server_lifecycle(tmp_path):
    (tmp_path / "CONVENTIONS.md").write_text("Use current hashes before edits.")
    connection = McpConnection(
        McpServer(
            executable=sys.executable, args=["-m", "repopilot.mcp_sample", "--root", str(tmp_path)]
        ),
        tmp_path,
    )
    try:
        tools = await connection.open()
        assert [tool.name for tool in tools] == ["get_project_conventions"]
        result = await connection.call("get_project_conventions", {"topic": "editing"})
        assert not result.is_error
        assert "current hashes" in result.content[0].text
    finally:
        await connection.close()
    assert connection.active is False


async def test_bad_server_does_not_leak_exception_body(tmp_path):
    connection = McpConnection(
        McpServer(
            executable=sys.executable, args=["-c", "raise RuntimeError('secret')"], timeout=2
        ),
        tmp_path,
    )
    with pytest.raises(RepoPilotError) as error:
        await connection.open()
    assert error.value.code == "MCP_UNAVAILABLE" and "secret" not in str(error.value)
