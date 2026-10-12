import os
import subprocess
import sys

import pytest

from repopilot.errors import RepoPilotError
from repopilot.workspace import MAX_FILE_BYTES, Workspace


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "a/../../outside",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "C:relative",
        "\\\\server\\share",
        "\\\\?\\C:\\file",
        "x.txt:secret",
        "NUL",
        "con.txt",
        "foo.",
        "foo ",
        ".git/config",
        ".env",
        ".env.production",
        "a/id_rsa",
        ".aws/config",
        "a/private.pem",
        ".venv/main.py",
    ],
)
def test_unsafe_paths(tmp_path, path):
    with pytest.raises(RepoPilotError):
        Workspace(tmp_path).resolve(path)


def test_read_utf8_bom_crlf_and_chinese_path(tmp_path):
    name = "中文 空格.txt"
    data = b"\xef\xbb\xbf" + "第一行\r\nsecond\r\n".encode()
    (tmp_path / name).write_bytes(data)
    snap = Workspace(tmp_path).snapshot(name)
    assert snap.raw == data
    assert snap.bom and snap.newline == "\r\n"
    assert len(snap.sha256) == 64


@pytest.mark.parametrize(
    "data,code",
    [
        (b"a\x00b", "UNSUPPORTED_ENCODING"),
        (b"\xff\xfe", "UNSUPPORTED_ENCODING"),
        (b"a" * (MAX_FILE_BYTES + 1), "FILE_TOO_LARGE"),
    ],
    ids=["binary", "invalid-utf8", "large"],
)
def test_bad_files(tmp_path, data, code):
    (tmp_path / "file.txt").write_bytes(data)
    with pytest.raises(RepoPilotError) as error:
        Workspace(tmp_path).snapshot("file.txt")
    assert error.value.code == code


def test_listing_filters_sensitive_generated_and_ignored(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "ok.py").write_text("x = 1")
    (tmp_path / ".env").write_text("SECRET=value")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "ignored.txt").write_text("ignored")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "compiled.txt").write_text("generated")
    files = Workspace(tmp_path).list_files()
    assert "ok.py" in files
    assert ".env" not in files and "ignored.txt" not in files
    assert not any(p.startswith((".git/", "target/")) for p in files)


def test_symlink_or_junction_cannot_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("must not read")
    root = tmp_path / "workspace"
    root.mkdir()
    link = root / "link"
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(RepoPilotError, match="Links"):
            Workspace(root).snapshot("link/secret.txt")
        with pytest.raises(RepoPilotError):
            Workspace(root).resolve("link/new.txt")
    finally:
        if sys.platform == "win32":
            os.rmdir(link)
        else:
            link.unlink()


def test_internal_state_is_excluded_even_with_custom_name(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    with pytest.raises(RepoPilotError):
        Workspace(tmp_path, excluded_roots=(state,)).resolve("custom-state/db.sqlite")
