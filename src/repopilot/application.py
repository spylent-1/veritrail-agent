"""Composition root for CLI and integration tests."""

import re
from pathlib import Path

from filelock import FileLock, Timeout

from repopilot.agent import AgentRunner, EventSink
from repopilot.config import Settings
from repopilot.editor import Editor
from repopilot.errors import RepoPilotError
from repopilot.mcp_adapter import McpAdapter
from repopilot.mcp_connection import McpConnection
from repopilot.models import JsonObject, RunReport
from repopilot.providers import ModelProvider
from repopilot.recovery import history_evidence, prepare_rollback, reconcile_session
from repopilot.safety import ApprovalHandler, PolicyEngine, PreparedAction
from repopilot.store import SessionStore
from repopilot.test_runner import TestRunner
from repopilot.tools import ReadTools, ToolExecutor, ToolRegistry
from repopilot.workspace import Workspace, digest


async def run_task(
    task: str,
    root: Path,
    home: Path,
    settings: Settings,
    provider: ModelProvider,
    on_event: EventSink | None = None,
    *,
    mode: str = "read",
    approval: ApprovalHandler | None = None,
    session_id: str | None = None,
    mcp_names: list[str] | None = None,
) -> RunReport:
    if mode not in ("read", "edit"):
        raise RepoPilotError("INVALID_MODE", "Choose read or edit mode.")
    workspace = Workspace(root, excluded_roots=(home,))
    locks = home / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = FileLock(locks / (digest(str(workspace.root).casefold().encode()) + ".lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise RepoPilotError(
            "WORKSPACE_BUSY", "Another RepoPilot run holds this workspace."
        ) from None
    store: SessionStore | None = None
    connections: list[McpConnection] = []
    startup_warnings: list[str] = []
    try:
        store = SessionStore(home)
        history = []
        if session_id:
            if store.session(session_id)["workspace"] != str(workspace.root):
                raise RepoPilotError("WORKSPACE_CHANGED", "Session belongs to another workspace.")
            history = reconcile_session(store, session_id, workspace)
        else:
            session_id = store.create_session(workspace.root, task)
        run_id = store.start_run(session_id, mode, settings.model_dump())
        registry = ToolRegistry()
        readers = ReadTools(workspace)
        readers.evidence.extend(history_evidence(history))
        readers.register(registry)
        if mode == "edit":
            Editor(workspace, readers, home / "runs" / run_id).register(registry)
            TestRunner(
                workspace, settings.tests, home / "runs" / run_id, settings.limits.output_bytes
            ).register(registry)

        def record(kind: str, data: JsonObject) -> None:
            assert store is not None
            if kind != "run_finished":
                store.record(run_id, kind, data)
            if on_event:
                on_event(kind, data)

        for name in dict.fromkeys(mcp_names or []):
            from repopilot.config import StrictModel
            from repopilot.models import ToolCall
            from repopilot.tools import ToolDefinition, ToolOutput

            if name not in settings.mcp:
                raise RepoPilotError(
                    "MCP_NOT_CONFIGURED", "MCP server is not in host configuration."
                )
            connection = McpConnection(settings.mcp[name], workspace.root)
            connections.append(connection)
            adapter = McpAdapter(name, connection, registry, settings.limits.output_bytes)
            action = adapter.start_action()
            startup_registry = ToolRegistry()

            async def prepare_start(
                args: JsonObject, prepared: PreparedAction = action
            ) -> PreparedAction:
                return prepared

            async def no_start_bypass(args: JsonObject) -> ToolOutput:
                raise RepoPilotError("UNSAFE_TOOL", "MCP startup requires approval.")

            startup_registry.register(
                ToolDefinition(
                    "start_mcp",
                    "Host MCP startup",
                    StrictModel,
                    no_start_bypass,
                    "external",
                    prepare_start,
                )
            )
            identifier = f"host_mcp_{name}"
            store.host_tool(run_id, "start_mcp", identifier)
            record("tool_started", {"call_id": identifier, "name": "start_mcp"})
            startup_result = await ToolExecutor(
                startup_registry,
                settings.limits,
                policy=PolicyEngine(allow_external=True),
                approval=approval,
                on_event=record,
            ).execute(ToolCall(id=identifier, name="start_mcp", arguments="{}"))
            record("tool_finished", startup_result.model_dump())
            if startup_result.status != "ok":
                startup_warnings.append(f"MCP {name}: {startup_result.error_code}")

        runner = AgentRunner(
            provider,
            ToolExecutor(
                registry,
                settings.limits,
                policy=PolicyEngine(
                    "edit" if mode == "edit" else "read", allow_external=bool(mcp_names)
                ),
                approval=approval,
                on_event=record,
            ),
            settings.limits,
            record,
        )
        report = await runner.run(task, session_id, run_id=run_id, history=history)
        report.warnings.extend(startup_warnings)
        refs = {e.id: e for e in readers.evidence}
        for identifier in set(re.findall(r"\[(E\d+)\]", report.answer)):
            if identifier not in refs:
                report.warnings.append(f"Unknown evidence citation: {identifier}")
                continue
            ref = refs[identifier]
            if not any(e.id == identifier for e in report.evidence):
                report.evidence.append(ref)
            try:
                if workspace.snapshot(ref.path).sha256 != ref.sha256:
                    report.warnings.append(f"Stale evidence citation: {identifier}")
            except RepoPilotError:
                report.warnings.append(f"Unavailable evidence citation: {identifier}")
        store.record(run_id, "run_finished", report.model_dump())
        return report
    finally:
        for connection in reversed(connections):
            await connection.close()
        if store:
            store.close()
        lock.release()


async def rollback_run(
    run_id: str, home: Path, approval: ApprovalHandler | None = None
) -> RunReport:
    from repopilot.config import Limits, StrictModel
    from repopilot.models import ToolCall
    from repopilot.tools import ToolDefinition, ToolOutput

    store = SessionStore(home)
    lock: FileLock | None = None
    try:
        original = store.run(run_id)
        workspace = Workspace(
            Path(store.session(original["session_id"])["workspace"]), excluded_roots=(home,)
        )
        locks = home / "locks"
        locks.mkdir(exist_ok=True)
        lock = FileLock(locks / (digest(str(workspace.root).casefold().encode()) + ".lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise RepoPilotError("WORKSPACE_BUSY", "Workspace is running another task.") from None
        action = prepare_rollback(store, run_id, workspace)
        audit_id = store.start_run(original["session_id"], "edit", {"origin": "host_rollback"})
        store.host_tool(audit_id, "rollback", "rollback")
        registry = ToolRegistry()

        async def prepare(args: JsonObject) -> PreparedAction:
            return action

        async def no_bypass(args: JsonObject) -> ToolOutput:
            raise RepoPilotError("UNSAFE_TOOL", "Rollback requires approval.")

        registry.register(
            ToolDefinition("rollback", "Host rollback", StrictModel, no_bypass, "write", prepare)
        )
        executor = ToolExecutor(
            registry,
            Limits(),
            policy=PolicyEngine("edit"),
            approval=approval,
            on_event=lambda kind, data: store.record(audit_id, kind, data),
        )
        store.record(audit_id, "tool_started", {"call_id": "rollback", "name": "rollback"})
        result = await executor.execute(ToolCall(id="rollback", name="rollback", arguments="{}"))
        store.record(audit_id, "tool_finished", result.model_dump())
        report = RunReport(
            run_id=audit_id,
            session_id=original["session_id"],
            status="completed" if result.status == "ok" else "failed",
            stop_reason="HOST_ROLLBACK" if result.status == "ok" else result.error_code or "FAILED",
            tool_calls=1,
            changed_files=result.data.get("changed_files", []),
            warnings=[result.message] if result.message else [],
        )
        store.record(audit_id, "run_finished", report.model_dump())
        return report
    finally:
        store.close()
        if lock and lock.is_locked:
            lock.release()
