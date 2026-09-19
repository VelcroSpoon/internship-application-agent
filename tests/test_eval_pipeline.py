"""Record then replay over the four real fixture postings, with scripted
models standing in for the API. What this proves: the real prompts are
deterministic enough that a replay reproduces a recording exactly, which is
the property the whole record-once design depends on."""

import json

import pytest

from internship_agent.config import (
    CandidateConfig,
    CriteriaConfig,
    CriteriaFile,
    CriticConfig,
    ScreenerConfig,
    WriterConfig,
    load_voice,
)
from internship_agent.evals.cassette import Cassette, CassetteMiss
from internship_agent.evals.harness import CONTROL, LOOP, summarise
from internship_agent.evals.run import record, replay, summary_json, write_report
from internship_agent.writer.models import Bullet, Draft
from tests.critique_factory import critique, finding
from tests.fakes import FakeLLM

MODEL = "fake-model"  # FakeLLM's model name, used for both agents here


def settings() -> CriteriaFile:
    return CriteriaFile(
        candidate=CandidateConfig(master_resume_path="config/master_resume.md"),
        criteria=CriteriaConfig(target_cycle="Summer 2027"),
        screener=ScreenerConfig(model="unused"),
        writer=WriterConfig(model=MODEL),
        critic=CriticConfig(model=MODEL),
    )


def draft(tag: str) -> Draft:
    return Draft(
        bullets=[
            Bullet(
                text=f"{tag}: fine-tuned a BERT-style classifier on 40k tickets.", resume_anchor="a"
            ),
            Bullet(text=f"{tag}: removed 12% near-duplicates with MinHash.", resume_anchor="b"),
            Bullet(text=f"{tag}: pytest autograder, a week down to two days.", resume_anchor="c"),
        ],
        cover_letter=f"Version {tag}. " + "I work on the pipeline more than the model. " * 10,
    )


def scripted():
    """Posting 1 runs the full three rounds; postings 2-4 clear the bar at once.
    Evaluate processes postings in order, loop arm before control arm."""
    return {
        (LOOP, "writer"): FakeLLM(
            [draft("p1r0"), draft("p1r1"), draft("p1r2"), draft("p2"), draft("p3"), draft("p4")]
        ),
        (LOOP, "critic"): FakeLLM(
            [
                critique(0, default_score=2, findings=[finding()]),
                critique(1, default_score=3),
                critique(2, default_score=4),
                critique(0, default_score=5),
                critique(0, default_score=5),
                critique(0, default_score=5),
            ]
        ),
        (CONTROL, "writer"): FakeLLM([draft(f"c{i}") for i in range(4)]),
        (CONTROL, "critic"): FakeLLM([critique(0, default_score=3) for _ in range(4)]),
    }


def _record(tmp_path, real_postings, fake_resume, voice=None):
    backends = scripted()
    return record(
        real_postings,
        writer_for=lambda tag: backends[(tag, "writer")],
        critic_for=lambda tag: backends[(tag, "critic")],
        resume_text=fake_resume,
        voice=voice or load_voice(),
        settings=settings(),
        cassette_path=tmp_path / "cassette.json",
    )


def test_replay_reproduces_the_recording_exactly(tmp_path, real_postings, fake_resume):
    recorded_runs, _ = _record(tmp_path, real_postings, fake_resume)

    replayed_runs = replay(
        real_postings,
        cassette=Cassette.load(tmp_path / "cassette.json"),
        writer_model=MODEL,
        critic_model=MODEL,
        resume_text=fake_resume,
        voice=load_voice(),
        settings=settings(),
    )

    assert summary_json(summarise(replayed_runs), replayed_runs) == summary_json(
        summarise(recorded_runs), recorded_runs
    )


def test_the_recording_has_one_entry_per_model_call(tmp_path, real_postings, fake_resume):
    _, cassette = _record(tmp_path, real_postings, fake_resume)

    by_tag = {LOOP: 0, CONTROL: 0}
    for rec in cassette.recordings:
        by_tag[rec.tag] += 1

    assert by_tag == {LOOP: 12, CONTROL: 8}  # 6 drafts + 6 critiques; 4 + 4


def test_the_loop_arm_ran_the_scripted_rounds(tmp_path, real_postings, fake_resume):
    runs, _ = _record(tmp_path, real_postings, fake_resume)

    assert [len(r.loop.rounds) for r in runs] == [3, 1, 1, 1]
    assert runs[0].loop.stopped_because == "round_cap"
    assert [s.overall for s in runs[0].loop.rounds] == [2.0, 3.0, 4.0]
    assert runs[0].loop.rounds[1].edit_magnitude > 0  # the revision changed the text
    assert all(len(r.control.rounds) == 1 for r in runs)


def test_changing_a_prompt_makes_replay_fail_loudly(tmp_path, real_postings, fake_resume):
    """Editing voice.toml changes the Writer's system prompt, so every recorded
    Writer answer is now an answer to a different question."""
    _record(tmp_path, real_postings, fake_resume)
    edited_voice = load_voice().model_copy(update={"banned_phrases": ["a new banned phrase"]})

    with pytest.raises(CassetteMiss, match="re-record"):
        replay(
            real_postings,
            cassette=Cassette.load(tmp_path / "cassette.json"),
            writer_model=MODEL,
            critic_model=MODEL,
            resume_text=fake_resume,
            voice=edited_voice,
            settings=settings(),
        )


def test_the_report_is_written_with_the_verdict_and_the_numbers(
    tmp_path, real_postings, fake_resume
):
    runs, cassette = _record(tmp_path, real_postings, fake_resume)

    summary, report = write_report(runs, cassette, out_dir=tmp_path / "results")

    text = report.read_text(encoding="utf-8")
    assert summary.verdict in text
    assert "## By round" in text and "paired Δ" in text
    assert "noise floor" in text
    assert "Scale AI" in text
    data = json.loads((tmp_path / "results" / "summary.json").read_text(encoding="utf-8"))
    assert data["postings"] == 4
    assert len(data["runs"]) == 4


def test_the_eval_never_touches_the_real_database(tmp_path, real_postings, fake_resume):
    from pathlib import Path

    real_db = Path(__file__).parent.parent / "data" / "agent.db"
    before = real_db.stat().st_mtime if real_db.exists() else None

    _record(tmp_path, real_postings, fake_resume)

    after = real_db.stat().st_mtime if real_db.exists() else None
    assert before == after
