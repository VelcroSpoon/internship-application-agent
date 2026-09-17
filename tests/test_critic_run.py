"""Critic runner: normalization of the gate's inputs, persistence of the
full critique plus per-dimension rows, and the prompt's contents."""

import json

import pytest

from internship_agent.agents.critic import Critique, Dimension, Severity, Verdict
from internship_agent.config import CriticConfig
from internship_agent.critic.run import count_blockers, normalize, run_critic
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.writer.models import Bullet, Draft
from tests.critique_factory import critique, finding
from tests.fakes import FakeLLM

TS = "2026-09-17T00:00:00Z"
LETTER = "I built a MinHash deduper in pandas for 40k support tickets. " * 8


def draft() -> Draft:
    return Draft(
        bullets=[
            Bullet(text=f"Bullet {i} on PyTorch.", resume_anchor=f"resume line {i}")
            for i in range(3)
        ],
        cover_letter=LETTER,
    )


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def posting_row(conn):
    conn.execute(
        "INSERT INTO postings (dedupe_hash, source, company, title, location, url, description, "
        "content_hash, first_seen_at, last_seen_at) "
        "VALUES ('h', 's', 'Scale AI', 'ML Intern', 'Montreal, QC', 'u', "
        "'Requires PyTorch.', 'c', ?, ?)",
        (TS, TS),
    )
    return conn.execute("SELECT * FROM postings").fetchone()


def seed_draft(conn, posting_id: int) -> int:
    conn.execute(
        "INSERT INTO applications (posting_id, status, created_at, updated_at) "
        "VALUES (?, 'drafting', ?, ?)",
        (posting_id, TS, TS),
    )
    cur = conn.execute(
        "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
        "writer_model, created_at) VALUES (1, 0, '[]', 'x', 'm', ?)",
        (TS,),
    )
    return cur.lastrowid


def _run(conn, llm, posting, draft_id, **kw):
    kw.setdefault("round_index", 0)
    kw.setdefault("resume_text", "RESUME")
    kw.setdefault("config", CriticConfig(model="fake"))
    kw.setdefault("application_id", 1)
    kw.setdefault("now", TS)
    return run_critic(conn, llm, posting=posting, draft=draft(), draft_id=draft_id, **kw)


# --- normalization ----------------------------------------------------------


def test_count_blockers_counts_only_grounding_blockers():
    c = critique(
        findings=[
            finding(Dimension.GROUNDING, Severity.BLOCKER),
            finding(Dimension.GROUNDING, Severity.BLOCKER, excerpt="led the team"),
            finding(Dimension.GROUNDING, Severity.MAJOR),  # not a blocker
            finding(Dimension.VOICE, Severity.BLOCKER),  # not grounding
        ],
        unsupported_claim_count=0,
    )

    assert count_blockers(c) == 2


def test_overall_is_recomputed_from_the_scores_with_spec_weights():
    """The model claimed 4.9; grounding 2 with the rest at 5 is 4.10."""
    c = critique(overall=4.9, grounding=2, coverage=5, specificity=5, density=5, voice=5)

    fixed, reported = normalize(c, round_index=0)

    assert fixed.overall == pytest.approx(4.10)
    assert reported["overall"] == 4.9


def test_unsupported_claim_count_is_recomputed_so_the_hard_gate_cannot_be_dodged():
    c = critique(
        findings=[finding(), finding(excerpt="led a team of six")],
        unsupported_claim_count=0,  # model under-reported
    )

    fixed, reported = normalize(c, round_index=0)

    assert fixed.unsupported_claim_count == 2
    assert reported["unsupported_claim_count"] == 0


def test_round_index_comes_from_the_orchestrator_not_the_model():
    fixed, reported = normalize(critique(round_index=0), round_index=2)

    assert fixed.round_index == 2
    assert reported["round_index"] == 0


def test_verdict_is_left_alone_because_the_gate_never_reads_it():
    c = critique(verdict=Verdict.ACCEPT, grounding=1, coverage=1, specificity=1, density=1, voice=1)

    fixed, reported = normalize(c, round_index=0)

    assert fixed.verdict is Verdict.ACCEPT
    assert fixed.overall == pytest.approx(1.0)
    assert reported["verdict"] == "accept"


def test_findings_and_scores_pass_through_untouched():
    c = critique(findings=[finding()], grounding=2)

    fixed, _ = normalize(c, round_index=1)

    assert fixed.findings == c.findings
    assert fixed.scores == c.scores


# --- persistence ------------------------------------------------------------


def test_persists_critique_with_per_dimension_score_rows(conn):
    posting = posting_row(conn)
    draft_id = seed_draft(conn, posting["id"])
    llm = FakeLLM([critique(grounding=2, coverage=4, specificity=4, density=4, voice=4)])

    result = _run(conn, llm, posting, draft_id)

    row = conn.execute("SELECT * FROM critiques").fetchone()
    assert row["draft_id"] == draft_id and row["round_index"] == 0
    assert row["overall"] == pytest.approx(3.4)
    assert row["critic_model"] == "fake-model"
    assert Critique.model_validate_json(row["critique_json"]).overall == pytest.approx(3.4)
    scores = conn.execute(
        "SELECT dimension, score FROM critique_scores ORDER BY dimension"
    ).fetchall()
    assert len(scores) == 5
    assert dict(scores) == {
        "coverage": 4,
        "density": 4,
        "grounding": 2,
        "specificity": 4,
        "voice": 4,
    }
    assert result.critique_id == row["id"]


def test_model_reported_values_are_kept_for_the_eval(conn):
    posting = posting_row(conn)
    draft_id = seed_draft(conn, posting["id"])
    llm = FakeLLM([critique(overall=5.0, unsupported_claim_count=0, findings=[finding()])])

    _run(conn, llm, posting, draft_id)

    stored = json.loads(conn.execute("SELECT usage_json FROM critiques").fetchone()[0])
    assert stored["model_reported"]["overall"] == 5.0
    assert stored["model_reported"]["unsupported_claim_count"] == 0
    assert stored["usage"]["prompt_tokens"] == 100
    row = conn.execute("SELECT overall, unsupported_claim_count FROM critiques").fetchone()
    assert row["overall"] == pytest.approx(3.0) and row["unsupported_claim_count"] == 1


def test_scored_event_carries_the_gate_inputs(conn):
    posting = posting_row(conn)
    draft_id = seed_draft(conn, posting["id"])

    _run(conn, FakeLLM([critique(findings=[finding()])]), posting, draft_id)

    payload = json.loads(
        conn.execute("SELECT payload_json FROM events WHERE kind = 'critic.scored'").fetchone()[0]
    )
    assert payload["unsupported_claim_count"] == 1 and payload["findings"] == 1


def test_persist_false_writes_nothing(conn):
    posting = posting_row(conn)
    draft_id = seed_draft(conn, posting["id"])

    result = _run(conn, FakeLLM([critique()]), posting, draft_id, persist=False)

    assert result.critique_id is None
    assert conn.execute("SELECT COUNT(*) FROM critiques").fetchone()[0] == 0


# --- prompt -----------------------------------------------------------------


def test_prompt_puts_rubric_and_resume_in_the_cacheable_system_turn(conn):
    posting = posting_row(conn)
    draft_id = seed_draft(conn, posting["id"])
    llm = FakeLLM([critique()])

    _run(conn, llm, posting, draft_id, round_index=2, resume_text="MY RESUME LINE")

    (call,) = llm.calls
    assert call.schema is Critique
    assert "GROUNDING" in call.system and "You score application drafts" in call.system
    assert "MY RESUME LINE" in call.system
    # Posting and draft vary per call, so they must not sit in the cached prefix.
    assert "Requires PyTorch." in call.user and "Requires PyTorch." not in call.system
    assert "Bullet 0 on PyTorch." in call.user
    assert "resume line 0" in call.user  # the writer's claimed anchors
    assert "round 2" in call.user
