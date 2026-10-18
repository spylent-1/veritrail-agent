import asyncio
import json
from copy import deepcopy

import pytest
from typer.testing import CliRunner

from repopilot.application import run_task
from repopilot.cli import app
from repopilot.config import Settings
from repopilot.errors import RepoPilotError
from repopilot.models import ModelTurn, ToolCall, validate_messages
from repopilot.providers import ScriptedFake
from repopilot.sanitize import clean
from repopilot.store import SessionStore


class ChatFake(ScriptedFake):
    def __init__(self, turns):
        super().__init__(turns)
        self.closed = False
        self.loops = []
        self.tool_names = []

    async def complete(self, messages, tools, limits):
        self.loops.append(asyncio.get_running_loop())
        self.tool_names.append([tool["function"]["name"] for tool in tools])
        if self.turns and isinstance(self.turns[0], asyncio.CancelledError):
            validate_messages(messages)
            self.requests.append(deepcopy(messages))
            raise self.turns.popleft()
        return await super().complete(messages, tools, limits)

    async def close(self):
        self.closed = True


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    home = tmp_path / "state"
    monkeypatch.setenv("REPOPILOT_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-fixture-key")
    monkeypatch.setattr("repopilot.cli.interactive_terminal", lambda: True)
    return root, home


def install_fake(monkeypatch, turns):
    fake = ChatFake(turns)
    monkeypatch.setattr("repopilot.cli.DeepSeekProvider", lambda settings, key: fake)
    return fake


def test_chat_followup_keeps_history_tool_pairs_and_one_event_loop(chat_env, monkeypatch):
    root, home = chat_env
    (root / "a.py").write_text("value = 1\n")
    fake = install_fake(
        monkeypatch,
        [
            ModelTurn(
                tool_calls=[ToolCall(id="read1", name="read_file", arguments='{"path":"a.py"}')]
            ),
            ModelTurn(content="First answer [E1]"),
            ModelTurn(content="Followup answer [E1]"),
        ],
    )
    result = CliRunner().invoke(
        app, ["chat", "--workspace", str(root)], input="Read a.py\nExplain it\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert "Followup answer" in result.output
    assert fake.closed and all(loop is fake.loops[0] for loop in fake.loops)
    assert [m.content for m in fake.requests[-1] if m.role == "user"] == [
        "Read a.py",
        "Explain it",
    ]
    assert "edit_file" not in fake.tool_names[0]
    validate_messages(fake.requests[-1])
    store = SessionStore(home)
    try:
        assert len(store.sessions()) == 1
        sid = store.sessions()[0]["id"]
        assert len(store.runs(sid)) == 2
        validate_messages(store.messages(sid))
        assert "会话:" in result.output
    finally:
        store.close()


def test_chat_new_session_drops_previous_context(chat_env, monkeypatch):
    root, home = chat_env
    fake = install_fake(monkeypatch, [ModelTurn(content="old"), ModelTurn(content="new")])
    result = CliRunner().invoke(
        app,
        ["chat", "--workspace", str(root)],
        input="Old task\n/new\nNew task\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert [m.content for m in fake.requests[-1] if m.role == "user"] == ["New task"]
    store = SessionStore(home)
    try:
        assert len(store.sessions()) == 2
    finally:
        store.close()


@pytest.mark.parametrize("user_input", ["\n/help\n/unknown\n/exit\n", ""])
def test_chat_exit_commands_and_eof_do_not_call_model(chat_env, monkeypatch, user_input):
    root, home = chat_env
    fake = install_fake(monkeypatch, [])
    result = CliRunner().invoke(app, ["chat", "--workspace", str(root)], input=user_input)
    assert result.exit_code == 0, result.output
    assert fake.closed and fake.requests == []
    assert not (home / "sessions.sqlite3").exists()


def test_chat_reopens_saved_session_and_mode_without_replaying(chat_env, monkeypatch):
    root, home = chat_env
    initial = asyncio.run(
        run_task(
            "Original task",
            root,
            home,
            Settings(),
            ScriptedFake([ModelTurn(content="old")]),
            mode="edit",
        )
    )
    fake = install_fake(monkeypatch, [ModelTurn(content="Followup")])
    result = CliRunner().invoke(
        app, ["chat", "--session", initial.session_id], input="Next task\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 1
    assert "edit_file" in fake.tool_names[0]
    assert [m.content for m in fake.requests[0] if m.role == "user"] == [
        "Original task",
        "Next task",
    ]
    store = SessionStore(home)
    try:
        assert store.session(initial.session_id)["workspace"] == str(root.resolve())
        assert len(store.runs(initial.session_id)) == 2
    finally:
        store.close()


def test_chat_rejects_session_workspace_mismatch_before_model(chat_env, monkeypatch, tmp_path):
    root, home = chat_env
    store = SessionStore(home)
    try:
        sid = store.create_session(root, "saved")
    finally:
        store.close()
    fake = install_fake(monkeypatch, [])
    result = CliRunner().invoke(
        app, ["chat", "--session", sid, "--workspace", str(tmp_path)], input="/exit\n"
    )
    assert result.exit_code == 2 and "WORKSPACE_CHANGED" in result.output
    assert not fake.requests


def test_chat_edit_still_denies_unapproved_operation(chat_env, monkeypatch):
    root, home = chat_env
    fake = install_fake(
        monkeypatch,
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="write1",
                        name="create_file",
                        arguments=json.dumps({"path": "new.py", "content": "x = 1\n"}),
                    )
                ]
            ),
            ModelTurn(content="Operation denied"),
        ],
    )
    result = CliRunner().invoke(
        app, ["chat", "--workspace", str(root), "--mode", "edit"], input="Create\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert not (root / "new.py").exists()
    tool_message = next(m for m in fake.requests[-1] if m.role == "tool")
    assert json.loads(tool_message.content)["status"] == "denied"
    store = SessionStore(home)
    try:
        assert store.tools(store.runs(store.sessions()[0]["id"])[0]["id"])[0]["status"] == "denied"
    finally:
        store.close()


def test_chat_failure_allows_explicit_followup_without_automatic_replay(chat_env, monkeypatch):
    root, home = chat_env
    fake = install_fake(
        monkeypatch, [RepoPilotError("PROVIDER_ERROR", "fixture failure"), ModelTurn(content="ok")]
    )
    result = CliRunner().invoke(
        app, ["chat", "--workspace", str(root)], input="First\nTry again\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 2 and fake.closed
    store = SessionStore(home)
    try:
        assert [r["status"] for r in store.runs(store.sessions()[0]["id"])] == [
            "failed",
            "completed",
        ]
    finally:
        store.close()


def test_chat_cancelled_task_is_saved_and_closes_provider(chat_env, monkeypatch):
    root, home = chat_env
    fake = install_fake(monkeypatch, [asyncio.CancelledError()])
    result = CliRunner().invoke(
        app, ["chat", "--workspace", str(root)], input="Cancel task\nNever sent\n"
    )
    assert result.exit_code == 0, result.output
    assert fake.closed and len(fake.requests) == 1
    store = SessionStore(home)
    try:
        assert store.runs(store.sessions()[0]["id"])[0]["status"] == "cancelled"
    finally:
        store.close()


def test_chat_requires_terminal(monkeypatch):
    monkeypatch.setattr("repopilot.cli.interactive_terminal", lambda: False)
    result = CliRunner().invoke(app, ["chat"], input="Should not run\n")
    assert result.exit_code == 2 and "INTERACTIVE_REQUIRED" in result.output


def test_chat_missing_key_fails_before_prompt(chat_env, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    result = CliRunner().invoke(app, ["chat"])
    assert result.exit_code == 2 and "KEY_MISSING" in result.output


def test_chat_help_describes_session_and_mode(monkeypatch):
    # Rich can put ANSI style boundaries inside option names on CI terminals.
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = CliRunner().invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    output = clean(result.output)
    assert "--session" in output and "--mode" in output
