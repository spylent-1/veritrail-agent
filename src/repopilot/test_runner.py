"""Host-configured test profiles. A successful process exit alone never proves a passing test."""

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import Field

from repopilot.config import StrictModel, TestProfile
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject
from repopilot.process import run_process
from repopilot.safety import PreparedAction
from repopilot.tools import ToolDefinition, ToolOutput, ToolRegistry
from repopilot.workspace import Workspace


class TestArgs(StrictModel):
    profile: str = Field(min_length=1)


def junit_counts(files: list[Path]) -> JsonObject:
    counts = {"tests": 0, "failed": 0, "errors": 0, "skipped": 0}
    for file in files:
        raw = file.read_bytes()
        if len(raw) > 2 * 1024 * 1024 or b"<!DOCTYPE" in raw.upper():
            raise RepoPilotError("INVALID_REPORT", "Test report size or XML format is unsupported.")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise RepoPilotError("INVALID_REPORT", "Test report is not valid XML.") from exc
        for case in root.iter("testcase"):
            counts["tests"] += 1
            counts["failed"] += case.find("failure") is not None
            counts["errors"] += case.find("error") is not None
            counts["skipped"] += case.find("skipped") is not None
    return counts


class TestRunner:
    __test__ = False

    def __init__(
        self,
        workspace: Workspace,
        profiles: dict[str, TestProfile],
        artifact_dir: Path,
        output_bytes: int = 16384,
    ) -> None:
        self.workspace = workspace
        self.profiles = profiles
        self.artifact_dir = artifact_dir
        self.output_bytes = output_bytes

    async def prepare(self, args: JsonObject) -> PreparedAction:
        name = args["profile"]
        if name not in self.profiles:
            raise RepoPilotError("UNKNOWN_PROFILE", "Use a host-configured test profile name.")
        profile = self.profiles[name]
        executable = Path(profile.executable)
        if not executable.is_absolute() or not executable.is_file():
            raise RepoPilotError(
                "INVALID_EXECUTABLE", "Configure an absolute installed executable."
            )
        if profile.kind != "pytest":
            raise RepoPilotError("UNSUPPORTED_RUNNER", "Maven support is not installed yet.")
        cwd = self.workspace.resolve(profile.cwd, allow_root=True)
        if not cwd.is_dir():
            raise RepoPilotError("INVALID_CWD", "Test working directory must exist.")
        key = uuid.uuid4().hex
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        xml = self.artifact_dir / f"{key}.xml"
        log = self.artifact_dir / f"{key}.log"
        argv = [str(executable), "-m", "pytest", *profile.args, f"--junitxml={xml}"]
        intent = {
            "kind": "test_run",
            "profile": name,
            "argv": argv,
            "cwd": str(cwd),
            "report_artifact": str(xml),
        }

        async def commit() -> ToolOutput:
            process = await run_process(argv, cwd, profile.timeout)
            log.write_text(process.output, encoding="utf-8")
            counts = (
                junit_counts([xml])
                if xml.is_file()
                else {
                    "tests": 0,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                }
            )
            executed = counts["tests"] - counts["skipped"]
            verification = (
                "passed"
                if process.returncode == 0
                and executed > 0
                and not counts["failed"]
                and not counts["errors"]
                else "not_run"
                if process.returncode in (0, 5) and executed == 0
                else "failed"
            )
            output = process.output.encode()
            return ToolOutput(
                {
                    "profile": name,
                    "verification": verification,
                    "returncode": process.returncode,
                    "counts": counts,
                    "duration": round(process.duration, 3),
                    "log_artifact": log.name,
                    "output": output[: self.output_bytes].decode("utf-8", errors="ignore"),
                },
                truncated=process.truncated or len(output) > self.output_bytes,
            )

        preview = (
            f"Execute repository tests (repository code runs with your OS permissions):\n"
            f"cwd: {cwd}\nargv: {argv}\ntimeout: {profile.timeout}s"
        )
        return PreparedAction("execute", preview, intent, commit, profile.timeout + 5)

    def register(self, registry: ToolRegistry) -> None:
        async def no_bypass(args: JsonObject) -> ToolOutput:
            raise RepoPilotError("UNSAFE_TOOL", "Test execution requires approval.")

        registry.register(
            ToolDefinition(
                "run_tests",
                "Run a named host-configured test profile. "
                f"Available profiles: {', '.join(self.profiles) or '(none)'}. "
                "No arbitrary commands or extra arguments.",
                TestArgs,
                no_bypass,
                "execute",
                self.prepare,
            )
        )
