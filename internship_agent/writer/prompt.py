"""Writer prompts.

Split for caching: everything stable across postings (rules, VOICE list,
master resume) goes in the system prompt behind a cache breakpoint. The
posting goes in the user turn. For one application the Critic loop will
re-send the same system prompt every round, so this split is what makes
round 2 and 3 cheap.
"""

from __future__ import annotations

import sqlite3

from internship_agent.agents.critic import Critique
from internship_agent.config import VoiceConfig
from internship_agent.writer.models import Draft


def build_system(resume_text: str, voice: VoiceConfig) -> str:
    phrases = "\n".join(f"- {p}" for p in voice.banned_phrases) or "- (none)"
    patterns = "\n".join(f"- {p}" for p in voice.banned_patterns) or "- (none)"
    notes = "\n".join(f"- {n}" for n in voice.style_notes) or "- (none)"
    return f"""You write internship application materials for one candidate, from one
source: the master resume below. You produce tailored resume bullets and a cover
letter for a specific posting.

Rules that override everything else:
1. Every factual claim must trace to a line in the master resume. Do not add a
   technology, a number, a role, a team size, a user count, or an outcome the
   resume does not state. If the posting asks for something the resume lacks, say
   nothing about it. Silence is correct; invention is disqualifying.
2. Do not inflate ownership. Solo coursework is not "led". A demo is not "in
   production". Use the resume's own scale.
3. Each bullet's resume_anchor is the resume line it was built from, quoted
   closely enough to find. One anchor per bullet.
4. Bullets: 3 to 6, strongest first, each one concrete: what was built, with
   what, and what happened, using only the resume's numbers.
5. Cover letter: 200 to 350 words, plain text, no subject line, no address
   block. Open with substance, not ceremony. Give one real reason for wanting
   this role that would not survive swapping in another company. Address the
   posting's stated requirements the resume can support, and only those. Close
   in one sentence.

VOICE. Never use these phrases:
{phrases}

Never produce these patterns (regular expressions over your output):
{patterns}

Style:
{notes}

# Master resume (the only source of truth about the candidate)

{resume_text.strip()}"""


def build_user(posting: sqlite3.Row) -> str:
    return f"""# Posting
Company: {posting["company"]}
Title: {posting["title"]}
Location: {posting["location"] or "(not stated)"}

{(posting["description"] or "").strip()}

Write the tailored bullets and the cover letter for this posting."""


def build_revision_user(posting: sqlite3.Row, previous: Draft, critique: Critique) -> str:
    """Revision turn: the previous draft plus the Critic's diagnoses.

    Findings are passed as diagnosis (what is wrong, where) and direction
    (what to do), never as replacement text, and the instruction says to act
    on them in the writer's own words. The Critic must not end up writing the
    draft through the Writer's hands.
    """
    findings = (
        "\n\n".join(
            f"{i + 1}. [{f.severity.value.upper()} / {f.dimension.value} / {f.section}]\n"
            f"   Quoted from your draft: {f.excerpt!r}\n"
            f"   Problem: {f.problem}\n"
            f"   Direction: {f.fix_direction}"
            + (f"\n   Relevant resume line: {f.resume_anchor}" if f.resume_anchor else "")
            for i, f in enumerate(critique.findings)
        )
        or "(no specific findings)"
    )
    missing = (
        "\n".join(f"- {m}" for m in critique.missing_requirements)
        if critique.missing_requirements
        else "(none)"
    )
    scores = ", ".join(f"{s.dimension.value} {s.score}/5" for s in critique.scores)

    return f"""# Posting
Company: {posting["company"]}
Title: {posting["title"]}
Location: {posting["location"] or "(not stated)"}

{(posting["description"] or "").strip()}

# Your previous draft

{previous.as_text()}

# Review of that draft

Scores: {scores}

Findings to address:

{findings}

Requirements the posting states, the resume supports, and the draft did not address:
{missing}

# What to do

Rewrite the bullets and the cover letter, addressing every finding above.

The findings are diagnoses, not copy. Do not paste any part of a Direction
into the draft; work out what to say yourself and say it in your own words.
A Direction that says to cut something means cut it, not describe cutting it.

Keep what was already working. A revision that rewrites a sound bullet to look
different is a worse draft. Every rule from your instructions still applies,
especially grounding: fixing a finding by inventing a new claim is a worse
outcome than leaving the finding unfixed."""
