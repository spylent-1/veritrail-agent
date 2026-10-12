"""Run 12 fixed cases sequentially. Approvals apply only to disposable fixture workspaces."""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from evals.grading import grade_edit, grade_location
from repopilot.application import run_task
from repopilot.config import Settings, TestProfile, api_key
from repopilot.deepseek import DeepSeekProvider
from repopilot.models import JsonObject
from repopilot.process import child_environment
from repopilot.safety import PreparedAction
from repopilot.store import SessionStore
from repopilot.workspace import digest

REPO = Path(__file__).resolve().parents[1]


def load_cases() -> list[JsonObject]:
    return json.loads((REPO / "evals/cases.json").read_text(encoding="utf-8"))


def populate(root: Path, case: JsonObject) -> None:
    source = (
        REPO
        / "tests/fixtures"
        / ("java-project" if case["language"] == "java" else "python-project")
    )
    shutil.copytree(source, root, ignore=shutil.ignore_patterns("target", "__pycache__"))
    if case["language"] == "java":
        for file in (REPO / "tests/fixtures/java-read").glob("*.java"):
            shutil.copy2(file, root / "src/main/java/demo" / file.name)
    if case["kind"] == "edit":
        file = root / case["file"]
        text = file.read_text(encoding="utf-8")
        if text.count(case["good"]) != 1:
            raise ValueError("Mutation must uniquely match the fixed fixture")
        file.write_text(text.replace(case["good"], case["bad"]), encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("target/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    environment = child_environment()
    environment.update(
        GIT_AUTHOR_DATE="2026-10-12T00:00:00+00:00", GIT_COMMITTER_DATE="2026-10-12T00:00:00+00:00"
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=RepoPilot Evaluation",
            "-c",
            "user.email=eval@repopilot.invalid",
            "commit",
            "-q",
            "-m",
            "Fixed evaluation fixture",
        ],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
    )


class FixtureApproval:
    """Not installed in the CLI; strictly scoped to this fixed benchmark's source file."""

    def __init__(self, root: Path, allowed: str) -> None:
        self.root = root.resolve()
        self.allowed = allowed
        self.decisions: list[JsonObject] = []

    async def confirm(self, action: PreparedAction) -> bool:
        intent = action.intent
        granted = (intent.get("kind") == "file_write" and intent.get("path") == self.allowed) or (
            intent.get("kind") == "test_run"
            and intent.get("profile") == "unit"
            and Path(intent["cwd"]).resolve() == self.root
        )
        self.decisions.append(
            {"kind": intent.get("kind"), "granted": granted, "fingerprint": action.fingerprint()}
        )
        return granted


def summarize(records: list[JsonObject], repeats: int, total_cases: int) -> JsonObject:
    groups = {kind: [r for r in records if r["kind"] == kind] for kind in ("locate", "edit")}
    counts = {
        kind: {"passed": sum(r["passed"] for r in rows), "total": len(rows)}
        for kind, rows in groups.items()
    }
    full = repeats == 3 and total_cases == 12 and len(records) == 36
    stable = [
        name
        for name in ("EJ1", "EP1")
        if len([r for r in records if r["case"] == name]) == 3
        and all(r["passed"] for r in records if r["case"] == name)
    ]
    gate = (
        (
            "passed"
            if counts["locate"]["passed"] >= 15
            and counts["edit"]["passed"] >= 12
            and len(stable) == 2
            else "failed"
        )
        if full
        else "partial"
    )
    return {
        "gate": gate,
        "counts": counts,
        "stable_examples": stable,
        "input_tokens": sum(r["usage"]["input_tokens"] for r in records),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in records),
    }


async def evaluate(args: argparse.Namespace) -> int:
    selected = [case for case in load_cases() if not args.case or case["id"] in args.case]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records: list[JsonObject] = []
    starting_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()
    metadata = {
        "source_commit": starting_commit,
        "uv_lock_sha256": digest((REPO / "uv.lock").read_bytes()),
        "model": "deepseek-flash",
        "thinking": "disabled",
        "repeats": args.repeats,
        "dataset_sha256": digest((REPO / "evals/cases.json").read_bytes()),
    }
    if not args.check_graders and not api_key():
        (output / "summary.json").write_text(
            json.dumps(
                {**metadata, "live_status": "not_run", "reason": "DEEPSEEK_API_KEY missing"},
                indent=2,
            ),
            encoding="utf-8",
        )
        return 2
    for case in selected:
        if args.check_graders and case["kind"] != "edit":
            continue
        for attempt in range(1, (1 if args.check_graders else args.repeats) + 1):
            directory = output / f"{case['id']}-{attempt}-{time.time_ns()}"
            root = directory / "workspace"
            populate(root, case)
            if args.check_graders:
                broken = await grade_edit(case["language"], root, directory / "grader-broken")
                file = root / case["file"]
                file.write_text(
                    file.read_text().replace(case["bad"], case["good"]), encoding="utf-8"
                )
                fixed = await grade_edit(case["language"], root, directory / "grader-fixed")
                assert not broken["passed"] and fixed["passed"], case["id"]
                print(f"grader {case['id']}: rejects mutation, accepts fixed fixture", flush=True)
                continue
            executable = shutil.which("mvn") if case["language"] == "java" else sys.executable
            if not executable:
                raise RuntimeError("Maven not installed")
            settings = Settings(
                tests={
                    "unit": TestProfile(
                        kind="maven" if case["language"] == "java" else "pytest",
                        executable=executable,
                        args=["-q"],
                    )
                }
            )
            provider = DeepSeekProvider(settings.model, api_key() or "")
            approval = FixtureApproval(root, case["file"])
            started = time.monotonic()
            print(f"start {case['id']} attempt {attempt}", flush=True)
            try:
                report = await run_task(
                    case["prompt"],
                    root,
                    directory / "state",
                    settings,
                    provider,
                    mode="edit" if case["kind"] == "edit" else "read",
                    approval=approval,
                )
            finally:
                await provider.close()
            grade = (
                await grade_edit(case["language"], root, directory / "grader")
                if case["kind"] == "edit"
                else {"passed": grade_location(case, report, root)}
            )
            passed = bool(grade["passed"] and report.status == "completed")
            if case["kind"] == "edit":
                passed &= report.verification == "passed" and case["file"] in report.changed_files
            (directory / "report.json").write_text(
                report.model_dump_json(indent=2), encoding="utf-8"
            )
            (directory / "grade.json").write_text(json.dumps(grade, indent=2), encoding="utf-8")
            store = SessionStore(directory / "state")
            try:
                events = store.events(report.run_id)
                (directory / "trace.jsonl").write_text(
                    "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                    encoding="utf-8",
                )
            finally:
                store.close()
            record = {
                "case": case["id"],
                "kind": case["kind"],
                "language": case["language"],
                "attempt": attempt,
                "passed": passed,
                "status": report.status,
                "stop_reason": report.stop_reason,
                "verification": report.verification,
                "model_turns": report.model_turns,
                "tool_calls": report.tool_calls,
                "duration": round(time.monotonic() - started, 3),
                "usage": report.usage.model_dump(),
                "usage_complete": report.usage_complete,
                "warnings": report.warnings,
                "approval_decisions": approval.decisions,
                "run_id": report.run_id,
                "fixture_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=root, text=True
                ).strip(),
                "artifacts": directory.name,
            }
            records.append(record)
            summary = {
                **metadata,
                **summarize(records, args.repeats, len(selected)),
                "records": records,
            }
            (output / "summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(
                f"finish {case['id']} attempt {attempt}: {passed}; "
                f"{report.model_turns} model turns / "
                f"{report.tool_calls} tools / {record['duration']}s",
                flush=True,
            )
    if args.check_graders:
        return 0
    return 0 if summarize(records, args.repeats, len(selected))["gate"] != "failed" else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--output", type=Path, default=REPO / ".eval-runs/baseline")
    parser.add_argument("--check-graders", action="store_true")
    raise SystemExit(asyncio.run(evaluate(parser.parse_args())))


if __name__ == "__main__":
    main()
