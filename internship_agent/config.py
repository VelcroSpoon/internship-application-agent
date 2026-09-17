"""Settings. Everything the human edits lives in TOML under ``config/``;
paths and secrets come from the environment.

TOML rather than YAML because ``tomllib`` is in the standard library from
3.11, and the config surface is small enough that YAML's extra syntax buys
nothing. ``extra="forbid"`` on every model so a misspelled key fails at load
instead of silently disabling something.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from internship_agent.scout.base import Source
from internship_agent.scout.greenhouse import GreenhouseSource

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("INTERNSHIP_AGENT_CONFIG", REPO_ROOT / "config" / "sources.toml")
)
DEFAULT_DB_PATH = Path(os.environ.get("INTERNSHIP_AGENT_DB", REPO_ROOT / "data" / "agent.db"))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GreenhouseBoard(_Strict):
    board: str = Field(min_length=1)
    company: str | None = None


class ScoutConfig(_Strict):
    user_agent: str = Field(min_length=1)
    # Floor of 1s: the crawl-politeness requirement is not something config can disable.
    delay_seconds: float = Field(default=3.0, ge=1.0)
    greenhouse: list[GreenhouseBoard] = Field(default_factory=list)


class AppConfig(_Strict):
    scout: ScoutConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    with open(path, "rb") as f:
        return AppConfig.model_validate(tomllib.load(f))


def build_sources(scout: ScoutConfig) -> list[Source]:
    return [
        GreenhouseSource(board=b.board, company=b.company, user_agent=scout.user_agent)
        for b in scout.greenhouse
    ]
