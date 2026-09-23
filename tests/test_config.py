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
    # Which backend each agent uses is a running choice, not a contract. The
    # test pins that the shipped file loads and names a known one, so swapping
    # Ollama for Anthropic does not fail the suite.
    assert cf.screener.backend in {"ollama", "anthropic"}
    assert cf.writer.backend in {"ollama", "anthropic"}
    assert cf.critic.backend in {"ollama", "anthropic"}
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


def test_criteria_writer_section_and_anthropic_backend_load(tmp_path: Path):
    from internship_agent.config import load_criteria

    p = tmp_path / "criteria.toml"
    p.write_text(
        '[candidate]\nmaster_resume_path = "r.md"\n'
        '[criteria]\ntarget_cycle = "S"\n'
        '[screener]\nmodel = "claude-haiku-4-5"\nbackend = "anthropic"\n'
        '[writer]\nmodel = "claude-opus-5"\neffort = "medium"\n',
        encoding="utf-8",
    )

    cf = load_criteria(p)

    assert cf.screener.backend == "anthropic"
    assert cf.writer.backend == "anthropic" and cf.writer.model == "claude-opus-5"
    assert cf.writer.effort == "medium"


def test_shipped_criteria_has_a_writer_section():
    from internship_agent.config import DEFAULT_CRITERIA_PATH, load_criteria

    cf = load_criteria(DEFAULT_CRITERIA_PATH)
    assert cf.writer.model


# --- the shipped title pre-filter, against real titles -----------------------
# Collected from the eleven configured boards on 2026-09-22. Each MISSED title
# was being dropped before any model saw it.


SHOULD_PASS = [
    "Software Engineering Intern (Summer 2027)",
    "Applied Science Intern",
    # Lyft posts its Montreal internships in French. Stagiaire = intern.
    "Développeur Logiciels (Stagiaire), Backend (l'été 2027 - Montreal)",
    "Développeur Logiciels (Stagiaire), Automatisation des tests (l'été 2027)",
    "Software Engineer, Early Career — Immediate Start",
    "Anthropic Fellows Program, ML Systems & Reinforcement Learning",
    "2027 Software Engineering Internships",  # plural used to slip through
    "Machine Learning Interns",
    "Software Engineer - New Grad",
]

SHOULD_NOT_PASS = [
    "Head of International Security",
    "Internal Audit - Treasury",
    "Administrative Business Partner, Office of the President",
    "University Recruiting Manager",
    # Scale's contractor expert network, not an internship or fellowship program.
    "SWE Fellow - Human Frontier Collective (Canada)",
    "Senior Software Engineer - Database Engine Internals",
    "Staff Software Engineer, Platform",
]


def _prefilter():
    from internship_agent.config import DEFAULT_CRITERIA_PATH, load_criteria

    return load_criteria(DEFAULT_CRITERIA_PATH).prefilter.compiled()


@pytest.mark.parametrize("title", SHOULD_PASS)
def test_shipped_prefilter_passes_real_early_career_titles(title):
    assert any(p.search(title) for p in _prefilter()), title


@pytest.mark.parametrize("title", SHOULD_NOT_PASS)
def test_shipped_prefilter_rejects_real_senior_and_noise_titles(title):
    assert not any(p.search(title) for p in _prefilter()), title
