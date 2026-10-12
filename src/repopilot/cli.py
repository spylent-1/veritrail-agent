"""Thin CLI boundary. Agent modules do not import Typer or Rich."""

import json
import shutil
import sys

import typer
from rich.console import Console

from repopilot import __version__
from repopilot.config import api_key, load_settings

app = typer.Typer(no_args_is_help=True, help="RepoPilot: inspect, edit and verify trusted code.")
console = Console(markup=False, highlight=False)


@app.callback()
def main() -> None:
    """Inspect, edit and verify trusted local code with explicit approvals."""


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


if __name__ == "__main__":
    app()
