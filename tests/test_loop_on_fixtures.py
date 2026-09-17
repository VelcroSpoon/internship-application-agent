"""The loop over real posting text with scripted models.

The other loop tests use a toy posting to isolate control flow. These run
the same code against the four postings captured from a live Greenhouse
board, so prompt assembly meets the real thing: 7-10k characters of HTML
flattened to text, em-dashes, multi-city locations, and a title with a
trailing space. No network, no API spend.
"""

import pytest

from internship_agent.config import (
    CandidateConfig,
    CriteriaConfig,
    CriteriaFile,
    CriticConfig,
    ScreenerConfig,
    VoiceConfig,
    WriterConfig,
    load_voice,
)
from internship_agent.orchestrator import run_loop
from internship_agent.writer.models import Bullet, Draft
from tests.conftest import INTERN_EXTERNAL_ID
from tests.critique_factory import critique, finding
from tests.fakes import FakeLLM


def settings() -> CriteriaFile:
    return CriteriaFile(
        candidate=CandidateConfig(master_resume_path="config/master_resume.md"),
        criteria=CriteriaConfig(target_cycle="Summer 2027"),
        screener=ScreenerConfig(model="fake"),
        writer=WriterConfig(model="fake"),
        critic=CriticConfig(model="fake"),
    )


def draft(tag: str) -> Draft:
    return Draft(
        bullets=[
            Bullet(
                text=f"Fine-tuned a BERT-style classifier ({tag}), raising macro-F1 to 0.84.",
                resume_anchor="raised macro-F1 from 0.71 to 0.84",
            ),
            Bullet(
                text="Deduplicated 12% near-duplicate tickets with MinHash in pandas.",
                resume_anchor="deduplicated 12% near-duplicate tickets using MinHash",
            ),
            Bullet(
                text="Built a pytest autograder that cut grading turnaround to two days.",
                resume_anchor="cut grading turnaround from a week to two days",
            ),
        ],
        cover_letter=f"Draft {tag}. " + "I work on ML systems that other people rely on. " * 12,
    )


def test_the_loop_runs_over_a_real_posting(seeded_db, fake_resume):
    conn, ids = seeded_db
    posting_id = ids[INTERN_EXTERNAL_ID]

    result = run_loop(
        conn,
        writer_backend=FakeLLM([draft("a"), draft("b")]),
        critic_backend=FakeLLM(
            [
                critique(0, default_score=3, findings=[finding()]),  # blocker
                critique(1, default_score=4),  # clean, clears the bar
            ]
        ),
        posting_id=posting_id,
        resume_text=fake_resume,
        voice=load_voice(),
        settings=settings(),
    )

    assert result.rounds_completed == 2
    assert result.stopped_because == "quality_bar"
    assert [c.overall for c in result.history] == [pytest.approx(3.0), pytest.approx(4.0)]
    assert conn.execute("SELECT status FROM applications").fetchone()[0] == "awaiting_review"


def test_the_real_posting_text_reaches_the_prompts_intact(seeded_db, fake_resume):
    conn, ids = seeded_db
    posting_id = ids[INTERN_EXTERNAL_ID]
    writer, critic = FakeLLM([draft("a")]), FakeLLM([critique(0, default_score=5)])

    run_loop(
        conn,
        writer_backend=writer,
        critic_backend=critic,
        posting_id=posting_id,
        resume_text=fake_resume,
        voice=load_voice(),
        settings=settings(),
    )

    writer_call, critic_call = writer.calls[0], critic.calls[0]
    # A real requirement from the captured posting, flattened out of HTML.
    assert "Available for a Summer 2027 internship" in writer_call.user
    assert "<p>" not in writer_call.user and "&lt;" not in writer_call.user
    assert "Software Engineering Intern (Summer 2027)" in writer_call.user
    # The shipped VOICE list and the real resume are in the cacheable prefix.
    assert "synergy" in writer_call.system  # from config/voice.toml
    assert "Plain declarative sentences." in writer_call.system  # a style note
    assert "MinHash" in writer_call.system
    # The Critic sees the rubric and the same resume, not the Writer's rules.
    # ("passionate about" appears in both, since the rubric names it as machine
    # cadence, so it cannot distinguish the two prompts.)
    assert "GROUNDING" in critic_call.system and "MinHash" in critic_call.system
    assert "synergy" not in critic_call.system
    assert "Plain declarative sentences." not in critic_call.system


def test_every_fixture_posting_can_be_drafted_for(seeded_db, fake_resume):
    """Includes the non-intern postings: the loop must not choke on any of
    the real text, whatever the Screener would have said about fit."""
    conn, ids = seeded_db

    for external_id, posting_id in ids.items():
        result = run_loop(
            conn,
            writer_backend=FakeLLM([draft(external_id)]),
            critic_backend=FakeLLM([critique(0, default_score=5)]),
            posting_id=posting_id,
            resume_text=fake_resume,
            voice=load_voice(),
            settings=settings(),
        )
        assert result.rounds_completed == 1, external_id

    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == len(ids)
    rows = conn.execute(
        "SELECT round_index, AVG(overall), COUNT(*) FROM critiques GROUP BY round_index"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(0, 5.0, len(ids))]


def test_a_draft_using_the_shipped_voice_list_is_flagged(seeded_db, fake_resume):
    conn, ids = seeded_db
    bad = draft("x").model_copy(
        update={"cover_letter": "I am passionate about distributed systems. " * 10}
    )

    run_loop(
        conn,
        writer_backend=FakeLLM([bad]),
        critic_backend=FakeLLM([critique(0, default_score=5)]),
        posting_id=ids[INTERN_EXTERNAL_ID],
        resume_text=fake_resume,
        voice=load_voice(),
        settings=settings(),
    )

    import json

    usage = json.loads(conn.execute("SELECT usage_json FROM drafts").fetchone()[0])
    assert "phrase: passionate about" in usage["voice_hits"]


def test_an_empty_voice_list_flags_nothing(seeded_db, fake_resume):
    """The VOICE list is config: emptying the file disables the tripwire
    without touching code."""
    conn, ids = seeded_db

    run_loop(
        conn,
        writer_backend=FakeLLM([draft("x")]),
        critic_backend=FakeLLM([critique(0, default_score=5)]),
        posting_id=ids[INTERN_EXTERNAL_ID],
        resume_text=fake_resume,
        voice=VoiceConfig(),
        settings=settings(),
    )

    import json

    usage = json.loads(conn.execute("SELECT usage_json FROM drafts").fetchone()[0])
    assert usage["voice_hits"] == []
