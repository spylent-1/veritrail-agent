import asyncio
import json
import sys

import pytest

from repopilot.config import Limits
from repopilot.config import TestProfile as Profile
from repopilot.models import ToolCall
from repopilot.process import run_process
from repopilot.safety import PolicyEngine
from repopilot.test_runner import TestRunner as Runner
from repopilot.tools import ToolExecutor, ToolRegistry
from repopilot.workspace import Workspace


class Approve:
    async def confirm(self, action):
        return True


def setup(tmp_path, timeout=20, approve=True):
    root = tmp_path / "repo"
    root.mkdir()
    registry = ToolRegistry()
    Runner(
        Workspace(root),
        {"unit": Profile(executable=sys.executable, timeout=timeout, args=["-q"])},
        tmp_path / "artifacts",
    ).register(registry)
    return root, ToolExecutor(
        registry, Limits(), policy=PolicyEngine("edit"), approval=Approve() if approve else None
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("def test_ok(): assert 1 == 1\n", "passed"),
        ("def test_bad(): assert 1 == 2\n", "failed"),
        ("", "not_run"),
        ("import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n", "not_run"),
    ],
)
async def test_actual_pytest_status(tmp_path, code, expected):
    root, executor = setup(tmp_path)
    (root / "test_example.py").write_text(code)
    result = await executor.execute(
        ToolCall(id="t", name="run_tests", arguments='{"profile":"unit"}')
    )
    assert result.status == "ok", result
    assert result.data["verification"] == expected


async def test_tests_cannot_receive_host_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "never-in-child")
    root, executor = setup(tmp_path)
    (root / "test_env.py").write_text(
        "import os\ndef test_env(): assert 'DEEPSEEK_API_KEY' not in os.environ\n"
    )
    result = await executor.execute(
        ToolCall(id="t", name="run_tests", arguments='{"profile":"unit"}')
    )
    assert result.data["verification"] == "passed"


async def test_deny_test_does_not_start_process(tmp_path):
    root, executor = setup(tmp_path, approve=False)
    (root / "test_side.py").write_text("from pathlib import Path\nPath('marker').touch()")
    result = await executor.execute(
        ToolCall(id="t", name="run_tests", arguments='{"profile":"unit"}')
    )
    assert result.status == "denied" and not (root / "marker").exists()


async def test_timeout_and_large_output(tmp_path):
    with pytest.raises(TimeoutError):
        await run_process([sys.executable, "-c", "import time; time.sleep(20)"], tmp_path, 0.3)
    result = await run_process(
        [sys.executable, "-c", "print('x'*100000)"], tmp_path, 5, capture_bytes=1000
    )
    assert result.truncated and len(result.output) == 1000


async def test_cancel_cleans_process(tmp_path):
    task = asyncio.create_task(
        run_process([sys.executable, "-c", "import time; time.sleep(20)"], tmp_path, 30)
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_extra_model_command_arguments_rejected(tmp_path):
    _, executor = setup(tmp_path)
    result = await executor.execute(
        ToolCall(
            id="t",
            name="run_tests",
            arguments=json.dumps(
                {
                    "profile": "unit",
                    "args": ["dangerous"],
                }
            ),
        )
    )
    assert result.error_code == "INVALID_ARGUMENTS"
