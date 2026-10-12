import os
import shutil
from pathlib import Path

import pytest

from repopilot.config import TestProfile as Profile
from repopilot.errors import RepoPilotError
from repopilot.test_runner import TestRunner as Runner
from repopilot.test_runner import maven_command
from repopilot.workspace import Workspace


async def test_real_maven_in_space_path_and_bug_repair(tmp_path):
    executable = shutil.which("mvn")
    if executable is None:
        pytest.skip("Maven not installed")
    root = tmp_path / "java project 空格"
    shutil.copytree(Path(__file__).parent / "fixtures/java-project", root)
    runner = Runner(
        Workspace(root),
        {"java": Profile(kind="maven", executable=executable, args=["-q"], timeout=180)},
        tmp_path / "artifacts",
    )
    file = root / "src/main/java/demo/ShopService.java"
    original = file.read_text()
    file.write_text(original.replace("price - discount", "price + discount"))
    broken = await (await runner.prepare({"profile": "java"})).commit()
    assert broken.data["verification"] == "failed", broken.data
    file.write_text(original)
    fixed = await (await runner.prepare({"profile": "java"})).commit()
    assert fixed.data["verification"] == "passed", fixed.data
    assert fixed.data["counts"]["tests"] == 3


@pytest.mark.skipif(os.name != "nt", reason="Windows batch quoting")
@pytest.mark.parametrize("unsafe", ["&", "|", "%", "!", "^", '"', "\n"])
def test_windows_shell_syntax_rejected(unsafe):
    with pytest.raises(RepoPilotError):
        maven_command(Path("C:/Maven/mvn.cmd"), [f"-Dname=a{unsafe}b"], Path("C:/repo"))
