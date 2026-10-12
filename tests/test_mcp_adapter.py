import sys
from pathlib import Path

import pytest

from repopilot.application import run_task
from repopilot.config import McpServer, Settings
from repopilot.errors import RepoPilotError
from repopilot.mcp_adapter import model_tool_name, supported_schema
from repopilot.mcp_connection import McpConnection
from repopilot.models import ModelTurn, ToolCall
from repopilot.providers import ScriptedFake
from repopilot.store import SessionStore


class Approve:
    async def confirm(self, action):
        return True


def settings(tmp_path):
    return Settings(
        mcp={
            "docs": McpServer(
                executable=sys.executable,
                args=["-m", "repopilot.mcp_sample", "--root", str(tmp_path)],
                tools=["get_project_conventions"],
            )
        }
    )


async def test_real_mcp_uses_registry_validation_approval_and_trace(tmp_path):
    (tmp_path / "CONVENTIONS.md").write_text("Use current hashes.")
    fake = ScriptedFake(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="external",
                        name=model_tool_name("docs", "get_project_conventions"),
                        arguments='{"topic":"editing"}',
                    )
                ]
            ),
            ModelTurn(content="Project requires current hashes."),
        ]
    )
    home = tmp_path / "state"
    report = await run_task(
        "get conventions",
        tmp_path,
        home,
        settings(tmp_path),
        fake,
        mcp_names=["docs"],
        approval=Approve(),
    )
    assert report.status == "completed" and report.tool_calls == 1
    assert "Use current hashes" in fake.requests[-1][-1].content
    store = SessionStore(home)
    try:
        events = store.events(report.run_id)
        assert len([e for e in events if e["kind"] == "approval"]) == 2
        assert any(
            e["kind"] == "tool_intent" and e["data"]["intent"]["kind"] == "mcp_call" for e in events
        )
    finally:
        store.close()


async def test_startup_denied_core_still_works(tmp_path):
    fake = ScriptedFake([ModelTurn(content="No external tool available.")])
    report = await run_task(
        "hello", tmp_path, tmp_path / "state", settings(tmp_path), fake, mcp_names=["docs"]
    )
    assert report.status == "completed" and "USER_DENIED" in report.warnings[0]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "https://evil/schema"},
        {"type": "object", "patternProperties": {}},
        {"type": "array"},
    ],
)
def test_unsupported_schema_rejected(schema):
    with pytest.raises(RepoPilotError):
        supported_schema(schema)


async def test_invalid_external_args_never_reach_call_approval(tmp_path):
    (tmp_path / "CONVENTIONS.md").write_text("fixture")
    fake = ScriptedFake(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="invalid",
                        name=model_tool_name("docs", "get_project_conventions"),
                        arguments='{"topic":42}',
                    )
                ]
            ),
            ModelTurn(content="Invalid argument, no call made."),
        ]
    )
    report = await run_task(
        "conventions",
        tmp_path,
        tmp_path / "state",
        settings(tmp_path),
        fake,
        mcp_names=["docs"],
        approval=Approve(),
    )
    assert '"error_code":"INVALID_ARGUMENTS"' in fake.requests[-1][-1].content
    store = SessionStore(tmp_path / "state")
    try:
        assert len([e for e in store.events(report.run_id) if e["kind"] == "approval"]) == 1
    finally:
        store.close()


@pytest.mark.parametrize("name,args", [("slow", {"seconds": 5}), ("die", {})])
async def test_timeout_or_disconnect_disables_connection(tmp_path, name, args):
    script = Path(__file__).parent / "fixtures/mcp_faults.py"
    connection = McpConnection(
        McpServer(executable=sys.executable, args=[str(script)], timeout=2), tmp_path
    )
    try:
        await connection.open()
        with pytest.raises(RepoPilotError) as error:
            await connection.call(name, args)
        assert error.value.code == "MCP_OUTCOME_UNKNOWN" and not connection.active
        with pytest.raises(RepoPilotError) as repeated:
            await connection.call(name, args)
        assert repeated.value.code == "MCP_DISABLED"
    finally:
        await connection.close()
