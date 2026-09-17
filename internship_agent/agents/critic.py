"""
Critic agent: scores a Writer draft against a fixed rubric and returns
structured, addressable findings. The Critic NEVER rewrites the draft —
it only diagnoses. Revision is the Writer's job.

Usage:
    critique = await run_critic(posting, master_resume, draft, round_index)
    if critique.verdict == Verdict.ACCEPT or round_index >= MAX_ROUNDS:
        stop()
    else:
        draft = await run_writer(posting, master_resume, draft, critique)
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class Dimension(str, Enum):
    GROUNDING = "grounding"
    COVERAGE = "coverage"
    SPECIFICITY = "specificity"
    DENSITY = "density"
    VOICE = "voice"


class Severity(str, Enum):
    BLOCKER = "blocker"  # factually unsupported; must be fixed
    MAJOR = "major"      # materially weakens the application
    MINOR = "minor"      # polish


class Verdict(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"


class DimensionScore(BaseModel):
    dimension: Dimension
    score: int = Field(ge=1, le=5, description="Anchored 1-5. See rubric.")
    reason: str = Field(
        max_length=300,
        description="One or two sentences. Cite the specific text that drove the score.",
    )


class Finding(BaseModel):
    dimension: Dimension
    severity: Severity
    section: Literal["bullets", "cover_letter", "both"]
    excerpt: str = Field(
        max_length=300,
        description="The exact span of the draft this finding is about, quoted verbatim.",
    )
    problem: str = Field(
        max_length=300,
        description="What is wrong with this span. State the defect, not a rewrite.",
    )
    fix_direction: str = Field(
        max_length=300,
        description=(
            "What the Writer should do about it, in imperative form. "
            "Do NOT supply replacement prose."
        ),
    )
    resume_anchor: str | None = Field(
        default=None,
        description=(
            "For grounding findings: the master-resume line that does or does not "
            "support the excerpt. Null if no supporting line exists."
        ),
    )


class Critique(BaseModel):
    round_index: int = Field(ge=0)
    scores: list[DimensionScore] = Field(min_length=5, max_length=5)
    findings: list[Finding] = Field(
        default_factory=list,
        max_length=12,
        description="Ordered by severity, blockers first. Empty list is valid.",
    )
    unsupported_claim_count: int = Field(
        ge=0,
        description="Count of BLOCKER-severity grounding findings. Redundant by design — "
        "makes the gate cheap to evaluate without walking the findings list.",
    )
    missing_requirements: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Requirements stated in the posting that the draft never addresses, "
        "AND that the master resume can actually support. Omit ones he genuinely lacks.",
    )
    verdict: Verdict
    overall: float = Field(ge=1.0, le=5.0, description="Weighted mean. See WEIGHTS.")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

WEIGHTS: dict[Dimension, float] = {
    Dimension.GROUNDING: 0.30,
    Dimension.COVERAGE: 0.25,
    Dimension.SPECIFICITY: 0.20,
    Dimension.DENSITY: 0.15,
    Dimension.VOICE: 0.10,
}

MAX_ROUNDS = 3
ACCEPT_THRESHOLD = 4.0
MIN_DELTA = 0.15  # stop early if a round improves overall by less than this


def compute_overall(scores: list[DimensionScore]) -> float:
    return round(sum(WEIGHTS[s.dimension] * s.score for s in scores), 3)


def should_continue(history: list[Critique]) -> bool:
    """Stop conditions, in order: hard gate, round cap, quality bar, plateau."""
    latest = history[-1]
    if latest.unsupported_claim_count > 0:
        return latest.round_index + 1 < MAX_ROUNDS  # always try to fix blockers
    if latest.round_index + 1 >= MAX_ROUNDS:
        return False
    if latest.overall >= ACCEPT_THRESHOLD:
        return False
    if len(history) >= 2 and (latest.overall - history[-2].overall) < MIN_DELTA:
        return False
    return True


# ---------------------------------------------------------------------------
# Rubric (goes in the Critic's system prompt)
# ---------------------------------------------------------------------------

RUBRIC = """
You score application drafts. You do not write them. Never supply replacement
prose — if you catch yourself drafting a better sentence, convert it into a
fix_direction instead.

You are given three inputs: the job posting, the candidate's master resume
(the sole source of truth about the candidate), and the draft.

Score each of five dimensions 1-5 using these anchors.

GROUNDING — is every factual claim traceable to the master resume?
  5  Every claim maps to a specific resume line. Numbers match exactly.
  4  All claims supported; one is stretched in emphasis but not in substance.
  3  A claim is directionally true but inflated (scope, duration, or ownership).
  2  A claim has no resume support, or a number was invented or rounded up.
  1  Multiple invented claims, or a technology/role the candidate never had.
  Any score of 1-2 produces a BLOCKER finding. Treat inflation of ownership
  ("led" for solo coursework, "in production" for a demo, team size, user
  counts) as invention, not as emphasis.

COVERAGE — does the draft address what the posting actually asks for?
  5  Every stated requirement the candidate can support is addressed directly.
  4  All core requirements addressed; a nice-to-have is unaddressed.
  3  A core requirement is addressed only obliquely.
  2  A core requirement the resume could support is ignored entirely.
  1  The draft reads as if written for a different posting.
  Do not penalize the draft for skipping a requirement the candidate genuinely
  lacks. Silence there is correct; fabrication would be a GROUNDING blocker.

SPECIFICITY — concrete over abstract.
  5  Named technologies, measured outcomes, and the actual technical decision.
  4  Concrete throughout; one claim states an outcome without its mechanism.
  3  Mixed — real specifics sit beside abstract capability statements.
  2  Mostly abstract. "Experience with distributed systems" with nothing behind it.
  1  No verifiable detail anywhere.
  A number with no method behind it is not specificity. "Improved performance
  40%" scores no better than "improved performance" unless the how is present.

DENSITY — signal per word.
  5  Nothing removable without losing information.
  4  One or two sentences carry less than their length.
  3  A visible filler paragraph, or restated resume content in prose form.
  2  Opening and closing are pure ceremony; the middle repeats the resume.
  1  Mostly filler.
  Penalize: restating the company's own mission back to them, "I am writing to
  express my interest," stacked adjectives, and any sentence that would survive
  unchanged in an application to a different company.

VOICE — does it read as written by this person?
  5  Specific register, plain sentences, a real reason for wanting this role.
  4  Natural throughout; one stock phrase.
  3  Competent but interchangeable with any other applicant's letter.
  2  Recognisably machine-generated cadence: triads, "not just X but Y,"
     em-dash asides, "passionate about," "excited by the opportunity to."
  1  Template with slots filled.

Findings rules:
  - One finding per defect. Do not merge two problems into one finding.
  - excerpt must be copied verbatim from the draft, long enough to locate
    unambiguously but no longer than needed.
  - fix_direction is imperative and addressable in a single edit:
    "Replace the user-count claim with the resume's actual figure, or cut it."
  - Cap findings at 12. If there are more, keep the highest-severity ones —
    a Writer given 20 findings will fix them shallowly.
  - Empty findings with all 5s is a legitimate output. Do not invent problems
    to look rigorous.

Verdict:
  REVISE if any BLOCKER exists, or overall < 4.0.
  ACCEPT otherwise.
  The verdict is advisory — the orchestrator owns the stop decision.

Return only the Critique JSON object. No preamble, no markdown fences.
"""
