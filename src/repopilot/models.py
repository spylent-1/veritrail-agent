"""Small wire-independent types used by the loop, tools and persistence."""

from typing import Any, Literal

from pydantic import Field

from repopilot.config import StrictModel
from repopilot.errors import RepoPilotError

JsonObject = dict[str, Any]


class ToolCall(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: str  # Raw JSON: invalid model arguments are handled by ToolExecutor.

    def wire(self) -> JsonObject:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }


class Message(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None

    def wire(self) -> JsonObject:
        result: JsonObject = {"role": self.role, "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = [call.wire() for call in self.tool_calls]
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        return result


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ModelTurn(StrictModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: Usage | None = None
    usage_complete: bool = True


class EvidenceRef(StrictModel):
    id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    sha256: str


class ToolResult(StrictModel):
    call_id: str
    status: Literal["ok", "error", "denied", "skipped", "unknown"] = "ok"
    data: JsonObject = Field(default_factory=dict)
    error_code: str | None = None
    message: str = ""
    truncated: bool = False
    evidence: list[EvidenceRef] = Field(default_factory=list)

    def message_for_model(self) -> Message:
        return Message(role="tool", tool_call_id=self.call_id, content=self.model_dump_json())


class RunReport(StrictModel):
    run_id: str
    session_id: str
    status: Literal["completed", "failed", "cancelled", "interrupted", "budget_exhausted"]
    stop_reason: str
    answer: str = ""
    verification: Literal["passed", "failed", "not_run", "unknown"] = "not_run"
    model_turns: int = 0
    tool_calls: int = 0
    usage: Usage = Field(default_factory=Usage)
    usage_complete: bool = True
    evidence: list[EvidenceRef] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_messages(messages: list[Message]) -> None:
    """Reject orphaned results, duplicate call IDs and incomplete tool groups."""
    pending: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise RepoPilotError("PROTOCOL_ERROR", "Orphaned or duplicate tool result.")
            pending.remove(message.tool_call_id)
        else:
            if pending:
                raise RepoPilotError("PROTOCOL_ERROR", "Incomplete tool result group.")
            for call in message.tool_calls:
                if message.role != "assistant" or call.id in seen:
                    raise RepoPilotError("PROTOCOL_ERROR", "Invalid or duplicate tool call ID.")
                pending.add(call.id)
                seen.add(call.id)
    if pending:
        raise RepoPilotError("PROTOCOL_ERROR", "Missing tool results.")
