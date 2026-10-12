"""Thin CLI boundary. Agent modules do not import Typer or Rich."""

import asyncio
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from repopilot import __version__
from repopilot.application import rollback_run, run_task
from repopilot.config import Settings, api_key, load_settings, state_home
from repopilot.deepseek import DeepSeekProvider
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, RunReport
from repopilot.store import SessionStore
from repopilot.ui import TerminalApproval

app = typer.Typer(no_args_is_help=True, help="RepoPilot: inspect, edit and verify trusted code.")
console = Console(markup=False, highlight=False)


def checked_settings() -> Settings:
    try:
        return load_settings()
    except (ValueError, OSError):
        raise RepoPilotError(
            "CONFIG_ERROR", "Invalid host configuration; check config.toml."
        ) from None


@app.callback()
def main() -> None:
    """Inspect, edit and verify trusted local code with explicit approvals."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")) -> None:
    """Report local prerequisites without exposing credentials or calling a model."""
    try:
        settings = load_settings()
    except (ValueError, OSError) as exc:
        console.print(f"Invalid host configuration ({type(exc).__name__}).")
        raise typer.Exit(2) from exc
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "git": shutil.which("git") is not None,
        "rg": shutil.which("rg") is not None,
        "java": shutil.which("java") is not None,
        "maven": shutil.which("mvn") is not None,
        "api_key_configured": bool(api_key()),
        "model": settings.model.model,
        "live_model_verified": False,
    }
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False))
    else:
        for name, value in report.items():
            console.print(f"{name}: {value}")
        if not report["api_key_configured"]:
            console.print("Set DEEPSEEK_API_KEY for live runs. Offline tests do not need a key.")
        if not report["rg"]:
            console.print("Install ripgrep and add rg to PATH before using workspace tools.")


@app.command()
def run(
    task: str,
    workspace: Annotated[Path, typer.Option("--workspace")] = Path("."),
    mode: str = typer.Option("read", "--mode"),
    json_output: bool = typer.Option(False, "--json"),
    mcp: Annotated[list[str] | None, typer.Option("--mcp")] = None,
) -> None:
    """Ask the model to inspect code using constrained workspace tools."""
    if mode not in ("read", "edit"):
        console.print("Choose --mode read or edit.")
        raise typer.Exit(2)

    def show_event(kind: str, data: JsonObject) -> None:
        if not json_output and kind in ("model_started", "tool_started"):
            console.print(f"{kind}: {data.get('name', data.get('turn', ''))}")

    async def execute() -> RunReport:
        settings = checked_settings()
        key = api_key()
        if not key:
            raise RepoPilotError("KEY_MISSING", "Set DEEPSEEK_API_KEY in the host environment.")
        provider = DeepSeekProvider(settings.model, key)
        try:
            return await run_task(
                task,
                workspace,
                state_home(),
                settings,
                provider,
                show_event,
                mode=mode,
                approval=TerminalApproval(console),
                mcp_names=mcp,
            )
        finally:
            await provider.close()

    try:
        report = asyncio.run(execute())
    except RepoPilotError as exc:
        console.print(f"{exc.code}: {exc}")
        raise typer.Exit(2) from None
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        console.print(report.answer)
        console.print(f"Run: {report.status}; verification: {report.verification}")
        for warning in report.warnings:
            console.print(warning)
    if report.status != "completed":
        raise typer.Exit(1)


@app.command()
def sessions() -> None:
    """List locally saved sessions."""
    store = SessionStore(state_home())
    try:
        for session in store.sessions():
            console.print(f"{session['id']}  {session['workspace']}  {session['title']}")
    finally:
        store.close()


@app.command()
def resume(session_id: str, task: str | None = typer.Option(None, "--task")) -> None:
    """Inspect saved state, then continue with new input; never automatically replay."""
    store = SessionStore(state_home())
    try:
        session = store.session(session_id)
        runs = store.runs(session_id)
        console.print(f"Session: {session_id}; workspace: {session['workspace']}")
        console.print(f"Last run: {runs[-1]['status'] if runs else 'none'}; approvals expired.")
    except RepoPilotError as exc:
        console.print(f"{exc.code}: {exc}")
        raise typer.Exit(2) from None
    finally:
        store.close()
    if task is None:
        if not sys.stdin.isatty():
            console.print("Use --task to continue noninteractively; side effects remain denied.")
            return
        task = typer.prompt("Next task")

    async def execute() -> RunReport:
        settings = checked_settings()
        key = api_key()
        if not key:
            raise RepoPilotError("KEY_MISSING", "Set DEEPSEEK_API_KEY.")
        provider = DeepSeekProvider(settings.model, key)
        try:
            return await run_task(
                task or "",
                Path(session["workspace"]),
                state_home(),
                settings,
                provider,
                mode=runs[-1]["mode"] if runs else "read",
                session_id=session_id,
                approval=TerminalApproval(console),
            )
        finally:
            await provider.close()

    try:
        report = asyncio.run(execute())
        console.print(report.model_dump_json(indent=2))
    except RepoPilotError as exc:
        console.print(f"{exc.code}: {exc}")
        raise typer.Exit(2) from None


@app.command()
def rollback(run_id: str) -> None:
    """Review and restore only this run's confirmed file edits, with conflict checks."""
    try:
        report = asyncio.run(rollback_run(run_id, state_home(), TerminalApproval(console)))
        console.print(report.model_dump_json(indent=2))
        if report.status != "completed":
            raise typer.Exit(1)
    except RepoPilotError as exc:
        console.print(f"{exc.code}: {exc}")
        raise typer.Exit(2) from None


@app.command()
def trace(run_id: str, jsonl: bool = typer.Option(False, "--jsonl")) -> None:
    """Inspect a saved run; --jsonl exports ordered events to stdout."""
    store = SessionStore(state_home())
    try:
        if jsonl:
            for event in store.events(run_id):
                typer.echo(json.dumps(event, ensure_ascii=False))
        else:
            report = store.report(run_id)
            console.print(report.model_dump_json(indent=2) if report else "Run is incomplete.")
    except RepoPilotError as exc:
        console.print(f"{exc.code}: {exc}")
        raise typer.Exit(2) from None
    finally:
        store.close()


if __name__ == "__main__":
    app()
