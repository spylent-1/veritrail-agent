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
from repopilot.agent import AgentRunner
from repopilot.config import api_key, load_settings, state_home
from repopilot.deepseek import DeepSeekProvider
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, RunReport
from repopilot.tools import ReadTools, ToolExecutor, ToolRegistry
from repopilot.workspace import Workspace

app = typer.Typer(no_args_is_help=True, help="RepoPilot: inspect, edit and verify trusted code.")
console = Console(markup=False, highlight=False)


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
) -> None:
    """Ask the model to inspect code using constrained workspace tools."""
    if mode != "read":
        console.print("This development milestone supports --mode read only.")
        raise typer.Exit(2)

    def show_event(kind: str, data: JsonObject) -> None:
        if not json_output and kind in ("model_started", "tool_started"):
            console.print(f"{kind}: {data.get('name', data.get('turn', ''))}")

    async def execute() -> RunReport:
        settings = load_settings()
        key = api_key()
        if not key:
            raise RepoPilotError("KEY_MISSING", "Set DEEPSEEK_API_KEY in the host environment.")
        root = Workspace(workspace, excluded_roots=(state_home(),))
        registry = ToolRegistry()
        ReadTools(root).register(registry)
        provider = DeepSeekProvider(settings.model, key)
        try:
            return await AgentRunner(
                provider, ToolExecutor(registry, settings.limits), settings.limits, show_event
            ).run(task)
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


if __name__ == "__main__":
    app()
