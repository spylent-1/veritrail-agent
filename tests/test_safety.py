import pytest

from repopilot.config import Limits, StrictModel
from repopilot.models import ToolCall
from repopilot.safety import PolicyEngine, PreparedAction
from repopilot.tools import ToolDefinition, ToolExecutor, ToolOutput, ToolRegistry


class Approval:
    def __init__(self, granted=True, mutate=False):
        self.granted = granted
        self.mutate = mutate

    async def confirm(self, action):
        if self.mutate:
            action.intent["content"] = "changed"
        return self.granted


def setup(approval=None, journal=None, mode="edit"):
    effects = []

    async def commit():
        effects.append("executed")
        return ToolOutput({"changed": True})

    async def prepare(args):
        return PreparedAction("write", "exact preview", {"content": "original"}, commit)

    async def forbidden(args):
        raise AssertionError("direct handler bypass")

    registry = ToolRegistry()
    registry.register(ToolDefinition("change", "fixture", StrictModel, forbidden, "write", prepare))
    executor = ToolExecutor(
        registry, Limits(), policy=PolicyEngine(mode), approval=approval, on_event=journal
    )
    return executor, effects


@pytest.mark.parametrize(
    "mode,approval", [("read", Approval()), ("edit", Approval(False)), ("edit", None)]
)
async def test_denied_actions_have_no_effect(mode, approval):
    executor, effects = setup(approval, mode=mode)
    result = await executor.execute(ToolCall(id="x", name="change", arguments="{}"))
    assert result.status == "denied" and effects == []


async def test_approved_intent_recorded_before_commit():
    events = []
    executor, effects = setup(Approval(), lambda kind, data: events.append((kind, list(effects))))
    assert (await executor.execute(ToolCall(id="x", name="change", arguments="{}"))).status == "ok"
    assert events[-1] == ("tool_intent", []) and effects == ["executed"]


async def test_mutated_approval_rejected():
    executor, effects = setup(Approval(mutate=True))
    result = await executor.execute(ToolCall(id="x", name="change", arguments="{}"))
    assert result.error_code == "APPROVAL_CHANGED" and effects == []


async def test_journal_failure_does_not_commit():
    def journal(kind, data):
        if kind == "tool_intent":
            raise OSError("disk failure")

    executor, effects = setup(Approval(), journal)
    result = await executor.execute(ToolCall(id="x", name="change", arguments="{}"))
    assert result.error_code == "IO_ERROR" and effects == []
