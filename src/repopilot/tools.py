"""Registry contains definitions; Executor is the only runtime dispatch boundary."""

import asyncio
import json
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from repopilot.config import Limits, StrictModel
from repopilot.errors import RepoPilotError
from repopilot.git_diff import read_git_diff
from repopilot.models import EvidenceRef, JsonObject, ToolCall, ToolResult
from repopilot.safety import ApprovalHandler, DenyApproval, PolicyEngine, PreparedAction
from repopilot.sanitize import clean, clean_data
from repopilot.workspace import Workspace


@dataclass
class ToolOutput:
    data: JsonObject
    evidence: list[EvidenceRef] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ToolDefinition:
    name: str
    description: str
    args_type: type[BaseModel]
    handler: Callable[[JsonObject], Awaitable[ToolOutput]]
    risk: Literal["read", "write", "execute", "external"] = "read"
    prepare: Callable[[JsonObject], Awaitable[PreparedAction]] | None = None
    custom_schema: JsonObject | None = None
    validate_args: Callable[[JsonObject], JsonObject] | None = None

    def wire(self) -> JsonObject:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.custom_schema or self.args_type.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise RepoPilotError("DUPLICATE_TOOL", "Tool names cannot override existing tools.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise RepoPilotError("UNKNOWN_TOOL", "Requested tool is not registered.")
        return self._tools[name]

    def list(self) -> list[JsonObject]:
        return [tool.wire() for tool in self._tools.values()]


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        limits: Limits,
        *,
        policy: PolicyEngine | None = None,
        approval: ApprovalHandler | None = None,
        on_event: Callable[[str, JsonObject], None] | None = None,
    ) -> None:
        self.registry = registry
        self.limits = limits
        self.policy = policy or PolicyEngine()
        self.approval = approval or DenyApproval()
        self.on_event = on_event or (lambda kind, data: None)
        self.approval_seconds = 0.0

    async def execute(self, call: ToolCall, active_budget: float | None = None) -> ToolResult:
        started = time.monotonic()
        approval_before = self.approval_seconds

        def deadline(default: float) -> float:
            if active_budget is None:
                return default
            remaining = active_budget - (
                time.monotonic() - started - (self.approval_seconds - approval_before)
            )
            if remaining <= 0:
                raise RepoPilotError("TIME_BUDGET", "Active task time exhausted.")
            return min(default, remaining)

        try:
            tool = self.registry.get(call.name)
            decision = self.policy.evaluate(tool.risk)
            if decision == "deny":
                return ToolResult(
                    call_id=call.id,
                    status="denied",
                    error_code="POLICY_DENIED",
                    message="Host policy forbids this operation.",
                )
            if len(call.arguments.encode()) > self.limits.context_bytes:
                raise RepoPilotError("INVALID_ARGUMENTS", "Tool arguments exceed the limit.")
            if tool.validate_args:
                raw = json.loads(call.arguments)
                if not isinstance(raw, dict):
                    raise RepoPilotError(
                        "INVALID_ARGUMENTS", "MCP arguments must be a JSON object."
                    )
                args = tool.validate_args(raw)
            else:
                args = tool.args_type.model_validate_json(call.arguments, strict=True).model_dump()
            if decision == "allow":
                async with asyncio.timeout(deadline(self.limits.tool_timeout)):
                    output = await tool.handler(args)
            else:
                if not tool.prepare:
                    raise RepoPilotError("UNSAFE_TOOL", "Side effects require a prepared action.")
                async with asyncio.timeout(deadline(self.limits.tool_timeout)):
                    action = await tool.prepare(args)
                if action.risk != tool.risk:
                    raise RepoPilotError("UNSAFE_TOOL", "Prepared risk does not match tool policy.")
                fingerprint = action.fingerprint()
                started = time.monotonic()
                try:
                    granted = await self.approval.confirm(action)
                finally:
                    self.approval_seconds += time.monotonic() - started
                self.on_event(
                    "approval", {"call_id": call.id, "granted": granted, "fingerprint": fingerprint}
                )
                if not granted:
                    return ToolResult(
                        call_id=call.id,
                        status="denied",
                        error_code="USER_DENIED",
                        message="User declined the concrete operation.",
                    )
                if action.fingerprint() != fingerprint:
                    raise RepoPilotError("APPROVAL_CHANGED", "Operation changed after approval.")
                # A failing journal write prevents commit. Nothing is executed before this event.
                self.on_event(
                    "tool_intent",
                    {"call_id": call.id, "fingerprint": fingerprint, "intent": action.intent},
                )
                async with asyncio.timeout(deadline(action.timeout)):
                    output = await action.commit()
            result = ToolResult(
                call_id=call.id,
                data=output.data,
                evidence=output.evidence,
                truncated=output.truncated,
            )
            return ToolResult.model_validate(clean_data(result.model_dump()))
        except (ValidationError, json.JSONDecodeError, RecursionError, UnicodeError):
            return ToolResult(
                call_id=call.id,
                status="error",
                error_code="INVALID_ARGUMENTS",
                message="Arguments must satisfy the tool JSON schema.",
            )
        except RepoPilotError as exc:
            return ToolResult(
                call_id=call.id,
                status="unknown" if exc.code == "MCP_OUTCOME_UNKNOWN" else "error",
                error_code=exc.code,
                message=clean(str(exc)),
            )
        except (TimeoutError, subprocess.TimeoutExpired):
            return ToolResult(
                call_id=call.id,
                status="error",
                error_code="TIMEOUT",
                message="Tool deadline exceeded.",
            )
        except OSError:
            return ToolResult(
                call_id=call.id,
                status="error",
                error_code="IO_ERROR",
                message="The file or process operation failed.",
            )


class ListArgs(StrictModel):
    path: str = "."
    pattern: str = Field(default="*", max_length=200)
    limit: int = Field(default=100, ge=1, le=200)


class ReadArgs(StrictModel):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=200, ge=1)


class GrepArgs(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    path: str = "."
    pattern: str = Field(default="*", max_length=200)
    limit: int = Field(default=30, ge=1, le=100)


class DiffArgs(StrictModel):
    path: str = "."


class ReadTools:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.observed: dict[str, str] = {}
        self.evidence: list[EvidenceRef] = []

    def _ref(self, path: str, start: int, end: int, sha256: str) -> EvidenceRef:
        ref = EvidenceRef(
            id=f"E{len(self.evidence) + 1}",
            path=path,
            start_line=start,
            end_line=end,
            sha256=sha256,
        )
        self.evidence.append(ref)
        return ref

    async def list_files(self, args: JsonObject) -> ToolOutput:
        files = await asyncio.to_thread(
            self.workspace.list_files, args["path"], args["pattern"], args["limit"] + 1
        )
        return ToolOutput({"files": files[: args["limit"]]}, truncated=len(files) > args["limit"])

    async def read_file(self, args: JsonObject) -> ToolOutput:
        snap = await asyncio.to_thread(self.workspace.snapshot, args["path"])
        lines = snap.text.splitlines()
        start = args["start_line"]
        end = min(args["end_line"], start + 199, max(1, len(lines)))
        if start > max(1, len(lines)) or end < start:
            raise RepoPilotError("INVALID_RANGE", "Requested line range is outside the file.")
        requested_end = min(args["end_line"], len(lines))
        rendered: list[str] = []
        used = 0
        partial_line: int | None = None
        for number in range(start, end + 1):
            if not lines:
                break
            line = f"{number}: {lines[number - 1]}"
            size = len(line.encode("utf-8")) + bool(rendered)
            if used + size > 12000:
                if not rendered:
                    # A partial preview must never authorize a complete-line citation.
                    rendered.append(line.encode("utf-8")[:12000].decode("utf-8", errors="ignore"))
                    partial_line = number
                break
            rendered.append(line)
            used += size
        complete_count = len(rendered) if partial_line is None else 0
        returned_end = start + complete_count - 1
        truncated = bool(lines) and returned_end < requested_end
        data: JsonObject = {
            "path": snap.path,
            "sha256": snap.sha256,
            "total_lines": len(lines),
            "text": "\n".join(rendered),
        }
        if truncated:
            data["next_start_line"] = returned_end + 1
            data["notice"] = "Output truncated; request a smaller line range before editing."
        if partial_line is not None:
            data["partial_line"] = partial_line
            data["notice"] = "Source line exceeds the byte limit; partial preview has no evidence."
        if not truncated:
            self.observed[snap.path] = snap.sha256
        refs = [self._ref(snap.path, start, returned_end, snap.sha256)] if complete_count else []
        return ToolOutput(data, refs, truncated)

    def _grep(self, args: JsonObject) -> list[JsonObject]:
        deadline = time.monotonic() + 10
        if not self.workspace.rg:
            raise RepoPilotError("RG_MISSING", "Install ripgrep first.")
        candidates = self.workspace.list_files(args["path"], args["pattern"], 5001)
        if len(candidates) > 5000:
            raise RepoPilotError("SEARCH_TOO_BROAD", "Narrow the search to a subdirectory.")
        matches: list[JsonObject] = []
        # Check text eligibility before giving paths to rg. Never let rg choose its own paths.
        for relative in candidates:
            if time.monotonic() >= deadline:
                raise RepoPilotError("TIMEOUT", "Narrow the search to a subdirectory.")
            try:
                snap = self.workspace.snapshot(relative)
            except RepoPilotError:
                continue
            result = subprocess.run(
                [
                    self.workspace.rg,
                    "--no-config",
                    "--json",
                    "--fixed-strings",
                    "--max-count",
                    str(args["limit"]),
                    "--",
                    args["query"],
                    relative,
                ],
                cwd=self.workspace.root,
                capture_output=True,
                timeout=max(0.1, deadline - time.monotonic()),
                check=False,
            )
            if result.returncode not in (0, 1):
                raise RepoPilotError("SEARCH_FAILED", "ripgrep search failed.")
            for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                event = json.loads(line)
                if event.get("type") == "match":
                    data = event["data"]
                    matches.append(
                        {
                            "path": relative,
                            "line": data["line_number"],
                            "text": data["lines"].get("text", "")[:400],
                            "sha256": snap.sha256,
                        }
                    )
                    if len(matches) >= args["limit"]:
                        return matches
        return matches

    async def grep_code(self, args: JsonObject) -> ToolOutput:
        matches = await asyncio.to_thread(self._grep, args)
        refs = [self._ref(m["path"], m["line"], m["line"], m["sha256"]) for m in matches]
        return ToolOutput({"matches": matches}, refs, len(matches) >= args["limit"])

    async def git_diff(self, args: JsonObject) -> ToolOutput:
        git = shutil.which("git")
        if not git:
            raise RepoPilotError("GIT_MISSING", "Install Git first.")
        data, truncated = await asyncio.to_thread(read_git_diff, self.workspace, args["path"], git)
        return ToolOutput(data, truncated=truncated)

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                "git_diff",
                "Read Git changes for eligible files; these may include pre-existing user edits.",
                DiffArgs,
                self.git_diff,
            )
        )
        registry.register(
            ToolDefinition(
                "list_files", "List visible files in the workspace.", ListArgs, self.list_files
            )
        )
        registry.register(
            ToolDefinition(
                "read_file", "Read UTF-8 code with line numbers and hash.", ReadArgs, self.read_file
            )
        )
        registry.register(
            ToolDefinition(
                "grep_code", "Search a literal string in eligible code.", GrepArgs, self.grep_code
            )
        )
