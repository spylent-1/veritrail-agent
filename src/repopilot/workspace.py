"""Shared file visibility policy. This is not an OS sandbox."""

import fnmatch
import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from repopilot.errors import RepoPilotError

MAX_FILE_BYTES = 1024 * 1024
EXCLUDED_DIRS = {
    ".git",
    ".repopilot",
    ".ssh",
    ".aws",
    ".azure",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "target",
    "dist",
    "build",
    ".eval-runs",
}
SENSITIVE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "id_rsa*",
    "id_ed25519*",
    "credentials*",
    "secrets*",
    ".npmrc",
    ".pypirc",
)
RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_link(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    raw: bytes
    text: str
    sha256: str
    newline: str
    bom: bool


class Workspace:
    def __init__(self, root: Path, *, excluded_roots: tuple[Path, ...] = ()) -> None:
        if not root.is_dir() or is_link(root):
            raise RepoPilotError("INVALID_WORKSPACE", "Workspace must be a real local directory.")
        self.root = root.resolve()
        self.excluded_roots = tuple(p.resolve() for p in excluded_roots)
        self.rg = shutil.which("rg")

    def resolve(self, relative: str, *, allow_root: bool = False) -> Path:
        name = relative.replace("\\", "/")
        win = PureWindowsPath(name)
        if name in ("", ".") and allow_root:
            return self.root
        if not name or name.startswith("/") or win.drive:
            raise RepoPilotError("PATH_DENIED", "Only workspace-relative paths are accepted.")
        parts = name.split("/")
        for part in parts:
            if (
                part in ("", ".", "..")
                or any(c in part for c in ':<>"|?*\x00')
                or any(ord(c) < 32 for c in part)
                or part.endswith((" ", "."))
                or part.split(".")[0].casefold() in RESERVED
            ):
                raise RepoPilotError("PATH_DENIED", "Unsafe path component.")
            lower = part.casefold()
            if lower in EXCLUDED_DIRS or (
                lower != ".env.example"
                and any(fnmatch.fnmatchcase(lower, p) for p in SENSITIVE_PATTERNS)
            ):
                raise RepoPilotError("FILE_EXCLUDED", "Sensitive or generated path is excluded.")
        current = self.root
        for part in parts:
            current = current / part
            if os.path.lexists(current):
                if is_link(current):
                    raise RepoPilotError("PATH_DENIED", "Links and reparse points are excluded.")
                if current.is_file() and current.stat().st_nlink > 1:
                    raise RepoPilotError("PATH_DENIED", "Hard-linked files are excluded.")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root) or any(
            resolved.is_relative_to(p) for p in self.excluded_roots
        ):
            raise RepoPilotError("PATH_DENIED", "Path is outside the permitted workspace.")
        return current

    def snapshot(self, relative: str) -> FileSnapshot:
        path = self.resolve(relative)
        if not path.is_file():
            raise RepoPilotError("NOT_FOUND", "The requested regular file does not exist.")
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise RepoPilotError("FILE_TOO_LARGE", "Files larger than 1 MiB are not supported.")
        if b"\x00" in raw:
            raise RepoPilotError("UNSUPPORTED_ENCODING", "Binary and UTF-16 files are excluded.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RepoPilotError("UNSUPPORTED_ENCODING", "Only UTF-8 text is supported.") from exc
        remainder = text.replace("\r\n", "")
        newline = "\r\n" if "\r\n" in text else "\n"
        if "\r" in remainder or (newline == "\r\n" and "\n" in remainder):
            newline = "mixed"
        return FileSnapshot(
            path.relative_to(self.root).as_posix(),
            raw,
            text,
            digest(raw),
            newline,
            raw.startswith(b"\xef\xbb\xbf"),
        )

    def list_files(self, relative: str = ".", pattern: str = "*", limit: int = 200) -> list[str]:
        directory = self.resolve(relative, allow_root=True)
        if not directory.is_dir():
            raise RepoPilotError("NOT_DIRECTORY", "File listing requires a directory.")
        if not self.rg:
            raise RepoPilotError("RG_MISSING", "Install ripgrep and put rg on PATH.")
        try:
            result = subprocess.run(
                [self.rg, "--no-config", "--files", "--hidden", "--null", "--", "."],
                cwd=directory,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepoPilotError("TIMEOUT", "Workspace enumeration timed out.") from exc
        if result.returncode not in (0, 1):
            raise RepoPilotError("SEARCH_FAILED", "ripgrep could not enumerate this workspace.")
        if len(result.stdout) > 4 * 1024 * 1024:
            raise RepoPilotError("LIST_TOO_LARGE", "Narrow the file listing directory.")
        files: list[str] = []
        for entry in result.stdout.decode("utf-8", errors="replace").split("\x00"):
            if not entry:
                continue
            candidate = directory / entry
            rel = candidate.relative_to(self.root).as_posix()
            try:
                path = self.resolve(rel)
            except RepoPilotError:
                continue
            if path.is_file() and fnmatch.fnmatchcase(rel, pattern):
                files.append(rel)
        return sorted(files)[:limit]
