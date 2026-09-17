"""Settings. Everything the human edits lives in TOML under ``config/``;
paths and secrets come from the environment.

TOML rather than YAML because ``tomllib`` is in the standard library from
3.11, and the config surface is small enough that YAML's extra syntax buys
nothing. ``extra="forbid"`` on every model so a misspelled key fails at load
instead of silently disabling something.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from internship_agent.llm.anthropic_backend import AnthropicBackend
from internship_agent.llm.base import StructuredLLM
from internship_agent.llm.ollama import OllamaBackend
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


class SchedulerConfig(_Strict):
    """When the nightly discovery run fires. Discovery only: the scheduler
    never drafts and never submits."""

    enabled: bool = True
    hour: int = Field(default=3, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    timezone: str = "America/Toronto"


class AppConfig(_Strict):
    scout: ScoutConfig
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    with open(path, "rb") as f:
        return AppConfig.model_validate(tomllib.load(f))


def build_sources(scout: ScoutConfig) -> list[Source]:
    return [
        GreenhouseSource(board=b.board, company=b.company, user_agent=scout.user_agent)
        for b in scout.greenhouse
    ]


# --- criteria.toml: what the Screener looks for -----------------------------------

DEFAULT_CRITERIA_PATH = Path(
    os.environ.get("INTERNSHIP_AGENT_CRITERIA", REPO_ROOT / "config" / "criteria.toml")
)


class CandidateConfig(_Strict):
    master_resume_path: Path


def _compile_or_raise(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"bad regex {pattern!r}: {exc}") from exc


class Disqualifier(_Strict):
    """A hard no, detected by regex over the posting text, in code.

    Deliberately not given to the model: a 3B model handed a checklist
    echoes it back as findings instead of reading the posting.
    """

    label: str = Field(min_length=1)
    pattern: str = Field(min_length=1)

    @field_validator("pattern")
    @classmethod
    def _must_compile(cls, pattern: str) -> str:
        _compile_or_raise(pattern)
        return pattern

    def compiled(self) -> re.Pattern[str]:
        return _compile_or_raise(self.pattern)


DISQUALIFIED_SCORE_CAP = 30


class CriteriaConfig(_Strict):
    target_cycle: str = Field(min_length=1)
    target_roles: list[str] = Field(default_factory=list)
    acceptable_locations: list[str] = Field(default_factory=list)
    hard_disqualifiers: list[Disqualifier] = Field(default_factory=list)
    queue_threshold: int = Field(default=70, ge=0, le=100)


class PrefilterConfig(_Strict):
    title_patterns: list[str] = Field(default_factory=list)

    @field_validator("title_patterns")
    @classmethod
    def _must_compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            _compile_or_raise(p)
        return patterns

    def compiled(self) -> list[re.Pattern[str]]:
        return [_compile_or_raise(p) for p in self.title_patterns]


Backend = Literal["ollama", "anthropic"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class BackendConfig(_Strict):
    """Which model answers, and how. Shared by every agent so any of them can be
    pointed at the local or the hosted backend with a one-line config edit."""

    backend: Backend = "ollama"
    model: str = Field(min_length=1)
    ollama_host: str = "http://localhost:11434"
    effort: Effort = "medium"  # Anthropic only; Ollama ignores it
    max_attempts: int = Field(default=2, ge=1, le=5)


class ScreenerConfig(BackendConfig):
    description_max_chars: int = Field(default=6000, ge=500)


class WriterConfig(BackendConfig):
    backend: Backend = "anthropic"
    model: str = Field(default="claude-opus-5", min_length=1)


class CriticConfig(BackendConfig):
    # Sonnet: the Critic runs once per round against a fixed rubric, which is
    # judgement against anchors rather than open-ended generation. Cheaper per
    # round keeps a 3-round loop affordable.
    backend: Backend = "anthropic"
    model: str = Field(default="claude-sonnet-5", min_length=1)


class CriteriaFile(_Strict):
    candidate: CandidateConfig
    criteria: CriteriaConfig
    prefilter: PrefilterConfig = Field(default_factory=PrefilterConfig)
    screener: ScreenerConfig
    writer: WriterConfig = Field(default_factory=WriterConfig)
    critic: CriticConfig = Field(default_factory=CriticConfig)


def load_criteria(path: Path = DEFAULT_CRITERIA_PATH) -> CriteriaFile:
    with open(path, "rb") as f:
        return CriteriaFile.model_validate(tomllib.load(f))


def resolve_resume_path(cf: CriteriaFile) -> Path:
    p = cf.candidate.master_resume_path
    return p if p.is_absolute() else REPO_ROOT / p


def build_backend(cfg: BackendConfig) -> StructuredLLM:
    if cfg.backend == "ollama":
        return OllamaBackend(model=cfg.model, host=cfg.ollama_host)
    if cfg.backend == "anthropic":
        return AnthropicBackend(model=cfg.model, effort=cfg.effort)
    raise ValueError(f"unknown backend {cfg.backend!r}")  # unreachable via Literal


# --- voice.toml: the VOICE anti-pattern list ---------------------------------------

DEFAULT_VOICE_PATH = Path(
    os.environ.get("INTERNSHIP_AGENT_VOICE", REPO_ROOT / "config" / "voice.toml")
)


class VoiceConfig(_Strict):
    banned_phrases: list[str] = Field(default_factory=list)
    banned_patterns: list[str] = Field(default_factory=list)
    style_notes: list[str] = Field(default_factory=list)

    @field_validator("banned_patterns")
    @classmethod
    def _must_compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            _compile_or_raise(p)
        return patterns

    def compiled_patterns(self) -> list[re.Pattern[str]]:
        # MULTILINE so ^/$ anchor per line: salutation rules target the first line.
        return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in self.banned_patterns]


class _VoiceFile(_Strict):
    voice: VoiceConfig


def load_voice(path: Path = DEFAULT_VOICE_PATH) -> VoiceConfig:
    with open(path, "rb") as f:
        return _VoiceFile.model_validate(tomllib.load(f)).voice
