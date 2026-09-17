"""VOICE tripwire: the banned list lives in config and is applied in code
after the model writes. Hits are reported, never rewritten."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from internship_agent.config import DEFAULT_VOICE_PATH, VoiceConfig, load_voice
from internship_agent.writer.voice import find_voice_hits


def voice(**overrides) -> VoiceConfig:
    base = {
        "banned_phrases": ["passionate about", "I am writing to express my interest"],
        "banned_patterns": [r"\bnot (just|only) \w+,? but( also)?\b", " — "],
        "style_notes": ["Plain sentences."],
    }
    return VoiceConfig(**{**base, **overrides})


def test_phrase_hits_are_case_insensitive_and_labelled():
    hits = find_voice_hits("I'm PASSIONATE ABOUT compilers.", voice())
    assert hits == ["phrase: passionate about"]


def test_pattern_hits_report_the_matched_text():
    hits = find_voice_hits("It is not just fast but also correct — really.", voice())
    assert hits == ["pattern: not just fast but also", "pattern:  — "]


def test_no_hits_on_clean_text():
    assert find_voice_hits("I built a MinHash deduper in pandas.", voice()) == []


def test_each_rule_reported_once_even_if_it_matches_twice():
    text = "passionate about X. Also passionate about Y."
    assert find_voice_hits(text, voice()) == ["phrase: passionate about"]


def test_bad_pattern_is_rejected_at_load(tmp_path: Path):
    p = tmp_path / "voice.toml"
    p.write_text('[voice]\nbanned_patterns = ["(oops"]\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_voice(p)


def test_shipped_voice_file_loads_and_has_content():
    v = load_voice(DEFAULT_VOICE_PATH)
    assert len(v.banned_phrases) >= 10
    assert v.banned_patterns and v.style_notes
