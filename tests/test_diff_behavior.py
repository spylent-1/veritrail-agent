import subprocess

from repopilot.config import Limits
from repopilot.errors import RepoPilotError
from repopilot.models import ToolCall
from repopilot.tools import ReadTools, ToolExecutor, ToolRegistry
from repopilot.workspace import Workspace


def setup(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "a.py").write_text("value = 1\n")
    (tmp_path / "image.png").write_bytes(b"binary\x00image")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "a.py").write_text("value = 2\n")
    workspace = Workspace(tmp_path)
    registry = ToolRegistry()
    ReadTools(workspace).register(registry)
    return workspace, ToolExecutor(registry, Limits())


async def test_directory_diff_skips_binary_but_explicit_binary_is_rejected(tmp_path):
    _, executor = setup(tmp_path)
    result = await executor.execute(ToolCall(id="d", name="git_diff", arguments='{"path":"."}'))
    assert result.status == "ok"
    assert "+value = 2" in result.data["diff"]
    binary = await executor.execute(
        ToolCall(id="b", name="git_diff", arguments='{"path":"image.png"}')
    )
    assert binary.error_code == "UNSUPPORTED_ENCODING"


async def test_directory_diff_does_not_hide_non_encoding_policy_errors(tmp_path, monkeypatch):
    workspace, executor = setup(tmp_path)
    original = workspace.snapshot

    def deny(relative):
        if relative == "a.py":
            raise RepoPilotError("PATH_DENIED", "fixture race")
        return original(relative)

    monkeypatch.setattr(workspace, "snapshot", deny)
    result = await executor.execute(ToolCall(id="d", name="git_diff", arguments='{"path":"."}'))
    assert result.error_code == "PATH_DENIED"


async def test_nested_directory_diff_also_skips_binary(tmp_path):
    _, executor = setup(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "a.py").write_text("value = 3\n")
    (nested / "image.png").write_bytes(b"binary\x00image")
    result = await executor.execute(
        ToolCall(id="nested", name="git_diff", arguments='{"path":"nested"}')
    )
    assert result.status == "ok"
