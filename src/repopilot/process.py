"""Bounded asynchronous process IO and ordinary child-process tree cleanup."""

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from repopilot.sanitize import clean


def child_environment() -> dict[str, str]:
    names = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "JAVA_HOME",
        "LANG",
        "LC_ALL",
        "APPDATA",
        "LOCALAPPDATA",
    }
    result = {name: value for name, value in os.environ.items() if name.upper() in names}
    result.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return result


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: str
    truncated: bool
    duration: float


async def stop_tree(process: asyncio.subprocess.Process) -> None:
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32/taskkill.exe"),
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def run_process(
    argv: list[str], cwd: Path, timeout: float, *, capture_bytes: int = 1024 * 1024
) -> ProcessResult:
    started = time.monotonic()
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=child_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    data = bytearray()
    truncated = False
    try:
        async with asyncio.timeout(timeout):
            assert process.stdout is not None
            while chunk := await process.stdout.read(8192):
                available = max(0, capture_bytes - len(data))
                data.extend(chunk[:available])
                truncated |= len(chunk) > available
            await process.wait()
    except BaseException:
        await asyncio.shield(stop_tree(process))
        raise
    return ProcessResult(
        process.returncode if process.returncode is not None else -1,
        clean(data.decode("utf-8", errors="replace")),
        truncated,
        time.monotonic() - started,
    )
