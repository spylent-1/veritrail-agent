import json

import pytest

from repopilot.config import Limits
from repopilot.editor import Editor
from repopilot.models import ToolCall
from repopilot.safety import PolicyEngine
from repopilot.tools import ReadTools, ToolExecutor, ToolRegistry
from repopilot.workspace import Workspace, digest


class Approval:
    def __init__(self, granted=True, callback=None):
        self.granted = granted
        self.callback = callback
        self.previews = []

    async def confirm(self, action):
        self.previews.append(action.preview)
        if self.callback:
            self.callback()
        return self.granted


async def setup(tmp_path, data=b"x = 1\n", approval=None):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_bytes(data)
    workspace = Workspace(root)
    readers = ReadTools(workspace)
    registry = ToolRegistry()
    readers.register(registry)
    Editor(workspace, readers, tmp_path / "artifacts").register(registry)
    executor = ToolExecutor(registry, Limits(), policy=PolicyEngine("edit"), approval=approval)
    await executor.execute(ToolCall(id="r", name="read_file", arguments='{"path":"a.py"}'))
    return root, executor


def edit(data, old="x = 1", new="x = 2"):
    return ToolCall(
        id="e",
        name="edit_file",
        arguments=json.dumps(
            {
                "path": "a.py",
                "expected_hash": digest(data),
                "edits": [{"old_text": old, "new_text": new}],
            }
        ),
    )


async def test_exact_edit_preserves_bom_crlf_and_dirty_content(tmp_path):
    data = b"\xef\xbb\xbf# existing user change\r\nx = 1\r\n"
    approval = Approval()
    root, executor = await setup(tmp_path, data, approval)
    result = await executor.execute(edit(data))
    assert result.status == "ok"
    assert (root / "a.py").read_bytes() == data.replace(b"x = 1", b"x = 2")
    assert "-x = 1" in approval.previews[0] and "+x = 2" in approval.previews[0]


async def test_rejected_edit_has_no_workspace_effect(tmp_path):
    root, executor = await setup(tmp_path, approval=Approval(False))
    result = await executor.execute(edit(b"x = 1\n"))
    assert result.status == "denied" and (root / "a.py").read_bytes() == b"x = 1\n"


async def test_approval_race_preserves_user_change(tmp_path):
    root, executor = await setup(tmp_path, approval=Approval())
    executor.approval = Approval(callback=lambda: (root / "a.py").write_text("user changed"))
    result = await executor.execute(edit(b"x = 1\n"))
    assert result.error_code == "FILE_CONFLICT"
    assert (root / "a.py").read_text() == "user changed"


@pytest.mark.parametrize(
    "data,expected",
    [(b"x = 1\nx = 1\n", "MATCH_CONFLICT"), (b"x = 1\r\nx = 3\n", "MIXED_NEWLINES")],
)
async def test_ambiguous_and_mixed_edits_rejected(tmp_path, data, expected):
    _, executor = await setup(tmp_path, data, Approval())
    assert (await executor.execute(edit(data))).error_code == expected


async def test_create_never_overwrites(tmp_path):
    root, executor = await setup(tmp_path, approval=Approval())
    call = ToolCall(
        id="new", name="create_file", arguments='{"path":"nested/new.py","content":"x = 42\\n"}'
    )
    assert (await executor.execute(call)).status == "ok"
    assert (root / "nested/new.py").read_text() == "x = 42\n"
    assert (await executor.execute(call)).error_code == "FILE_CONFLICT"


async def test_hash_without_prior_read_cannot_authorize_edit(tmp_path):
    _, executor = await setup(tmp_path, approval=Approval())
    # The observation is owned by ReadTools, not supplied by the model.
    definition = executor.registry.get("edit_file")
    definition.prepare.__self__.readers.observed.clear()
    assert (await executor.execute(edit(b"x = 1\n"))).error_code == "READ_REQUIRED"
