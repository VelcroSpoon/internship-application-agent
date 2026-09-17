"""Stop conditions, against hand-built critique histories.

``should_continue`` and ``compute_overall`` ship in critic.py as the spec.
These are characterization tests: they pin the behaviour the orchestrator
depends on so a later edit to the spec file cannot change the loop's
shape silently. No database, no model, no I/O.

Constants under test: MAX_ROUNDS=3, ACCEPT_THRESHOLD=4.0, MIN_DELTA=0.15.
"""

import pytest

from internship_agent.agents.critic import (
    ACCEPT_THRESHOLD,
    MAX_ROUNDS,
    MIN_DELTA,
    WEIGHTS,
    Critique,
    Dimension,
    DimensionScore,
    Verdict,
    compute_overall,
    should_continue,
)


def scores(**by_dimension: int) -> list[DimensionScore]:
    """All five dimensions, defaulting to 3, overridden by name."""
    return [
        DimensionScore(dimension=d, score=by_dimension.get(d.value, 3), reason="r")
        for d in Dimension
    ]


def crit(round_index: int, overall: float, blockers: int = 0) -> Critique:
    return Critique(
        round_index=round_index,
        scores=scores(),
        findings=[],
        unsupported_claim_count=blockers,
        verdict=Verdict.REVISE,
        overall=overall,
    )


# --- compute_overall --------------------------------------------------------


def test_weights_sum_to_one_so_overall_stays_on_the_1_to_5_scale():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("uniform,expected", [(1, 1.0), (3, 3.0), (5, 5.0)])
def test_uniform_scores_give_that_score_as_overall(uniform, expected):
    assert compute_overall(scores(**{d.value: uniform for d in Dimension})) == expected


def test_grounding_is_weighted_most_heavily():
    """Dropping grounding to 2 costs more than dropping voice to 2."""
    bad_grounding = compute_overall(
        scores(grounding=2, coverage=5, specificity=5, density=5, voice=5)
    )
    bad_voice = compute_overall(scores(grounding=5, coverage=5, specificity=5, density=5, voice=2))
    assert bad_grounding == pytest.approx(4.10)
    assert bad_voice == pytest.approx(4.70)
    assert bad_grounding < bad_voice


# --- hard gate: blockers ----------------------------------------------------


def test_blocker_at_round_zero_continues():
    assert should_continue([crit(0, 2.0, blockers=1)]) is True


def test_blocker_overrides_a_passing_score():
    """The spec's named case: a draft can score above the bar and still be
    unfit, because an unsupported claim is disqualifying on its own."""
    history = [crit(0, 4.8, blockers=1)]

    assert history[-1].overall >= ACCEPT_THRESHOLD
    assert should_continue(history) is True


def test_blocker_overrides_a_plateau():
    history = [crit(0, 3.0, blockers=1), crit(1, 3.01, blockers=1)]

    assert history[-1].overall - history[-2].overall < MIN_DELTA
    assert should_continue(history) is True


def test_round_cap_beats_an_unfixed_blocker():
    """Three rounds is the budget even when the draft is still ungrounded.
    The human sees it with its critique rather than the loop running on."""
    history = [crit(0, 2.0, blockers=2), crit(1, 2.5, blockers=2), crit(2, 2.6, blockers=2)]

    assert should_continue(history) is False


# --- round cap --------------------------------------------------------------


def test_last_allowed_round_stops():
    assert should_continue([crit(MAX_ROUNDS - 1, 1.5)]) is False


def test_penultimate_round_continues():
    assert should_continue([crit(MAX_ROUNDS - 2, 1.5)]) is True


# --- quality bar ------------------------------------------------------------


def test_score_exactly_at_threshold_stops():
    assert should_continue([crit(0, ACCEPT_THRESHOLD)]) is False


def test_score_just_below_threshold_continues():
    assert should_continue([crit(0, 3.99)]) is True


# --- plateau ----------------------------------------------------------------


def test_improvement_below_min_delta_stops():
    assert should_continue([crit(0, 3.00), crit(1, 3.10)]) is False


def test_improvement_nominally_at_min_delta_falls_to_float_representation():
    """Reading `< MIN_DELTA` literally, a 0.15 gain should continue. In IEEE 754
    it depends on the operands: 3.15 - 3.00 is 0.1499999999999999 and stops,
    while 1.30 - 1.15 is 0.15000000000000013 and continues. Of the 76 reachable
    score pairs whose nominal gain is exactly 0.15, 50 stop and 26 continue.

    Pinned, not fixed: the threshold is the spec's. A draft that gained exactly
    the minimum is a plateau under either answer, so nothing real turns on it.
    """
    assert should_continue([crit(0, 3.00), crit(1, 3.15)]) is False
    assert should_continue([crit(0, 1.15), crit(1, 1.30)]) is True


def test_improvement_clearly_above_min_delta_continues():
    assert should_continue([crit(0, 3.00), crit(1, 3.20)]) is True


def test_a_regression_stops_the_loop():
    assert should_continue([crit(0, 3.50), crit(1, 2.90)]) is False


def test_plateau_is_measured_against_the_previous_round_only():
    """3.0 -> 3.5 -> 3.55: the last step is flat, so it stops, even though
    the run improved a lot overall."""
    assert should_continue([crit(0, 3.00), crit(1, 3.50), crit(2, 3.55)]) is False


def test_first_round_has_no_plateau_to_measure():
    assert should_continue([crit(0, 1.0)]) is True


# --- ordering of the gates --------------------------------------------------


def test_threshold_is_checked_before_plateau():
    """A big jump straight past the bar stops on quality, not on delta."""
    assert should_continue([crit(0, 2.0), crit(1, 4.5)]) is False


def test_a_clean_improving_mid_run_draft_continues():
    assert should_continue([crit(0, 2.0), crit(1, 3.0)]) is True
