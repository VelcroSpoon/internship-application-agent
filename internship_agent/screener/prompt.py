"""Screener prompts.

Written for a 3B model: short, concrete, one job. The criteria are rendered
into the system prompt once per run; the posting and resume go in the user
turn. The output shape is enforced by the backend's schema-constrained
decoding, so the prompt describes *judgement*, not JSON formatting.
"""

from __future__ import annotations

import sqlite3

from internship_agent.config import CriteriaConfig

TRUNCATION_MARKER = "\n[... description truncated ...]"


def build_system(criteria: CriteriaConfig) -> str:
    roles = "\n".join(f"- {r}" for r in criteria.target_roles) or "- (any)"
    locations = "\n".join(f"- {loc}" for loc in criteria.acceptable_locations) or "- (any)"
    disq = "\n".join(f"- {d}" for d in criteria.hard_disqualifiers) or "- (none)"
    return f"""You screen job postings for one specific candidate. You are strict and literal.
You read the posting and the candidate's resume, then judge fit. You never invent
skills the resume does not show.

The candidate wants, for the {criteria.target_cycle} cycle:
{roles}

Locations the candidate can work from (remote roles are fine anywhere):
{locations}

Hard disqualifiers. If the posting requires any of these, list it in
`disqualifiers` and cap fit_score at 30:
{disq}

fit_score scale:
  90-100  internship for the target cycle, role type matches, resume covers nearly
          every stated requirement
  70-89   internship, role type matches, resume covers the core requirements; a few gaps
  40-69   internship but role type or seniority is off, or major requirement gaps
  0-39    not an internship, wrong field, location impossible, or a hard disqualifier

is_internship is true only for internship or co-op roles. Full-time, senior,
staff, manager, fellow, and contractor roles are false and score 0-39 no matter
how well the skills match.

reason: one sentence, under 30 words, stating the single most decisive factor.
matched_requirements / missing_requirements: short phrases copied from the
posting's requirements, at most 5 each."""


def build_user(posting: sqlite3.Row, resume_text: str, max_chars: int) -> str:
    description = posting["description"] or ""
    if len(description) > max_chars:
        description = description[:max_chars].rstrip() + TRUNCATION_MARKER
    return f"""# Posting
Company: {posting["company"]}
Title: {posting["title"]}
Location: {posting["location"] or "(not stated)"}

{description}

# Candidate resume (sole source of truth about the candidate)
{resume_text.strip()}

Screen this posting for this candidate."""


def repair_suffix(error: str, raw_text: str) -> str:
    """Appended to the user prompt on retry after a schema failure."""
    return (
        "\n\n# Your previous answer was rejected\n"
        f"Validation error: {error}\n"
        f"Previous output: {raw_text[:500]}\n"
        "Answer again. Respect every field constraint."
    )
