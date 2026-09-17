"""Critic prompts.

The rubric is imported verbatim from the spec file; it is not restated or
paraphrased here. System holds the rubric and the master resume, both
stable across every application, so the cache prefix is reused on every
call the project ever makes. The posting and the draft go in the user turn.
"""

from __future__ import annotations

import sqlite3

from internship_agent.agents.critic import RUBRIC
from internship_agent.writer.models import Draft


def build_system(resume_text: str) -> str:
    return f"""{RUBRIC.strip()}

# Master resume (the sole source of truth about the candidate)

{resume_text.strip()}"""


def build_user(posting: sqlite3.Row, draft: Draft, round_index: int) -> str:
    anchors = "\n".join(
        f"- bullet {i + 1} claims support from: {b.resume_anchor}"
        for i, b in enumerate(draft.bullets)
    )
    return f"""# Posting
Company: {posting["company"]}
Title: {posting["title"]}
Location: {posting["location"] or "(not stated)"}

{(posting["description"] or "").strip()}

# Draft (revision round {round_index})

{draft.as_text()}

# Anchors the writer claimed

{anchors}

Score this draft. Set round_index to {round_index}."""


def build_revision_note(round_index: int) -> str:
    return f"This is revision round {round_index}."
