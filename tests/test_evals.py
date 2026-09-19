"""The eval harness: cassettes, edit magnitude, and the aggregation that
answers whether the critic loop improves drafts or plateaus."""

import json

import pytest

from internship_agent.agents.critic import Dimension
from internship_agent.evals.cassette import (
    Cassette,
    CassetteMiss,
    RecordingBackend,
    ReplayBackend,
    call_key,
)
from internship_agent.evals.harness import (
    ArmResult,
    PostingRun,
    RoundStat,
    edit_magnitude,
    summarise,
)
from internship_agent.llm.base import LLMOutputError
from internship_agent.writer.models import Bullet, Draft
from tests.fakes import FakeLLM


def draft(tag: str = "a") -> Draft:
    return Draft(
        bullets=[Bullet(text=f"Bullet {tag} {i}.", resume_anchor="line") for i in range(3)],
        cover_letter=f"Letter {tag}. " * 30,
    )


# --- cassette ---------------------------------------------------------------


def test_recording_then_replaying_returns_the_same_answer():
    cassette = Cassette()
    recorder = RecordingBackend(FakeLLM([draft("x")]), cassette, tag="loop")

    live = recorder.complete(system="SYS", user="USR", schema=Draft)
    replay = ReplayBackend(cassette, tag="loop", model="fake-model")
    replayed = replay.complete(system="SYS", user="USR", schema=Draft)

    assert replayed.parsed == live.parsed
    assert replayed.model == live.model
    assert replayed.usage == live.usage


def test_replay_survives_a_save_and_load_round_trip(tmp_path):
    cassette = Cassette()
    RecordingBackend(FakeLLM([draft("x")]), cassette, tag="loop").complete(
        system="SYS", user="USR", schema=Draft
    )
    path = tmp_path / "c.json"
    cassette.save(path)

    replay = ReplayBackend(Cassette.load(path), tag="loop", model="fake-model")

    assert replay.complete(system="SYS", user="USR", schema=Draft).parsed == draft("x")


def test_a_changed_prompt_misses_loudly_rather_than_serving_a_stale_answer():
    cassette = Cassette()
    RecordingBackend(FakeLLM([draft()]), cassette, tag="loop").complete(
        system="SYS", user="USR", schema=Draft
    )
    replay = ReplayBackend(cassette, tag="loop", model="fake-model")

    with pytest.raises(CassetteMiss, match="re-record"):
        replay.complete(system="SYS", user="a different prompt", schema=Draft)


def test_the_arm_tag_separates_otherwise_identical_calls():
    """The control's single-pass draft uses the same prompt as the loop's round
    0. Without the tag in the key they would share one recording and the
    control would be identical to round 0 by construction."""
    loop_key = call_key(tag="loop", model="m", schema="Draft", system="S", user="U")
    control_key = call_key(tag="control", model="m", schema="Draft", system="S", user="U")

    assert loop_key != control_key


def test_the_control_arm_cannot_read_the_loop_arms_recording():
    cassette = Cassette()
    RecordingBackend(FakeLLM([draft()]), cassette, tag="loop").complete(
        system="SYS", user="USR", schema=Draft
    )

    control = ReplayBackend(cassette, tag="control", model="fake-model")

    with pytest.raises(CassetteMiss):
        control.complete(system="SYS", user="USR", schema=Draft)


def test_repeated_identical_calls_replay_in_order_then_run_out():
    cassette = Cassette()
    recorder = RecordingBackend(FakeLLM([draft("one"), draft("two")]), cassette, tag="loop")
    recorder.complete(system="S", user="U", schema=Draft)
    recorder.complete(system="S", user="U", schema=Draft)

    replay = ReplayBackend(cassette, tag="loop", model="fake-model")

    assert replay.complete(system="S", user="U", schema=Draft).parsed == draft("one")
    assert replay.complete(system="S", user="U", schema=Draft).parsed == draft("two")
    with pytest.raises(CassetteMiss):
        replay.complete(system="S", user="U", schema=Draft)


def test_a_recording_that_no_longer_validates_is_an_output_error(tmp_path):
    cassette = Cassette()
    RecordingBackend(FakeLLM([draft()]), cassette, tag="loop").complete(
        system="S", user="U", schema=Draft
    )
    cassette.recordings[0].raw_text = json.dumps({"bullets": [], "cover_letter": ""})

    replay = ReplayBackend(cassette, tag="loop", model="fake-model")

    with pytest.raises(LLMOutputError, match="no longer validates"):
        replay.complete(system="S", user="U", schema=Draft)


def test_cost_summary_totals_tokens_per_model():
    cassette = Cassette()
    RecordingBackend(FakeLLM([draft(), draft()]), cassette, tag="loop").complete(
        system="S", user="U", schema=Draft
    )
    RecordingBackend(FakeLLM([draft()]), cassette, tag="control").complete(
        system="S", user="U", schema=Draft
    )

    summary = cassette.cost_summary()

    assert summary["fake-model"] == {"calls": 2, "in": 200, "out": 40}


# --- edit magnitude ---------------------------------------------------------


def test_identical_text_has_no_edit_magnitude():
    assert edit_magnitude("the same words here", "the same words here") == 0.0


def test_completely_rewritten_text_is_a_full_edit():
    assert edit_magnitude("alpha beta gamma", "delta epsilon zeta") == 1.0


def test_a_partial_rewrite_lands_between():
    magnitude = edit_magnitude("one two three four", "one two three five")

    assert 0.0 < magnitude < 1.0


def test_edit_magnitude_ignores_whitespace_and_case():
    assert edit_magnitude("Hello   World", "hello world") == 0.0


def test_edit_magnitude_from_nothing_is_a_full_edit():
    assert edit_magnitude("", "some new words") == 1.0
    assert edit_magnitude("", "") == 0.0


# --- aggregation ------------------------------------------------------------


def _round(index: int, overall: float, **dims: int) -> RoundStat:
    scores = {d: dims.get(d.value, 3) for d in Dimension}
    return RoundStat(
        round_index=index,
        overall=overall,
        unsupported_claim_count=0,
        dimension_scores=scores,
        edit_magnitude=None,
        voice_hits=0,
    )


def test_summary_averages_overall_by_round_across_postings():
    runs = [
        PostingRun(
            posting_id=1,
            company="A",
            title="T",
            loop=ArmResult(rounds=[_round(0, 2.0), _round(1, 3.0), _round(2, 4.0)]),
            control=ArmResult(rounds=[_round(0, 2.5)]),
        ),
        PostingRun(
            posting_id=2,
            company="B",
            title="T",
            loop=ArmResult(rounds=[_round(0, 3.0), _round(1, 3.5)]),
            control=ArmResult(rounds=[_round(0, 2.9)]),
        ),
    ]

    summary = summarise(runs)

    assert summary.by_round[0].mean_overall == pytest.approx(2.5)
    assert summary.by_round[0].postings == 2
    assert summary.by_round[1].mean_overall == pytest.approx(3.25)
    assert summary.by_round[2].mean_overall == pytest.approx(4.0)
    assert summary.by_round[2].postings == 1


def test_summary_reports_per_dimension_means_by_round():
    runs = [
        PostingRun(
            posting_id=1,
            company="A",
            title="T",
            loop=ArmResult(rounds=[_round(0, 2.0, grounding=2), _round(1, 4.0, grounding=5)]),
            control=ArmResult(rounds=[_round(0, 2.0, grounding=2)]),
        )
    ]

    summary = summarise(runs)

    assert summary.by_round[0].mean_by_dimension[Dimension.GROUNDING] == pytest.approx(2.0)
    assert summary.by_round[1].mean_by_dimension[Dimension.GROUNDING] == pytest.approx(5.0)


def test_summary_compares_the_final_round_against_the_control():
    runs = [
        PostingRun(
            posting_id=1,
            company="A",
            title="T",
            loop=ArmResult(rounds=[_round(0, 2.0), _round(1, 4.0)]),
            control=ArmResult(rounds=[_round(0, 2.4)]),
        )
    ]

    summary = summarise(runs)

    assert summary.mean_control == pytest.approx(2.4)
    assert summary.mean_final == pytest.approx(4.0)
    assert summary.lift_over_control == pytest.approx(1.6)
    # The noise floor: an independent redraw of the same prompt.
    assert summary.control_minus_round_zero == pytest.approx(0.4)


def test_the_verdict_calls_out_a_lift_smaller_than_the_noise_floor():
    """If an independent redraw moves the score as much as the whole loop does,
    the loop has not been shown to do anything."""
    runs = [
        PostingRun(
            posting_id=1,
            company="A",
            title="T",
            loop=ArmResult(rounds=[_round(0, 3.0), _round(1, 3.2)]),
            control=ArmResult(rounds=[_round(0, 3.4)]),
        )
    ]

    summary = summarise(runs)

    assert summary.lift_over_control < 0
    assert "noise" in summary.verdict.lower()


def test_the_verdict_names_a_plateau():
    runs = [
        PostingRun(
            posting_id=i,
            company="A",
            title="T",
            loop=ArmResult(rounds=[_round(0, 2.0), _round(1, 3.6), _round(2, 3.62)]),
            control=ArmResult(rounds=[_round(0, 2.0)]),
        )
        for i in range(3)
    ]

    summary = summarise(runs)

    assert "plateau" in summary.verdict.lower()
    assert summary.round_deltas[1] == pytest.approx(1.6)
    assert summary.round_deltas[2] == pytest.approx(0.02)


def test_an_empty_run_set_summarises_without_dividing_by_zero():
    summary = summarise([])

    assert summary.by_round == []
    assert summary.mean_final is None
    assert "no" in summary.verdict.lower()
