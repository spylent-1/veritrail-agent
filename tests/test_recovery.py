import json

import pytest

from repopilot.application import rollback_run, run_task
from repopilot.config import Settings
from repopilot.editor import Editor
from repopilot.errors import RepoPilotError
from repopilot.models import Message, ModelTurn, ToolCall, validate_messages
from repopilot.providers import ScriptedFake
from repopilot.recovery import reconcile_session
from repopilot.store import SessionStore
from repopilot.tools import ReadTools
from repopilot.workspace import Workspace, digest


class Approve:
    async def confirm(self, action):
        return True


async def interrupted(tmp_path, applied=False):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_bytes(b"x = 1\n")
    home = tmp_path / "state"
    store = SessionStore(home)
    sid = store.create_session(root, "fixture")
    rid = store.start_run(sid, "edit", {})
    call = ToolCall(
        id="c1",
        name="edit_file",
        arguments=json.dumps(
            {
                "path": "a.py",
                "expected_hash": digest(b"x = 1\n"),
                "edits": [{"old_text": "x = 1", "new_text": "x = 2"}],
            }
        ),
    )
    for message in [
        Message(role="system", content="fixture"),
        Message(role="user", content="edit"),
        Message(role="assistant", tool_calls=[call]),
    ]:
        store.record(rid, "message", message.model_dump())
    readers = ReadTools(Workspace(root))
    readers.observed["a.py"] = digest(b"x = 1\n")
    action = await Editor(readers.workspace, readers, home / "runs" / rid).prepare_edit(
        json.loads(call.arguments)
    )
    store.record(rid, "tool_started", {"call_id": "c1", "name": "edit_file"})
    store.record(rid, "tool_intent", {"call_id": "c1", "intent": action.intent})
    if applied:
        await action.commit()
    store.close()
    return root, home, sid, rid


@pytest.mark.parametrize("applied,status", [(False, "skipped"), (True, "ok")])
async def test_recovery_does_not_replay_write(tmp_path, applied, status):
    root, home, sid, rid = await interrupted(tmp_path, applied)
    original = (root / "a.py").read_bytes()
    store = SessionStore(home)
    try:
        messages = reconcile_session(store, sid, Workspace(root))
        validate_messages(messages)
        assert json.loads(messages[-1].content)["status"] == status
        assert (root / "a.py").read_bytes() == original
        assert store.report(rid).status == "interrupted"
    finally:
        store.close()


async def test_conflicting_recovery_remains_unknown(tmp_path):
    root, home, sid, rid = await interrupted(tmp_path, True)
    (root / "a.py").write_text("user changed")
    store = SessionStore(home)
    try:
        assert (
            json.loads(reconcile_session(store, sid, Workspace(root))[-1].content)["status"]
            == "unknown"
        )
    finally:
        store.close()


async def test_rollback_restores_dirty_original_and_refuses_new_change(tmp_path):
    root, home, sid, rid = await interrupted(tmp_path, True)
    store = SessionStore(home)
    reconcile_session(store, sid, Workspace(root))
    store.close()
    (root / "a.py").write_text("user changed")
    with pytest.raises(RepoPilotError, match="rollback refused"):
        await rollback_run(rid, home, Approve())
    (root / "a.py").write_bytes(b"x = 2\n")
    report = await rollback_run(rid, home, Approve())
    assert report.status == "completed" and (root / "a.py").read_bytes() == b"x = 1\n"


async def test_resume_has_no_reused_read_or_approval(tmp_path):
    root, home, sid, rid = await interrupted(tmp_path, True)
    fake = ScriptedFake(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="new",
                        name="edit_file",
                        arguments=json.dumps(
                            {
                                "path": "a.py",
                                "expected_hash": digest(b"x = 2\n"),
                                "edits": [{"old_text": "x = 2", "new_text": "x = 3"}],
                            }
                        ),
                    )
                ]
            ),
            ModelTurn(content="Need to re-read."),
        ]
    )
    report = await run_task(
        "continue", root, home, Settings(), fake, mode="edit", session_id=sid, approval=Approve()
    )
    assert report.status == "completed" and (root / "a.py").read_bytes() == b"x = 2\n"
    assert '"error_code":"READ_REQUIRED"' in fake.requests[-1][-1].content


async def test_interrupted_test_is_unknown_not_replayed(tmp_path):
    store = SessionStore(tmp_path / "state")
    sid = store.create_session(tmp_path, "test")
    rid = store.start_run(sid, "edit", {})
    call = ToolCall(id="t", name="run_tests", arguments='{"profile":"unit"}')
    store.record(rid, "message", Message(role="assistant", tool_calls=[call]).model_dump())
    store.record(rid, "tool_started", {"call_id": "t", "name": "run_tests"})
    store.record(rid, "tool_intent", {"call_id": "t", "intent": {"kind": "test_run"}})
    try:
        history = reconcile_session(store, sid, Workspace(tmp_path))
        assert json.loads(history[-1].content)["status"] == "unknown"
        assert store.report(rid).verification == "unknown"
    finally:
        store.close()


async def test_rollback_reverses_multiple_edits_and_removes_only_our_created_file(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    original = b"# user edit\nx = 1\n"
    (root / "a.py").write_bytes(original)
    (root / "unrelated.txt").write_text("keep")

    def call(identifier, name, args):
        return ModelTurn(
            tool_calls=[ToolCall(id=identifier, name=name, arguments=json.dumps(args))]
        )

    fake = ScriptedFake(
        [
            call("r1", "read_file", {"path": "a.py"}),
            call(
                "e1",
                "edit_file",
                {
                    "path": "a.py",
                    "expected_hash": digest(original),
                    "edits": [{"old_text": "x = 1", "new_text": "x = 2"}],
                },
            ),
            call("r2", "read_file", {"path": "a.py"}),
            call(
                "e2",
                "edit_file",
                {
                    "path": "a.py",
                    "expected_hash": digest(original.replace(b"1", b"2")),
                    "edits": [{"old_text": "x = 2", "new_text": "x = 3"}],
                },
            ),
            call("new", "create_file", {"path": "new.py", "content": "hello = 1\n"}),
            ModelTurn(content="done"),
        ]
    )
    home = tmp_path / "state"
    report = await run_task("edit", root, home, Settings(), fake, mode="edit", approval=Approve())
    assert report.changed_files == ["a.py", "new.py"]
    rolled_back = await rollback_run(report.run_id, home, Approve())
    assert rolled_back.status == "completed"
    assert (root / "a.py").read_bytes() == original
    assert not (root / "new.py").exists() and (root / "unrelated.txt").read_text() == "keep"
