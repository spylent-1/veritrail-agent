"""Provider protocol and deterministic model double; neither can execute tools."""

from collections import deque
from copy import deepcopy
from typing import Protocol

from repopilot.config import Limits
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, Message, ModelTurn, validate_messages


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[JsonObject],
        limits: Limits,
    ) -> ModelTurn: ...

    async def close(self) -> None: ...


class ScriptedFake:
    """A scripted protocol double, never advertised as real model evidence."""

    def __init__(self, turns: list[ModelTurn | Exception]) -> None:
        self.turns = deque(turns)
        self.requests: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[JsonObject],
        limits: Limits,
    ) -> ModelTurn:
        validate_messages(messages)
        self.requests.append(deepcopy(messages))
        if not self.turns:
            raise RepoPilotError("SCRIPT_EXHAUSTED", "Scripted model has no remaining turns.")
        turn = self.turns.popleft()
        if isinstance(turn, Exception):
            raise turn
        return turn.model_copy(deep=True)

    async def close(self) -> None:
        pass
