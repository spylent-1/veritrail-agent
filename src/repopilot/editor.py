"""Exact text edits, durable snapshots and single-file atomic writes."""

import difflib
import os
import tempfile
import uuid
from pathlib import Path

from pydantic import Field

from repopilot.config import StrictModel
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject
from repopilot.safety import PreparedAction
from repopilot.tools import ReadTools, ToolDefinition, ToolOutput, ToolRegistry
from repopilot.workspace import MAX_FILE_BYTES, Workspace, digest


class Replacement(StrictModel):
    old_text: str = Field(min_length=1)
    new_text: str


class EditArgs(StrictModel):
    path: str
    expected_hash: str = Field(pattern="^[0-9a-f]{64}$")
    edits: list[Replacement] = Field(min_length=1, max_length=20)


class CreateArgs(StrictModel):
    path: str
    content: str


def atomic_write(path: Path, data: bytes, *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".repopilot-write-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if not create:
            os.chmod(temp, path.stat().st_mode)
            os.replace(temp, path)
        else:
            # Atomic create-if-absent, unlike check + replace which can overwrite a new file.
            os.link(temp, path)
            temp.unlink()
    finally:
        temp.unlink(missing_ok=True)


class Editor:
    def __init__(self, workspace: Workspace, readers: ReadTools, artifact_dir: Path) -> None:
        self.workspace = workspace
        self.readers = readers
        self.artifact_dir = artifact_dir

    async def prepare_edit(self, args: JsonObject) -> PreparedAction:
        snap = self.workspace.snapshot(args["path"])
        if self.readers.observed.get(snap.path) != args["expected_hash"]:
            raise RepoPilotError("READ_REQUIRED", "Read the current file before proposing an edit.")
        if snap.sha256 != args["expected_hash"]:
            raise RepoPilotError("FILE_CONFLICT", "File changed since it was read.")
        if snap.newline == "mixed":
            raise RepoPilotError("MIXED_NEWLINES", "Mixed line endings require manual correction.")
        replacements: list[tuple[int, int, str]] = []
        for item in args["edits"]:
            old = self._normalize(item["old_text"], snap.newline)
            new = self._normalize(item["new_text"], snap.newline)
            if snap.text.count(old) != 1:
                raise RepoPilotError("MATCH_CONFLICT", "Each old_text must match exactly once.")
            start = snap.text.index(old)
            replacements.append((start, start + len(old), new))
        ordered = sorted(replacements)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:], strict=False)):
            raise RepoPilotError("MATCH_CONFLICT", "Replacement regions cannot overlap.")
        text = snap.text
        for start, end, new in reversed(ordered):
            text = text[:start] + new + text[end:]
        after = (b"\xef\xbb\xbf" if snap.bom else b"") + text.encode("utf-8")
        return self._prepare(snap.path, snap.raw, after, snap.sha256)

    async def prepare_create(self, args: JsonObject) -> PreparedAction:
        path = self.workspace.resolve(args["path"])
        if os.path.lexists(path):
            raise RepoPilotError("FILE_CONFLICT", "create_file cannot overwrite an existing path.")
        content = self._normalize(args["content"], "\n")
        return self._prepare(
            path.relative_to(self.workspace.root).as_posix(), None, content.encode(), None
        )

    @staticmethod
    def _normalize(text: str, newline: str) -> str:
        value = text.replace("\r\n", "\n")
        if "\r" in value or "\x00" in value:
            raise RepoPilotError("INVALID_TEXT", "Only ordinary UTF-8 text is supported.")
        return value.replace("\n", newline)

    def _prepare(
        self, relative: str, before: bytes | None, after: bytes, expected: str | None
    ) -> PreparedAction:
        if len(after) > MAX_FILE_BYTES:
            raise RepoPilotError("FILE_TOO_LARGE", "Resulting file exceeds 1 MiB.")
        if before == after:
            raise RepoPilotError("NO_CHANGE", "The proposed edit does not change the file.")
        preview = "".join(
            difflib.unified_diff(
                (before or b"").decode("utf-8-sig").splitlines(keepends=True),
                after.decode("utf-8-sig").splitlines(keepends=True),
                fromfile=f"a/{relative}" if before is not None else "/dev/null",
                tofile=f"b/{relative}",
            )
        )
        if len(preview.encode()) > 65536:
            raise RepoPilotError(
                "EDIT_TOO_BROAD", "Split the edit into smaller reviewable changes."
            )
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        key = uuid.uuid4().hex
        before_file = self.artifact_dir / f"{key}.before"
        after_file = self.artifact_dir / f"{key}.after"
        if before is not None:
            before_file.write_bytes(before)
        after_file.write_bytes(after)
        (self.artifact_dir / f"{key}.diff").write_text(preview, encoding="utf-8")
        intent = {
            "kind": "file_write",
            "path": relative,
            "before_hash": expected,
            "after_hash": digest(after),
            "before_artifact": str(before_file) if before else None,
            "after_artifact": str(after_file),
            "diff_artifact": f"{key}.diff",
        }
        # Empty existing files also need a before snapshot for rollback.
        if before is not None:
            intent["before_artifact"] = str(before_file)

        async def commit() -> ToolOutput:
            path = self.workspace.resolve(relative)
            if expected is None:
                if os.path.lexists(path):
                    raise RepoPilotError(
                        "FILE_CONFLICT", "Path was created while awaiting approval."
                    )
            elif self.workspace.snapshot(relative).sha256 != expected:
                raise RepoPilotError("FILE_CONFLICT", "File changed while awaiting approval.")
            atomic_write(path, after, create=expected is None)
            return ToolOutput(
                {
                    "changed_file": relative,
                    "before_hash": expected,
                    "after_hash": digest(after),
                    "diff_artifact": f"{key}.diff",
                }
            )

        return PreparedAction("write", preview, intent, commit)

    def register(self, registry: ToolRegistry) -> None:
        async def no_bypass(args: JsonObject) -> ToolOutput:
            raise RepoPilotError("UNSAFE_TOOL", "Editing requires preparation and host approval.")

        registry.register(
            ToolDefinition(
                "edit_file",
                "Propose unique exact replacements after reading "
                "the file; expected_hash must be the hash returned by read_file.",
                EditArgs,
                no_bypass,
                "write",
                self.prepare_edit,
            )
        )
        registry.register(
            ToolDefinition(
                "create_file",
                "Propose a new UTF-8 file; never overwrite.",
                CreateArgs,
                no_bypass,
                "write",
                self.prepare_create,
            )
        )
