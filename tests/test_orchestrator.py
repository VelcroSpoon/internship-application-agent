"""The revision loop. Two scripted backends (writer, critic), a real
database, no network.

What matters here: every draft is persisted with its critique and round
index, the stop decision comes from the spec's pure function, and a model
failure anywhere degrades into a reviewable application instead of a crash.
"""

import json

import pytest

from internship_agent.agents.critic import MAX_ROUNDS, Verdict, should_continue
from internship_agent.config import CriteriaFile, CriticConfig, VoiceConfig, WriterConfig
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.llm.base import LLMOutputError, LLMTransportError
from internship_agent.orchestrator import LoopRefused, describe_stop, run_loop
from internship_agent.writer.models import Bullet, Draft
from tests.critique_factory import critique, finding
from tests.fakes import FakeLLM

TS = "2026-09-17T00:00:00Z"


def draft(tag: str = "a") -> Draft:
    return Draft(
        bullets=[
            Bullet(text=f"Bullet {i} {tag} on PyTorch.", resume_anchor=f"line {i}")
            for i in range(3)
        ],
        cover_letter=f"Cover letter {tag}. I built a MinHash deduper in pandas. " * 8,
    )


def settings() -> CriteriaFile:
    from internship_agent.config import CandidateConfig, CriteriaConfig, ScreenerConfig

    return CriteriaFile(
        candidate=CandidateConfig(master_resume_path="config/master_resume.md"),
        criteria=CriteriaConfig(target_cycle="Summer 2027"),
        screener=ScreenerConfig(model="unused"),
        writer=WriterConfig(model="fake"),
        critic=CriticConfig(model="fake"),
    )


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def add_posting(conn) -> int:
    cur = conn.execute(
        "INSERT INTO postings (dedupe_hash, source, company, title, location, url, description, "
        "content_hash, first_seen_at, last_seen_at) "
        "VALUES ('h', 's', 'Scale AI', 'ML Intern', 'Montreal, QC', 'u', 'Needs PyTorch.', "
        "'c', ?, ?)",
        (TS, TS),
    )
    return cur.lastrowid


def _run(conn, writer_script, critic_script, posting_id, **kw):
    kw.setdefault("resume_text", "RESUME")
    kw.setdefault("voice", VoiceConfig())
    kw.setdefault("settings", settings())
    kw.setdefault("now", TS)
    return run_loop(
        conn,
        writer_backend=FakeLLM(writer_script),
        critic_backend=FakeLLM(critic_script),
        posting_id=posting_id,
        **kw,
    )


def rounds(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT round_index FROM drafts ORDER BY round_index")]


def critique_rounds(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT round_index FROM critiques ORDER BY round_index")]


def status(conn, application_id: int = 1) -> str:
    return conn.execute(
        "SELECT status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()[0]


# --- the normal paths -------------------------------------------------------


def test_runs_to_the_round_cap_and_persists_every_round(conn):
    """Low scores with real improvement each round: three drafts, three
    critiques, each pair sharing a round index. This is the data the project
    exists to produce."""
    pid = add_posting(conn)

    result = _run(
        conn,
        [draft("a"), draft("b"), draft("c")],
        [
            critique(0, default_score=2),  # 2.00
            critique(1, default_score=2, grounding=3),  # 2.30
            critique(2, default_score=3),  # 3.00
        ],
        pid,
    )

    assert rounds(conn) == [0, 1, 2]
    assert critique_rounds(conn) == [0, 1, 2]
    assert result.rounds_completed == MAX_ROUNDS
    assert result.stopped_because == "round_cap"
    assert status(conn) == "awaiting_review"
    paired = conn.execute(
        "SELECT d.round_index, c.round_index, c.overall FROM drafts d "
        "JOIN critiques c ON c.draft_id = d.id ORDER BY d.round_index"
    ).fetchall()
    assert [tuple(r) for r in paired] == [(0, 0, 2.0), (1, 1, 2.3), (2, 2, 3.0)]


def test_a_draft_that_clears_the_bar_first_time_stops_at_one_round(conn):
    pid = add_posting(conn)

    result = _run(conn, [draft()], [critique(0, default_score=5, verdict=Verdict.ACCEPT)], pid)

    assert rounds(conn) == [0] and critique_rounds(conn) == [0]
    assert result.stopped_because == "quality_bar"
    assert result.final_overall == pytest.approx(5.0)
    assert status(conn) == "awaiting_review"


def test_a_blocker_keeps_the_loop_going_past_a_passing_score(conn):
    """The hard gate: scoring 4.8 does not matter if a claim is unsupported."""
    pid = add_posting(conn)
    blocked = critique(0, default_score=5, findings=[finding()])
    clean = critique(1, default_score=5)

    result = _run(conn, [draft("a"), draft("b")], [blocked, clean], pid)

    assert rounds(conn) == [0, 1]
    first = conn.execute("SELECT unsupported_claim_count FROM critiques ORDER BY id").fetchall()
    assert [r[0] for r in first] == [1, 0]
    assert result.stopped_because == "quality_bar"


def test_a_plateau_stops_the_loop_before_the_cap(conn):
    pid = add_posting(conn)

    result = _run(
        conn,
        [draft("a"), draft("b")],
        [critique(0, default_score=3), critique(1, default_score=3, voice=4)],  # 3.00 -> 3.10
        pid,
    )

    assert rounds(conn) == [0, 1]
    assert result.stopped_because == "plateau"


def test_unresolved_blockers_at_the_cap_are_reported_as_such(conn):
    pid = add_posting(conn)
    blocked = [critique(i, default_score=2, findings=[finding()]) for i in range(3)]

    result = _run(conn, [draft("a"), draft("b"), draft("c")], blocked, pid)

    assert result.stopped_because == "blockers_unresolved_at_round_cap"
    assert result.final_unsupported_claims == 1
    assert status(conn) == "awaiting_review"


# --- composing with the standalone writer -----------------------------------


def test_an_existing_round_zero_draft_is_critiqued_rather_than_rewritten(conn):
    """`writer draft` then `loop run` must not produce two round-0 drafts."""
    pid = add_posting(conn)
    from internship_agent.writer.run import run_writer

    run_writer(
        conn,
        FakeLLM([draft("original")]),
        posting_id=pid,
        resume_text="RESUME",
        voice=VoiceConfig(),
        config=WriterConfig(model="fake"),
        now=TS,
    )

    result = _run(conn, [], [critique(0, default_score=5)], pid)

    assert rounds(conn) == [0]
    assert result.rounds_completed == 1
    assert "original" in conn.execute("SELECT cover_letter FROM drafts").fetchone()[0]


def test_an_application_already_awaiting_review_is_refused(conn):
    pid = add_posting(conn)
    _run(conn, [draft()], [critique(0, default_score=5)], pid)

    with pytest.raises(LoopRefused):
        _run(conn, [draft()], [critique(0, default_score=5)], pid)


def test_unknown_posting_raises(conn):
    with pytest.raises(LookupError):
        _run(conn, [], [], 999)


# --- degradation ------------------------------------------------------------


def test_a_critic_returning_junk_twice_leaves_a_reviewable_application(conn):
    """The spec's requirement: malformed model output must not crash mid-application."""
    pid = add_posting(conn)

    result = _run(
        conn,
        [draft()],
        [LLMOutputError("not json", "{{"), LLMOutputError("still not json", "{{{")],
        pid,
    )

    assert rounds(conn) == [0]  # the draft survives
    assert critique_rounds(conn) == []
    assert result.stopped_because == "critic_failed"
    assert status(conn) == "awaiting_review"
    kinds = [r[0] for r in conn.execute("SELECT kind FROM events ORDER BY id")]
    assert "critic.failed" in kinds
    assert "loop.finished" in kinds


def test_a_critic_failing_mid_loop_keeps_the_rounds_already_done(conn):
    pid = add_posting(conn)

    result = _run(
        conn,
        [draft("a"), draft("b")],
        [critique(0, default_score=2), LLMOutputError("junk", "x"), LLMOutputError("junk", "x")],
        pid,
    )

    assert rounds(conn) == [0, 1]
    assert critique_rounds(conn) == [0]
    assert result.stopped_because == "critic_failed"
    assert status(conn) == "awaiting_review"


def test_a_failing_revision_keeps_the_earlier_draft_and_its_critique(conn):
    pid = add_posting(conn)

    result = _run(
        conn,
        [draft("a"), LLMOutputError("bad", "x"), LLMOutputError("bad", "x")],
        [critique(0, default_score=2)],
        pid,
    )

    assert rounds(conn) == [0] and critique_rounds(conn) == [0]
    assert result.stopped_because == "writer_failed"
    assert status(conn) == "awaiting_review"


def test_a_failing_first_draft_leaves_nothing_to_review(conn):
    pid = add_posting(conn)

    result = _run(conn, [LLMOutputError("bad", "x"), LLMOutputError("bad", "x")], [], pid)

    assert rounds(conn) == []
    assert result.stopped_because == "writer_failed"
    assert result.rounds_completed == 0
    assert status(conn) == "drafting"  # nothing to approve, so not handed to the human


def test_an_unreachable_backend_stops_the_loop_without_raising(conn):
    pid = add_posting(conn)

    result = _run(conn, [draft()], [LLMTransportError("connection refused")], pid)

    assert rounds(conn) == [0]
    assert result.stopped_because == "backend_unreachable"
    assert status(conn) == "awaiting_review"


# --- the stop-reason label --------------------------------------------------


@pytest.mark.parametrize(
    "history",
    [
        [critique(0, overall=2.0)],
        [critique(0, overall=4.5)],
        [critique(0, overall=4.5, findings=[finding()])],
        [critique(2, overall=2.0)],
        [critique(2, overall=2.0, findings=[finding()])],
        [critique(0, default_score=3), critique(1, default_score=3, voice=4)],  # 3.00 -> 3.10
        [critique(0, overall=3.0), critique(1, overall=3.9)],
    ],
)
def test_the_label_never_disagrees_with_the_spec_function(history):
    """describe_stop exists for reporting, so it must mirror should_continue
    exactly rather than drift into a second, subtly different gate."""
    assert (describe_stop(history) == "continuing") is should_continue(history)


# --- persistence detail -----------------------------------------------------


def test_each_revision_stores_the_critique_that_prompted_it(conn):
    pid = add_posting(conn)

    _run(
        conn,
        [draft("a"), draft("b")],
        [critique(0, default_score=2, findings=[finding()]), critique(1, default_score=5)],
        pid,
    )

    stored = conn.execute("SELECT critique_json FROM critiques ORDER BY round_index").fetchall()
    first = json.loads(stored[0][0])
    assert first["round_index"] == 0
    assert first["findings"][0]["fix_direction"].startswith("Cut the user-count")
    assert len(json.loads(stored[1][0])["scores"]) == 5


def test_the_score_by_round_query_works_across_applications(conn):
    """The one query the whole schema is shaped around."""
    for _ in range(2):
        pid = add_posting(conn)
        conn.execute("UPDATE postings SET dedupe_hash = 'h' || id WHERE id = ?", (pid,))
        _run(
            conn,
            [draft("a"), draft("b")],
            [critique(0, default_score=2), critique(1, default_score=4)],  # 2.0 -> 4.0, stops
            pid,
        )

    rows = conn.execute(
        "SELECT round_index, AVG(overall) FROM critiques GROUP BY round_index ORDER BY round_index"
    ).fetchall()

    assert [tuple(r) for r in rows] == [(0, 2.0), (1, 4.0)]
