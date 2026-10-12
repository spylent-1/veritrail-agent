"""Only read trusted host configuration; never auto-load project configuration."""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(StrictModel):
    model_turns: int = Field(default=20, ge=1, le=100)
    tool_calls: int = Field(default=40, ge=1, le=200)
    model_timeout: float = Field(default=60, gt=0)
    tool_timeout: float = Field(default=10, gt=0)
    active_seconds: float = Field(default=900, gt=0)
    context_bytes: int = Field(default=65536, ge=1024)
    output_tokens: int = Field(default=4096, ge=1)
    output_bytes: int = Field(default=16384, ge=512)


class ModelSettings(StrictModel):
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"


class Settings(StrictModel):
    model: ModelSettings = Field(default_factory=ModelSettings)
    limits: Limits = Field(default_factory=Limits)


def state_home() -> Path:
    return Path(os.environ.get("REPOPILOT_HOME", str(Path.home() / ".repopilot"))).resolve()


def load_settings(home: Path | None = None) -> Settings:
    path = (home or state_home()) / "config.toml"
    if not path.exists():
        return Settings()
    with path.open("rb") as source:
        return Settings.model_validate(tomllib.load(source))


def api_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY") or None
