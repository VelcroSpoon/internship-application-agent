"""Builders for Critique objects in tests. Shared by the critic, leak, and
loop suites so a schema change breaks one file, not four."""

from __future__ import annotations

from internship_agent.agents.critic import (
    Critique,
    Dimension,
    DimensionScore,
    Finding,
    Severity,
    Verdict,
)


def dimension_scores(default: int = 3, **overrides: int) -> list[DimensionScore]:
    return [
        DimensionScore(dimension=d, score=overrides.get(d.value, default), reason="because")
        for d in Dimension
    ]


def finding(
    dimension: Dimension = Dimension.GROUNDING,
    severity: Severity = Severity.BLOCKER,
    fix_direction: str = "Cut the user-count claim or replace it with the resume's figure.",
    excerpt: str = "served 10,000 users",
    resume_anchor: str | None = None,
) -> Finding:
    return Finding(
        dimension=dimension,
        severity=severity,
        section="bullets",
        excerpt=excerpt,
        problem="The resume does not state a user count.",
        fix_direction=fix_direction,
        resume_anchor=resume_anchor,
    )


def critique(
    round_index: int = 0,
    *,
    default_score: int = 3,
    findings: list[Finding] | None = None,
    unsupported_claim_count: int | None = None,
    overall: float | None = None,
    verdict: Verdict = Verdict.REVISE,
    **score_overrides: int,
) -> Critique:
    """Defaults are internally consistent; pass overall/unsupported_claim_count
    explicitly to build the inconsistent ones the normalizer has to fix."""
    scores = dimension_scores(default_score, **score_overrides)
    found = findings or []
    blockers = sum(
        1 for f in found if f.severity is Severity.BLOCKER and f.dimension is Dimension.GROUNDING
    )
    return Critique(
        round_index=round_index,
        scores=scores,
        findings=found,
        unsupported_claim_count=(
            blockers if unsupported_claim_count is None else unsupported_claim_count
        ),
        verdict=verdict,
        overall=overall if overall is not None else sum(0.2 * s.score for s in scores),
    )
