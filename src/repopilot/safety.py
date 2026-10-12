"""Host-owned approval, never model-supplied permission."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from repopilot.models import JsonObject
from repopilot.workspace import digest

if TYPE_CHECKING:
    from repopilot.tools import ToolOutput

Risk = Literal["read", "write", "execute", "external"]


@dataclass(frozen=True)
class PreparedAction:
    risk: Risk
    preview: str
    intent: JsonObject
    commit: Callable[[], Awaitable["ToolOutput"]]
    timeout: float = 10

    def fingerprint(self) -> str:
        return digest(
            json.dumps(
                {"risk": self.risk, "preview": self.preview, "intent": self.intent}, sort_keys=True
            ).encode()
        )


class ApprovalHandler(Protocol):
    async def confirm(self, action: PreparedAction) -> bool: ...


class DenyApproval:
    async def confirm(self, action: PreparedAction) -> bool:
        return False


class PolicyEngine:
    def __init__(self, mode: Literal["read", "edit"] = "read", *, allow_external: bool = False):
        self.mode = mode
        self.allow_external = allow_external

    def evaluate(self, risk: Risk) -> Literal["allow", "ask", "deny"]:
        if risk == "read":
            return "allow"
        if risk == "external":
            return "ask" if self.allow_external else "deny"
        return "ask" if self.mode == "edit" else "deny"
