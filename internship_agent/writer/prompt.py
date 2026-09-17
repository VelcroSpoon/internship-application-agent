"""Writer prompts.

Split for caching: everything stable across postings (rules, VOICE list,
master resume) goes in the system prompt behind a cache breakpoint. The
posting goes in the user turn. For one application the Critic loop will
re-send the same system prompt every round, so this split is what makes
round 2 and 3 cheap.
"""

from __future__ import annotations

import sqlite3

from internship_agent.config import VoiceConfig


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
