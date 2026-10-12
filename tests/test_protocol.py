import pytest

from repopilot.config import Limits
from repopilot.errors import RepoPilotError
from repopilot.models import Message, ModelTurn, ToolCall, ToolResult, validate_messages
from repopilot.providers import ScriptedFake


def call(identifier="c1"):
    return ToolCall(id=identifier, name="read_file", arguments='{"path":"a.py"}')


def test_wire_and_tool_pairing():
    messages = [
        Message(role="user", content="read"),
        Message(role="assistant", tool_calls=[call(), call("c2")]),
        ToolResult(call_id="c1").message_for_model(),
        ToolResult(call_id="c2", status="denied").message_for_model(),
    ]
    validate_messages(messages)
    assert messages[1].wire()["tool_calls"][0]["function"]["name"] == "read_file"


@pytest.mark.parametrize(
    "messages",
    [
        [Message(role="assistant", tool_calls=[call()])],
        [ToolResult(call_id="c1").message_for_model()],
        [Message(role="assistant", tool_calls=[call(), call()])],
        [Message(role="assistant", tool_calls=[call()]), Message(role="user", content="next")],
    ],
)
def test_invalid_histories_rejected(messages):
    with pytest.raises(RepoPilotError):
        validate_messages(messages)


async def test_scripted_model_preserves_requests_and_errors():
    fake = ScriptedFake([ModelTurn(content="ok"), RepoPilotError("EXPECTED", "fixture")])
    messages = [Message(role="user", content="hello")]
    assert (await fake.complete(messages, [], Limits())).content == "ok"
    messages[0].content = "changed"
    assert fake.requests[0][0].content == "hello"
    with pytest.raises(RepoPilotError, match="fixture"):
        await fake.complete(messages, [], Limits())
