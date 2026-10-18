from unittest.mock import AsyncMock

import pytest

from repopilot.agent import AgentRunner
from repopilot.config import Limits
from repopilot.models import ModelTurn, ToolCall, ToolResult, validate_messages
from repopilot.providers import ScriptedFake
from repopilot.tools import ReadTools, ToolExecutor, ToolRegistry
from repopilot.workspace import Workspace


def runner(tmp_path, turns, limits=None):
    limits = limits or Limits()
    registry = ToolRegistry()
    ReadTools(Workspace(tmp_path)).register(registry)
    fake = ScriptedFake(turns)
    return AgentRunner(fake, ToolExecutor(registry, limits), limits), fake


def call(identifier="c1", arguments='{"path":"hello.py"}', name="read_file"):
    return ToolCall(id=identifier, name=name, arguments=arguments)


async def test_real_tool_loop_with_scripted_model(tmp_path):
    (tmp_path / "hello.py").write_text("answer = 42\n")
    agent, fake = runner(tmp_path, [ModelTurn(tool_calls=[call()]), ModelTurn(content="42 [E1]")])
    report = await agent.run("read hello")
    assert report.status == "completed" and report.verification == "not_run"
    assert report.tool_calls == 1 and report.model_turns == 2
    assert '"call_id":"c1"' in fake.requests[1][-1].content
    validate_messages(agent.messages)


async def test_failure_closes_every_tool_call(tmp_path):
    agent, _ = runner(
        tmp_path,
        [
            ModelTurn(tool_calls=[call("c1", "bad JSON"), call("c2")]),
            ModelTurn(content="No file evidence available."),
        ],
    )
    report = await agent.run("read")
    assert report.status == "completed" and report.tool_calls == 1
    assert '"status":"skipped"' in agent.messages[-2].content
    validate_messages(agent.messages)


async def test_duplicate_ids_do_not_execute(tmp_path):
    agent, _ = runner(tmp_path, [ModelTurn(tool_calls=[call(), call()])])
    report = await agent.run("read")
    assert report.stop_reason == "PROTOCOL_ERROR" and report.tool_calls == 0


async def test_budget_closes_group(tmp_path):
    (tmp_path / "hello.py").write_text("x = 1")
    agent, _ = runner(tmp_path, [ModelTurn(tool_calls=[call(), call("c2")])], Limits(tool_calls=1))
    report = await agent.run("read")
    assert report.status == "budget_exhausted" and report.tool_calls == 1
    validate_messages(agent.messages)


async def test_repeated_failure_stops(tmp_path):
    agent, _ = runner(tmp_path, [ModelTurn(tool_calls=[call(str(i))]) for i in range(3)])
    assert (await agent.run("read absent file")).stop_reason == "REPEATED_FAILURE"


async def test_truncated_tool_call_not_executed(tmp_path):
    agent, _ = runner(tmp_path, [ModelTurn(tool_calls=[call()], finish_reason="length")])
    report = await agent.run("read")
    assert report.stop_reason == "MODEL_INCOMPLETE" and report.tool_calls == 0


@pytest.mark.parametrize(
    "last_edit,retest,expected",
    [
        (ToolResult(call_id="e2", data={"changed_file": "a.py"}), False, "not_run"),
        (ToolResult(call_id="e2", data={"changed_file": "a.py"}), True, "passed"),
        (ToolResult(call_id="e2", status="denied"), False, "passed"),
        (ToolResult(call_id="e2", status="error", error_code="FILE_CONFLICT"), False, "passed"),
        (ToolResult(call_id="e2", status="error", error_code="NO_CHANGE"), False, "passed"),
    ],
)
async def test_verification_tracks_every_successful_write(
    tmp_path, monkeypatch, last_edit, retest, expected
):
    calls = [
        ToolCall(id="e1", name="edit_file", arguments="{}"),
        ToolCall(id="t1", name="run_tests", arguments="{}"),
        ToolCall(id="e2", name="edit_file", arguments="{}"),
    ]
    results = [
        ToolResult(call_id="e1", data={"changed_file": "a.py"}),
        ToolResult(call_id="t1", data={"verification": "passed"}),
        last_edit,
    ]
    if retest:
        calls.append(ToolCall(id="t2", name="run_tests", arguments="{}"))
        results.append(ToolResult(call_id="t2", data={"verification": "passed"}))
    agent, _ = runner(
        tmp_path, [*(ModelTurn(tool_calls=[c]) for c in calls), ModelTurn(content="Done")]
    )
    # ScriptedFake + scripted tool results isolate report state from subprocess behavior.
    execute = AsyncMock(side_effect=results)
    monkeypatch.setattr(agent.executor, "execute", execute)
    report = await agent.run("edit, verify, and edit the same file again")
    assert report.status == "completed"
    assert report.verification == expected
    assert report.changed_files == ["a.py"]
    assert execute.await_count == len(calls)
    validate_messages(agent.messages)
