import asyncio

import pytest

from repopilot.agent import AgentRunner
from repopilot.config import Limits, StrictModel
from repopilot.context import ContextBuilder
from repopilot.errors import RepoPilotError
from repopilot.models import Message, ModelTurn, ToolCall, ToolResult, validate_messages
from repopilot.providers import ScriptedFake
from repopilot.safety import PolicyEngine, PreparedAction
from repopilot.tools import ToolDefinition, ToolExecutor, ToolOutput, ToolRegistry


def group(identifier, content):
    return [
        Message(
            role="assistant", tool_calls=[ToolCall(id=identifier, name="read", arguments="{}")]
        ),
        ToolResult(
            call_id=identifier, data={"text": content, "path": "a.py", "sha256": "h"}
        ).message_for_model(),
    ]


def test_context_crops_old_payload_without_rewriting_history():
    history = [
        Message(role="system", content="rules"),
        Message(role="user", content="old"),
        *group("old", "x" * 5000),
        Message(role="assistant", content="done"),
        Message(role="user", content="current"),
        *group("new", "important current evidence"),
    ]
    original = history[3].content
    result = ContextBuilder(1700).build(history, [])
    assert result.bytes <= 1700 and history[3].content == original
    assert "important current evidence" in result.messages[-1].content
    validate_messages(result.messages)


def test_oversized_current_task_stops_instead_of_dropping_rules():
    with pytest.raises(RepoPilotError, match="cannot fit"):
        ContextBuilder(1024).build(
            [Message(role="system", content="rules"), Message(role="user", content="x" * 5000)], []
        )


async def test_approval_wait_does_not_consume_active_budget():
    class Approval:
        async def confirm(self, action):
            await asyncio.sleep(0.1)
            return True

    async def commit():
        return ToolOutput({"ok": True})

    async def prepare(args):
        return PreparedAction("write", "fixture", {}, commit)

    async def no_bypass(args):
        raise AssertionError()

    registry = ToolRegistry()
    registry.register(ToolDefinition("change", "fixture", StrictModel, no_bypass, "write", prepare))
    limits = Limits(active_seconds=0.08)
    executor = ToolExecutor(registry, limits, policy=PolicyEngine("edit"), approval=Approval())
    agent = AgentRunner(
        ScriptedFake(
            [
                ModelTurn(tool_calls=[ToolCall(id="c", name="change", arguments="{}")]),
                ModelTurn(content="done"),
            ]
        ),
        executor,
        limits,
    )
    assert (await agent.run("change")).status == "completed"


async def test_cancelled_group_has_no_dangling_calls():
    class Approval:
        async def confirm(self, action):
            raise asyncio.CancelledError

    async def prepare(args):
        async def commit():
            raise AssertionError("cancelled commit ran")

        return PreparedAction("write", "fixture", {}, commit)

    async def no_bypass(args):
        raise AssertionError()

    registry = ToolRegistry()
    registry.register(ToolDefinition("change", "fixture", StrictModel, no_bypass, "write", prepare))
    executor = ToolExecutor(registry, Limits(), policy=PolicyEngine("edit"), approval=Approval())
    agent = AgentRunner(
        ScriptedFake(
            [
                ModelTurn(
                    tool_calls=[
                        ToolCall(id="a", name="change", arguments="{}"),
                        ToolCall(id="b", name="change", arguments="{}"),
                    ]
                )
            ]
        ),
        executor,
        Limits(),
    )
    assert (await agent.run("change")).status == "cancelled"
    validate_messages(agent.messages)
