import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from repopilot.cli import app
from repopilot.config import load_settings


def test_doctor_does_not_expose_key(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOPILOT_HOME", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret-not-for-output")
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    assert "fixture-secret" not in result.output
    assert json.loads(result.output)["api_key_configured"] is True


def test_configuration_is_host_only(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("DEEPSEEK_API_KEY=untrusted")
    (repo / "config.toml").write_text('[model]\nmodel="untrusted"')
    monkeypatch.chdir(repo)
    assert load_settings(host).model.model == "deepseek-flash"


def test_unknown_config_is_rejected(tmp_path):
    (tmp_path / "config.toml").write_text("allow_everything = true")
    with pytest.raises(ValidationError):
        load_settings(tmp_path)


def test_missing_prerequisites_are_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOPILOT_HOME", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("repopilot.cli.shutil.which", lambda name: None)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Set DEEPSEEK_API_KEY" in result.output
    assert "Install ripgrep" in result.output


def test_help_has_doctor_command():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output
