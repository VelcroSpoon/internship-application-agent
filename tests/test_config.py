"""Config is a TOML file the human edits by hand, so typos must fail loudly."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from internship_agent.config import DEFAULT_CONFIG_PATH, build_sources, load_config
from internship_agent.scout.greenhouse import GreenhouseSource


def test_load_config_builds_typed_settings(tmp_path: Path):
    p = tmp_path / "sources.toml"
    p.write_text(
        '[scout]\nuser_agent = "X/1 (+mailto:a@b)"\ndelay_seconds = 1.5\n'
        '[[scout.greenhouse]]\nboard = "scaleai"\ncompany = "Scale AI"\n'
        '[[scout.greenhouse]]\nboard = "duolingo"\n',
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.scout.user_agent == "X/1 (+mailto:a@b)"
    assert cfg.scout.delay_seconds == 1.5
    assert [b.board for b in cfg.scout.greenhouse] == ["scaleai", "duolingo"]
    assert cfg.scout.greenhouse[1].company is None


def test_build_sources_yields_one_greenhouse_source_per_board(tmp_path: Path):
    p = tmp_path / "sources.toml"
    p.write_text(
        '[scout]\nuser_agent = "X/1"\n'
        '[[scout.greenhouse]]\nboard = "scaleai"\ncompany = "Scale AI"\n',
        encoding="utf-8",
    )

    sources = build_sources(load_config(p).scout)

    assert len(sources) == 1
    src = sources[0]
    assert isinstance(src, GreenhouseSource)
    assert src.name == "greenhouse:scaleai"
    assert src.company == "Scale AI"
    assert src.user_agent == "X/1"


def test_unknown_keys_are_rejected(tmp_path: Path):
    p = tmp_path / "sources.toml"
    p.write_text('[scout]\nuser_agent = "X/1"\ndelay_secs = 3\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(p)


def test_delay_below_one_second_is_rejected(tmp_path: Path):
    """Conservative crawling is a hard requirement; the config cannot turn it off."""
    p = tmp_path / "sources.toml"
    p.write_text('[scout]\nuser_agent = "X/1"\ndelay_seconds = 0.1\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(p)


def test_shipped_config_loads_and_names_a_contact_in_user_agent():
    cfg = load_config(DEFAULT_CONFIG_PATH)

    assert cfg.scout.greenhouse, "shipped config must list at least one board"
    assert "+" in cfg.scout.user_agent, "User-Agent should carry a contact URL or mailto"


# --- criteria.toml ----------------------------------------------------------


def test_shipped_criteria_loads_and_resume_exists():
    from internship_agent.config import DEFAULT_CRITERIA_PATH, load_criteria, resolve_resume_path

    cf = load_criteria(DEFAULT_CRITERIA_PATH)

    assert cf.criteria.target_cycle == "Summer 2027"
    assert 0 <= cf.criteria.queue_threshold <= 100
    assert cf.screener.backend == "ollama"
    assert resolve_resume_path(cf).is_file()


def test_criteria_prefilter_patterns_must_compile(tmp_path: Path):
    from internship_agent.config import load_criteria

    p = tmp_path / "criteria.toml"
    p.write_text(
        '[candidate]\nmaster_resume_path = "r.md"\n'
        '[criteria]\ntarget_cycle = "S"\n'
        '[prefilter]\ntitle_patterns = ["(unclosed"]\n'
        '[screener]\nmodel = "m"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_criteria(p)


def test_criteria_screener_backend_is_restricted_to_known_values(tmp_path: Path):
    from internship_agent.config import load_criteria

    p = tmp_path / "criteria.toml"
    p.write_text(
        '[candidate]\nmaster_resume_path = "r.md"\n'
        '[criteria]\ntarget_cycle = "S"\n'
        '[screener]\nmodel = "m"\nbackend = "openai"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_criteria(p)


def test_hard_disqualifier_patterns_must_compile(tmp_path: Path):
    from internship_agent.config import load_criteria

    p = tmp_path / "criteria.toml"
    p.write_text(
        '[candidate]\nmaster_resume_path = "r.md"\n'
        '[criteria]\ntarget_cycle = "S"\n'
        '[[criteria.hard_disqualifiers]]\nlabel = "clearance"\npattern = "(unclosed"\n'
        '[screener]\nmodel = "m"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_criteria(p)
