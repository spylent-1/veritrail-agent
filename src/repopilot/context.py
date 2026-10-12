"""Deterministic outgoing views; the saved transcript is never rewritten."""

import json
from dataclasses import dataclass

from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, Message, ToolResult, validate_messages


@dataclass(frozen=True)
class ModelInput:
    messages: list[Message]
    tools: list[JsonObject]
    bytes: int
    original_bytes: int
    omitted_turns: int


class ContextBuilder:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity

    def build(self, history: list[Message], tools: list[JsonObject]) -> ModelInput:
        validate_messages(history)
        messages = [message.model_copy(deep=True) for message in history]

        def size() -> int:
            return len(
                json.dumps(
                    {"messages": [m.wire() for m in messages], "tools": tools}, ensure_ascii=False
                ).encode()
            )

        original = size()
        latest_group = max(
            (i for i, message in enumerate(messages) if message.tool_calls), default=0
        )
        for i, message in enumerate(messages):
            if size() <= self.capacity:
                break
            if message.role == "tool" and message.content and i < latest_group:
                result = ToolResult.model_validate_json(message.content)
                omitted = []
                for key in ("text", "output", "diff", "matches", "files"):
                    if key in result.data:
                        result.data.pop(key)
                        omitted.append(key)
                if omitted:
                    result.data["context_omitted_fields"] = omitted
                    result.data["context_note"] = (
                        "Payload omitted from context; re-read current files."
                    )
                    result.truncated = True
                    message.content = result.model_dump_json()
        omitted_turns = 0
        while size() > self.capacity:
            users = [i for i, message in enumerate(messages) if message.role == "user"]
            if len(users) <= 1:
                raise RepoPilotError(
                    "CONTEXT_BUDGET", "Necessary current context cannot fit capacity."
                )
            del messages[users[0] : users[1]]
            omitted_turns += 1
        validate_messages(messages)
        return ModelInput(messages, tools, size(), original, omitted_turns)
