"""Bounded, policy-checked diffs for HEAD, index and worktree content."""

import difflib
import subprocess
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject
from repopilot.workspace import MAX_FILE_BYTES, Workspace


def read_git_diff(workspace: Workspace, relative: str, git: str) -> tuple[JsonObject, bool]:
    deadline = time.monotonic() + 9
    git_root = workspace.root

    def run(*args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RepoPilotError("TIMEOUT", "Narrow the Git diff scope.")
        result = subprocess.run(
            [
                git,
                "--no-optional-locks",
                *([] if "check-ignore" in args else ["--literal-pathspecs"]),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *args,
            ],
            cwd=git_root,
            capture_output=True,
            timeout=min(5, remaining),
            check=False,
        )
        if result.returncode not in allowed:
            raise RepoPilotError("GIT_DIFF_FAILED", "Git state could not be read safely.")
        return result

    selected = workspace.resolve(relative, allow_root=True)
    root = Path(run("rev-parse", "--show-toplevel").stdout.decode("utf-8").strip()).resolve()
    git_root = root
    scope = selected.relative_to(root).as_posix()
    status = run(
        "-c",
        "status.relativePaths=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
        "--",
        scope,
    ).stdout
    if len(status) > 4 * 1024 * 1024:
        raise RepoPilotError("DIFF_TOO_BROAD", "Specify a smaller directory or file.")
    entries: dict[str, list[str]] = {}
    for record in status.split(b"\0"):
        if not record:
            continue
        name = record[3:].decode("utf-8")
        path = root / name
        if not path.is_relative_to(workspace.root):
            continue
        entries.setdefault(path.relative_to(workspace.root).as_posix(), []).append(
            record[:2].decode("ascii")
        )
    if len(entries) > 200:
        raise RepoPilotError("DIFF_TOO_BROAD", "Specify a smaller directory or file.")
    directory = selected.is_dir() or any(
        selected in (workspace.root / name).parents for name in entries
    )
    # Preserve explicit-file validation even when Git reports no changes.
    if not directory and selected.is_file():
        workspace.snapshot(relative)

    def blob(name: str, tree: str) -> str | None:
        if tree == "HEAD":
            if run("rev-parse", "--verify", "HEAD", allowed=(0, 128)).returncode:
                return None
            output = run("ls-tree", "-z", "HEAD", "--", name).stdout
        else:
            output = run("ls-files", "--stage", "-z", "--", name).stdout
        if not output:
            return None
        records = [item for item in output.split(b"\0") if item]
        if len(records) != 1:
            raise RepoPilotError(
                "GIT_CONFLICT", "Resolve index conflicts before requesting a diff."
            )
        meta = records[0].split(b"\t", 1)[0].split()
        mode, oid = (meta[0], meta[2]) if tree == "HEAD" else (meta[0], meta[1])
        if mode not in (b"100644", b"100755"):
            raise RepoPilotError("PATH_DENIED", "Git links and submodules are excluded.")
        object_id = oid.decode("ascii")
        if int(run("cat-file", "-s", object_id).stdout) > MAX_FILE_BYTES:
            raise RepoPilotError("FILE_TOO_LARGE", "Git blob exceeds 1 MiB.")
        raw = run("cat-file", "blob", object_id).stdout
        if len(raw) > MAX_FILE_BYTES:
            raise RepoPilotError("FILE_TOO_LARGE", "Git blob exceeds 1 MiB.")
        if b"\x00" in raw:
            raise RepoPilotError("UNSUPPORTED_ENCODING", "Binary Git blobs are excluded.")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RepoPilotError(
                "UNSUPPORTED_ENCODING", "Only UTF-8 Git blobs are supported."
            ) from exc

    parts: list[str] = []
    used = 0
    skipped = 0
    truncated = False
    for name, states in sorted(entries.items()):
        try:
            path = workspace.resolve(name)
            repo_name = path.relative_to(root).as_posix()
            ignored = run("check-ignore", "--no-index", "-q", "--", repo_name, allowed=(0, 1))
            if ignored.returncode == 0:
                skipped += 1
                continue
            if any("U" in state or state in ("AA", "DD") for state in states):
                raise RepoPilotError(
                    "GIT_CONFLICT", "Resolve index conflicts before requesting a diff."
                )
            current = workspace.snapshot(name).text if path.is_file() else None
            base = blob(repo_name, "HEAD")
            index = blob(repo_name, "index")
        except RepoPilotError as exc:
            if directory and exc.code in (
                "FILE_EXCLUDED",
                "UNSUPPORTED_ENCODING",
                "FILE_TOO_LARGE",
            ):
                skipped += 1
                continue
            raise
        changes: list[tuple[str, str | None, str | None]] = []
        if any(state != "??" and state[0] != " " for state in states):
            changes.append(("staged", base, index))
        if any(state != "??" and state[1] != " " for state in states):
            changes.append(("unstaged", index, current))
        if "??" in states:
            changes.append(("untracked", None, current))
        for section, before, after in changes:
            header = f"[{section}] {name}\n"
            if before == after:
                header += "Git reports a mode or encoding change; decoded text is unchanged.\n"
            patch = difflib.unified_diff(
                (before or "").splitlines(keepends=True),
                (after or "").splitlines(keepends=True),
                fromfile=f"a/{name}" if before is not None else "/dev/null",
                tofile=f"b/{name}" if after is not None else "/dev/null",
            )
            # The status header also represents additions/deletions of empty files.
            for chunk in _patch_chunks(header, patch):
                encoded = chunk.encode("utf-8")
                if used + len(encoded) > 16384:
                    parts.append(encoded[: 16384 - used].decode("utf-8", errors="ignore"))
                    truncated = True
                    break
                parts.append(chunk)
                used += len(encoded)
            if truncated:
                break
        if truncated:
            break
    return {
        "diff": "".join(parts),
        "scope": (
            "Staged, unstaged and untracked Git changes; may include pre-existing user changes."
        ),
        "skipped_files": skipped,
    }, truncated


def _patch_chunks(header: str, patch: Iterable[str]) -> Iterator[str]:
    yield header
    for line in patch:
        yield line
        if not line.endswith("\n"):
            yield "\n\\ No newline at end of file\n"
