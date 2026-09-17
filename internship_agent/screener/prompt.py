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
    # Hard disqualifiers are intentionally absent: they are regexes applied in
    # code. Shown a checklist, a small model copies it into its findings.
    roles = "\n".join(f"- {r}" for r in criteria.target_roles) or "- (any)"
    locations = "\n".join(f"- {loc}" for loc in criteria.acceptable_locations) or "- (any)"
    return f"""You screen job postings for one specific candidate. You are strict and literal.
You compare the posting's stated requirements against the candidate's resume.
The resume is the only source of truth about the candidate.

The candidate wants, for the {criteria.target_cycle} cycle:
{roles}

Locations the candidate can work from (remote roles are fine anywhere):
{locations}

How to fill the fields:

matched_requirements: requirements from the posting that a specific line of the
resume clearly supports. If you cannot point to the resume line, it is not
matched. Copy the requirement's wording from the posting, shortened.

missing_requirements: requirements from the posting that the resume does not
show. Copy the wording from the posting, shortened. Only list things the posting
actually asks for.

is_internship: true only for internship or co-op roles. Full-time, new grad,
senior, staff, manager, fellow, and contractor roles are false.

fit_score:
  90-100  internship for the target cycle, role type matches, nearly every
          requirement is matched
  70-89   internship, role type matches, the core requirements are matched, a
          few gaps
  40-69   internship but role type or seniority is off, or several core
          requirements are missing
  0-39    not an internship, wrong field, or location the candidate cannot work from

reason: one sentence, under 30 words, naming the single most decisive
requirement or gap by its wording in the posting."""


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
