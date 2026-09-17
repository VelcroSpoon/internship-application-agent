"""VOICE tripwire. The banned list is config; this applies it in code after
the model writes, because a model told "avoid X" still says X sometimes.
Hits are labels for the record. Nothing here edits the draft."""

from __future__ import annotations

from internship_agent.config import VoiceConfig


def find_voice_hits(text: str, voice: VoiceConfig) -> list[str]:
    """One label per rule that fires, in config order. Phrases first, then patterns."""
    hits: list[str] = []
    lowered = text.lower()
    for phrase in voice.banned_phrases:
        if phrase.lower() in lowered:
            hits.append(f"phrase: {phrase}")
    for pattern in voice.compiled_patterns():
        m = pattern.search(text)
        if m:
            hits.append(f"pattern: {m.group(0)}")
    return hits
