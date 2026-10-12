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

    def wire(self) -> JsonObject:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_type.model_json_schema(),
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

    async def execute(self, call: ToolCall) -> ToolResult:
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
            args = tool.args_type.model_validate_json(call.arguments, strict=True).model_dump()
            if decision == "allow":
                async with asyncio.timeout(self.limits.tool_timeout):
                    output = await tool.handler(args)
            else:
                if not tool.prepare:
                    raise RepoPilotError("UNSAFE_TOOL", "Side effects require a prepared action.")
                async with asyncio.timeout(self.limits.tool_timeout):
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
                async with asyncio.timeout(action.timeout):
                    output = await action.commit()
            result = ToolResult(
                call_id=call.id,
                data=output.data,
                evidence=output.evidence,
                truncated=output.truncated,
            )
            return ToolResult.model_validate(clean_data(result.model_dump()))
        except ValidationError:
            return ToolResult(
                call_id=call.id,
                status="error",
                error_code="INVALID_ARGUMENTS",
                message="Arguments must satisfy the tool JSON schema.",
            )
        except RepoPilotError as exc:
            return ToolResult(
                call_id=call.id, status="error", error_code=exc.code, message=clean(str(exc))
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
        selected = lines[start - 1 : end]
        rendered = "\n".join(f"{start + i}: {line[:1000]}" for i, line in enumerate(selected))
        truncated = end < min(args["end_line"], len(lines)) or len(rendered.encode()) > 12000
        # Only grant a hash observation when the requested text has been returned.
        self.observed[snap.path] = snap.sha256
        ref = self._ref(snap.path, start, end, snap.sha256)
        return ToolOutput(
            {
                "path": snap.path,
                "sha256": snap.sha256,
                "total_lines": len(lines),
                "text": rendered.encode()[:12000].decode("utf-8", errors="ignore"),
            },
            [ref],
            truncated,
        )

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
        selected = self.workspace.resolve(args["path"], allow_root=True)
        candidates = (
            self.workspace.list_files(args["path"], limit=201)
            if selected.is_dir()
            else [selected.relative_to(self.workspace.root).as_posix()]
        )
        if len(candidates) > 200:
            raise RepoPilotError("DIFF_TOO_BROAD", "Specify a smaller directory or file.")
        diffs: list[str] = []
        for relative in candidates:
            self.workspace.snapshot(relative)
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    git,
                    "-c",
                    "core.fsmonitor=false",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--",
                    relative,
                ],
                cwd=self.workspace.root,
                capture_output=True,
                timeout=5,
                check=False,
            )
            if result.returncode:
                raise RepoPilotError("GIT_DIFF_FAILED", "Git diff failed; check repository setup.")
            diffs.append(result.stdout.decode("utf-8", errors="replace"))
            if sum(len(part.encode()) for part in diffs) >= 16384:
                break
        raw = "".join(diffs).encode()
        return ToolOutput(
            {
                "diff": raw[:16384].decode("utf-8", errors="ignore"),
                "scope": "Git working tree; may include pre-existing user changes.",
            },
            truncated=len(raw) > 16384,
        )

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
