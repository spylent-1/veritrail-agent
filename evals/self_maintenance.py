"""Exercise real maintenance on an isolated copy of RepoPilot's tracked source."""

import asyncio
import json
import shutil
import subprocess
import sys
import time

from evals.run import REPO, FixtureApproval
from repopilot.application import run_task
from repopilot.config import Settings, TestProfile, api_key
from repopilot.deepseek import DeepSeekProvider
from repopilot.process import run_process


async def main() -> None:
    output = REPO / ".eval-runs" / f"self-maintenance-{time.time_ns()}"
    root = output / "workspace"
    root.mkdir(parents=True)
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO).decode().split("\x00")
    for relative in filter(None, files):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    test = root / "tests/test_diff_behavior.py"
    shutil.copy2(REPO / "tests/test_diff_behavior.py", test)
    profile_args = [
        "-q",
        "tests/test_diff_behavior.py",
        "-p",
        "pytest_asyncio.plugin",
        "-o",
        "pythonpath=src",
    ]
    before = await run_process([sys.executable, "-m", "pytest", *profile_args], root, 60)
    assert before.returncode != 0, "The real maintenance case must fail before editing."
    settings = Settings(
        tests={"unit": TestProfile(executable=sys.executable, args=profile_args, timeout=60)}
    )
    provider = DeepSeekProvider(settings.model, api_key() or "")
    try:
        report = await run_task(
            "修复 RepoPilot 自身 git_diff：目录范围遇到二进制/超大非文本文件应跳过，"
            "显式指定该文件仍返回原来的错误；不得吞掉 PATH_DENIED 等其他错误。"
            "先运行 unit 测试，只允许局部修改 src/repopilot/tools.py，测试文件只读。"
            "修改后运行 unit，解释实际结果。",
            root,
            output / "state",
            settings,
            provider,
            mode="edit",
            approval=FixtureApproval(root, "src/repopilot/tools.py"),
        )
    finally:
        await provider.close()
    after = await run_process([sys.executable, "-m", "pytest", *profile_args], root, 60)
    evidence = {
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "workspace": str(root),
        "before_returncode": before.returncode,
        "after_returncode": after.returncode,
        "after_output": after.output,
        "report": report.model_dump(),
        "original_was_modified": False,
    }
    path = REPO / "docs/validation/self-maintenance.json"
    path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report.status,
                "verification": report.verification,
                "model_turns": report.model_turns,
                "tool_calls": report.tool_calls,
                "changed_files": report.changed_files,
                "after_returncode": after.returncode,
                "workspace": str(root),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    assert (
        report.status == "completed" and report.verification == "passed" and after.returncode == 0
    )


if __name__ == "__main__":
    asyncio.run(main())
