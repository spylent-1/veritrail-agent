import os
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


async def diff(executor, path="."):
    import json

    return await executor.execute(
        ToolCall(id="diff", name="git_diff", arguments=json.dumps({"path": path}))
    )


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


async def test_mixed_git_states_include_deleted_staged_and_untracked(tmp_path):
    _, executor = setup(tmp_path)
    git(tmp_path, "add", "a.py")
    (tmp_path / "a.py").write_text("value = 3\n")
    (tmp_path / "image.png").unlink()
    (tmp_path / "new file.py").write_text("fresh = 1\n")
    result = await diff(executor)
    assert result.status == "ok"
    text = result.data["diff"]
    assert "[staged]" in text and "+value = 2" in text
    assert "[unstaged]" in text and "+value = 3" in text
    assert "[untracked]" in text and "new file.py" in text
    # Deleted binary content is filtered even when no worktree file remains.
    assert "binary" not in text and "image.png" not in text


async def test_deleted_text_and_staged_deletion_with_recreated_file(tmp_path):
    _, executor = setup(tmp_path)
    git(tmp_path, "rm", "-f", "a.py")
    (tmp_path / "a.py").write_text("reborn = True\n")
    result = await diff(executor, "a.py")
    assert result.status == "ok"
    assert "[staged]" in result.data["diff"] and "-value = 1" in result.data["diff"]
    assert "[untracked]" in result.data["diff"] and "+reborn = True" in result.data["diff"]
    (tmp_path / "a.py").unlink()
    deleted = await diff(executor, "a.py")
    assert "-value = 1" in deleted.data["diff"]


async def test_unborn_repository_and_nested_scope(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "nested").mkdir()
    (root / "nested" / "中文 file.py").write_text("value = 1\n")
    (root / "outside.py").write_text("outside = True\n")
    git(root, "add", ".")
    registry = ToolRegistry()
    ReadTools(Workspace(root / "nested")).register(registry)
    result = await diff(ToolExecutor(registry, Limits()))
    assert result.status == "ok" and "中文 file.py" in result.data["diff"]
    assert "outside" not in result.data["diff"]


async def test_sensitive_and_ignored_paths_are_filtered_in_all_git_versions(tmp_path):
    _, executor = setup(tmp_path)
    (tmp_path / "a.py").write_bytes(b"old-binary\x00")
    git(tmp_path, "add", "a.py")
    (tmp_path / "a.py").write_text("safe_now = True\n")
    (tmp_path / ".env").write_text("PRIVATE_FIXTURE_VALUE=keep-private\n")
    git(tmp_path, "add", "-f", ".env")
    (tmp_path / ".env").unlink()
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("PRIVATE_FIXTURE_IGNORED\n")
    git(tmp_path, "add", "-f", "ignored.py")
    result = await diff(executor)
    assert result.status == "ok"
    assert "PRIVATE_FIXTURE" not in result.data["diff"]
    assert "safe_now" not in result.data["diff"]  # index blob is binary
    explicit = await diff(executor, "a.py")
    assert explicit.error_code == "UNSUPPORTED_ENCODING"


async def test_rename_is_delete_plus_add_and_scope_is_literal(tmp_path):
    _, executor = setup(tmp_path)
    git(tmp_path, "mv", "a.py", "renamed.py")
    result = await diff(executor)
    assert "--- a/a.py" in result.data["diff"]
    assert "+++ b/renamed.py" in result.data["diff"]
    escaped = await diff(executor, "../outside")
    assert escaped.error_code == "PATH_DENIED"


async def test_diff_output_limit_is_explicit(tmp_path):
    _, executor = setup(tmp_path)
    (tmp_path / "a.py").write_text("changed = 1234567890\n" * 2000)
    result = await diff(executor)
    assert result.status == "ok" and result.truncated
    assert len(result.data["diff"].encode()) <= 16384


async def test_diff_rejects_link_instead_of_reading_target(tmp_path):
    _, executor = setup(tmp_path)
    target = tmp_path / "outside.py"
    target.write_text("DO_NOT_READ_LINK_TARGET\n")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(target)
    except OSError:
        if os.name != "nt":
            raise
        # Exercise Git's symlink mode on Windows without requiring symlink privileges.
        oid = (
            subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=tmp_path,
                input=b"outside.py",
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        git(tmp_path, "update-index", "--add", "--cacheinfo", f"120000,{oid},linked.py")
        link.write_text("regular worktree cannot hide index link\n")
    result = await diff(executor, "linked.py")
    assert result.error_code == "PATH_DENIED"
    assert "DO_NOT_READ_LINK_TARGET" not in result.model_dump_json()


async def test_unstaged_deletion_and_literal_filename(tmp_path):
    _, executor = setup(tmp_path)
    (tmp_path / "a.py").unlink()
    deleted = await diff(executor, "a.py")
    assert "[unstaged]" in deleted.data["diff"]
    assert "-value = 1" in deleted.data["diff"] and "+++ /dev/null" in deleted.data["diff"]
    (tmp_path / "name[1].py").write_text("literal_only = True\n")
    (tmp_path / "name1.py").write_text("must_not_match = True\n")
    result = await diff(executor, "name[1].py")
    assert "literal_only" in result.data["diff"]
    assert "must_not_match" not in result.data["diff"]


async def test_oversized_index_cannot_hide_behind_small_worktree(tmp_path):
    _, executor = setup(tmp_path)
    (tmp_path / "a.py").write_text("x" * (1024 * 1024 + 1))
    git(tmp_path, "add", "a.py")
    (tmp_path / "a.py").write_text("small = 1\n")
    result = await diff(executor, "a.py")
    assert result.error_code == "FILE_TOO_LARGE"
    directory = await diff(executor)
    assert directory.status == "ok" and directory.data["skipped_files"] >= 1
    assert "small = 1" not in directory.data["diff"]
