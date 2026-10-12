"""Composition root for CLI and integration tests."""

import re
from pathlib import Path

from filelock import FileLock, Timeout

from repopilot.agent import AgentRunner, EventSink
from repopilot.config import Settings
from repopilot.editor import Editor
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, RunReport
from repopilot.providers import ModelProvider
from repopilot.safety import ApprovalHandler, PolicyEngine
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
    try:
        store = SessionStore(home)
        session_id = store.create_session(workspace.root, task)
        run_id = store.start_run(session_id, mode, settings.model_dump())
        registry = ToolRegistry()
        readers = ReadTools(workspace)
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

        runner = AgentRunner(
            provider,
            ToolExecutor(
                registry,
                settings.limits,
                policy=PolicyEngine("edit" if mode == "edit" else "read"),
                approval=approval,
                on_event=record,
            ),
            settings.limits,
            record,
        )
        report = await runner.run(task, session_id, run_id=run_id)
        refs = {e.id: e for e in report.evidence}
        for identifier in set(re.findall(r"\[(E\d+)\]", report.answer)):
            if identifier not in refs:
                report.warnings.append(f"Unknown evidence citation: {identifier}")
                continue
            ref = refs[identifier]
            try:
                if workspace.snapshot(ref.path).sha256 != ref.sha256:
                    report.warnings.append(f"Stale evidence citation: {identifier}")
            except RepoPilotError:
                report.warnings.append(f"Unavailable evidence citation: {identifier}")
        store.record(run_id, "run_finished", report.model_dump())
        return report
    finally:
        if store:
            store.close()
        lock.release()
