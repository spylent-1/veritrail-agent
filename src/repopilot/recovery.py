"""Reconcile interrupted operations without replay; guarded rollback of our own edits."""

import difflib
import json
from pathlib import Path

from repopilot.editor import atomic_write
from repopilot.errors import RepoPilotError
from repopilot.models import EvidenceRef, Message, RunReport, ToolResult, validate_messages
from repopilot.safety import PreparedAction
from repopilot.store import SessionStore
from repopilot.tools import ToolOutput
from repopilot.workspace import Workspace, digest, is_link


def reconcile_session(store: SessionStore, session_id: str, workspace: Workspace) -> list[Message]:
    store.session(session_id)
    for run in store.runs(session_id):
        rows = {row["call_id"]: row for row in store.tools(run["id"])}
        messages = store.messages(session_id)
        pending = {call.id for message in messages for call in message.tool_calls}
        pending -= {message.tool_call_id for message in messages if message.role == "tool"}
        for identifier in pending.intersection(rows):
            row = rows[identifier]
            if row["result"]:
                result = ToolResult.model_validate_json(row["result"])
            elif row["status"] == "proposed":
                result = ToolResult(
                    call_id=identifier,
                    status="skipped",
                    error_code="INTERRUPTED",
                    message="Operation was proposed but not started.",
                )
            else:
                result = ToolResult(
                    call_id=identifier,
                    status="unknown",
                    error_code="INTERRUPTED",
                    message="Interrupted operation; do not replay without inspection.",
                )
                intent = json.loads(row["intent"]) if row["intent"] else {}
                if intent.get("kind") == "file_write":
                    try:
                        path = workspace.resolve(intent["path"])
                        current = (
                            workspace.snapshot(intent["path"]).sha256 if path.exists() else None
                        )
                        if current == intent["after_hash"]:
                            result.status = "ok"
                            result.error_code = None
                            result.data = {
                                "changed_file": intent["path"],
                                "reconciled": True,
                                "after_hash": current,
                            }
                            result.message = "File equals the intended result; write is confirmed."
                        elif current == intent["before_hash"]:
                            result.status = "skipped"
                            result.message = "File equals pre-operation state; no replay performed."
                    except RepoPilotError:
                        pass
            store.record(run["id"], "message", result.message_for_model().model_dump())
            store.record(run["id"], "tool_finished", result.model_dump())
        if run["status"] == "running" or pending.intersection(rows):
            report = store.report(run["id"]) or RunReport(
                run_id=run["id"],
                session_id=session_id,
                status="interrupted",
                stop_reason="INTERRUPTED",
            )
            report.status = "interrupted"
            report.usage_complete = False
            report.stop_reason = "RECONCILED_INTERRUPTION"
            for row in store.tools(run["id"]):
                saved_result = (
                    ToolResult.model_validate_json(row["result"]) if row["result"] else None
                )
                if saved_result and saved_result.data.get("changed_file"):
                    path = saved_result.data["changed_file"]
                    if path not in report.changed_files:
                        report.changed_files.append(path)
                if row["name"] == "run_tests" and (
                    not saved_result or saved_result.status == "unknown"
                ):
                    report.verification = "unknown"
            report.warnings.append("Recovered interrupted run. Prior approvals are expired.")
            store.record(run["id"], "run_finished", report.model_dump())
    history = store.messages(session_id)
    validate_messages(history)
    return history


def history_evidence(history: list[Message]) -> list[EvidenceRef]:
    references: list[EvidenceRef] = []
    for message in history:
        if message.role == "tool" and message.content:
            references.extend(ToolResult.model_validate_json(message.content).evidence)
    return references


def prepare_rollback(store: SessionStore, run_id: str, workspace: Workspace) -> PreparedAction:
    store.run(run_id)
    steps: list[tuple[str, str, bytes | None]] = []
    simulated: dict[str, str | None] = {}
    previews: list[str] = []
    for row in reversed(store.tools(run_id)):
        intent = json.loads(row["intent"]) if row["intent"] else {}
        if intent.get("kind") != "file_write" or row["status"] not in ("ok", "running", "unknown"):
            continue
        relative = intent["path"]
        current = workspace.snapshot(relative)
        actual = simulated.get(relative, current.sha256)
        if actual != intent["after_hash"]:
            raise RepoPilotError(
                "ROLLBACK_CONFLICT", "Files changed after this run; rollback refused."
            )
        before: bytes | None = None
        if intent["before_artifact"]:
            artifact = Path(intent["before_artifact"])
            allowed = store.home / "runs" / run_id
            if not artifact.resolve().is_relative_to(allowed.resolve()) or is_link(artifact):
                raise RepoPilotError("ARTIFACT_INVALID", "Rollback snapshot path is invalid.")
            before = artifact.read_bytes()
            if digest(before) != intent["before_hash"]:
                raise RepoPilotError("ARTIFACT_INVALID", "Rollback snapshot hash is invalid.")
        steps.append((relative, intent["after_hash"], before))
        simulated[relative] = intent["before_hash"]
        previews.append(
            f"Restore {relative}: {intent['after_hash'][:12]} -> "
            f"{(intent['before_hash'] or 'absent')[:12]}"
        )
        previews.extend(
            difflib.unified_diff(
                current.text.splitlines(keepends=True),
                (before or b"").decode("utf-8-sig").splitlines(keepends=True),
                fromfile=relative,
                tofile=relative if before is not None else "/dev/null",
            )
        )
    if not steps:
        raise RepoPilotError("NOTHING_TO_ROLLBACK", "No confirmed file edits to rollback.")
    preview = "\n".join(previews)
    if len(preview.encode()) > 65536:
        raise RepoPilotError("ROLLBACK_TOO_BROAD", "Rollback preview exceeds review capacity.")

    async def commit() -> ToolOutput:
        changed = []
        for relative, expected, before in steps:
            if workspace.snapshot(relative).sha256 != expected:
                raise RepoPilotError(
                    "ROLLBACK_CONFLICT", "File changed while awaiting rollback approval."
                )
            path = workspace.resolve(relative)
            if before is None:
                path.unlink()
            else:
                atomic_write(path, before)
            changed.append(relative)
        return ToolOutput({"changed_files": sorted(set(changed)), "rolled_back_run": run_id})

    return PreparedAction(
        "write",
        preview,
        {
            "kind": "rollback",
            "run_id": run_id,
            "steps": [
                {
                    "path": p,
                    "expected_hash": h,
                    "restore_hash": digest(b) if b is not None else None,
                }
                for p, h, b in steps
            ],
        },
        commit,
    )
