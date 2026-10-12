from filelock import FileLock

from repopilot.application import run_task
from repopilot.config import Settings
from repopilot.errors import RepoPilotError
from repopilot.models import ModelTurn, ToolCall, validate_messages
from repopilot.providers import ScriptedFake
from repopilot.store import SessionStore
from repopilot.workspace import digest


async def test_saved_run_round_trips_and_trace_is_derived(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("value = 1\n")
    home = tmp_path / "state"
    fake = ScriptedFake(
        [
            ModelTurn(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments='{"path":"a.py"}')]
            ),
            ModelTurn(content="value is 1 [E1]"),
        ]
    )
    report = await run_task("read", root, home, Settings(), fake)
    store = SessionStore(home)
    try:
        assert store.report(report.run_id) == report
        validate_messages(store.messages(report.session_id))
        assert any(e["kind"] == "tool_finished" for e in store.events(report.run_id))
        assert store.session(report.session_id)["workspace"] == str(root.resolve())
        assert not list(root.glob("*.sqlite*"))
    finally:
        store.close()


async def test_workspace_lock_rejects_second_run(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    home = tmp_path / "state"
    (home / "locks").mkdir(parents=True)
    key = digest(str(root.resolve()).casefold().encode())
    with FileLock(home / "locks" / f"{key}.lock"):
        try:
            await run_task("read", root, home, Settings(), ScriptedFake([]))
        except RepoPilotError as exc:
            assert exc.code == "WORKSPACE_BUSY"
        else:
            raise AssertionError("second run acquired workspace")


async def test_invented_evidence_is_reported(tmp_path):
    report = await run_task(
        "read",
        tmp_path,
        tmp_path / "state",
        Settings(),
        ScriptedFake([ModelTurn(content="invented [E999]")]),
    )
    assert report.warnings == ["Unknown evidence citation: E999"]
