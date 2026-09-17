"""Screener runner against a scripted fake backend. Covers persistence,
dry-run, the title pre-filter, retry-then-skip on malformed output, and
abort on transport failure."""

import json

import pytest

from internship_agent.config import (
    CandidateConfig,
    CriteriaConfig,
    CriteriaFile,
    Disqualifier,
    PrefilterConfig,
    ScreenerConfig,
)
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.llm.base import LLMOutputError, LLMTransportError
from internship_agent.screener.models import Screening
from internship_agent.screener.run import run_screener
from tests.fakes import FakeLLM

TS = "2026-09-17T00:00:00Z"


def settings(**overrides) -> CriteriaFile:
    base = CriteriaFile(
        candidate=CandidateConfig(master_resume_path="config/master_resume.md"),
        criteria=CriteriaConfig(
            target_cycle="Summer 2027",
            target_roles=["ML internship"],
            acceptable_locations=["Montreal, QC"],
            hard_disqualifiers=[
                Disqualifier(label="security clearance", pattern=r"security clearance"),
                Disqualifier(label="PhD required", pattern=r"\bPhD\b.{0,20}\brequired\b"),
            ],
            queue_threshold=70,
        ),
        prefilter=PrefilterConfig(title_patterns=[r"\bintern(ship)?\b"]),
        screener=ScreenerConfig(
            backend="ollama",
            model="fake",
            ollama_host="http://x",
            description_max_chars=500,
            max_attempts=2,
        ),
    )
    return base.model_copy(update=overrides)


def screening(score: int, reason: str = "fine") -> Screening:
    return Screening(
        fit_score=score,
        reason=reason,
        is_internship=True,
        matched_requirements=["Python"],
        missing_requirements=[],
    )


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def add_posting(conn, title: str, description: str = "Needs Python. " * 20) -> int:
    cur = conn.execute(
        "INSERT INTO postings (dedupe_hash, source, external_id, company, title, location, url, "
        "description, content_hash, first_seen_at, last_seen_at) "
        "VALUES (?, 'greenhouse:x', ?, 'Scale AI', ?, 'Montreal, QC', 'https://x', ?, 'h', ?, ?)",
        (f"h-{title}", title, title, description, TS, TS),
    )
    return cur.lastrowid


def rows(conn):
    return conn.execute(
        "SELECT posting_id, model, fit_score, reason, raw_json FROM screenings ORDER BY id"
    ).fetchall()


def events(conn, kind):
    return conn.execute("SELECT payload_json FROM events WHERE kind = ?", (kind,)).fetchall()


def test_scores_each_posting_and_persists_screening_rows(conn):
    a = add_posting(conn, "ML Intern")
    b = add_posting(conn, "SWE Intern")
    llm = FakeLLM([screening(85, "strong"), screening(40, "weak")])

    summary = run_screener(conn, llm, settings=settings(), resume_text="RESUME", now=TS)

    got = rows(conn)
    assert [(r["posting_id"], r["model"], r["fit_score"], r["reason"]) for r in got] == [
        (a, "fake-model", 85.0, "strong"),
        (b, "fake-model", 40.0, "weak"),
    ]
    raw = json.loads(got[0]["raw_json"])
    assert raw["screening"]["matched_requirements"] == ["Python"]
    assert raw["usage"]["prompt_tokens"] == 100
    assert summary.scored == 2 and summary.failed == 0
    assert len(events(conn, "screener.scored")) == 2


def test_dry_run_returns_results_but_writes_nothing(conn):
    add_posting(conn, "ML Intern")
    llm = FakeLLM([screening(85)])

    summary = run_screener(conn, llm, settings=settings(), resume_text="R", dry_run=True, now=TS)

    assert summary.scored == 1
    assert summary.results[0].fit_score == 85
    assert rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_prefilter_skips_model_and_records_zero_row(conn):
    add_posting(conn, "VP, Research")
    llm = FakeLLM([])

    summary = run_screener(conn, llm, settings=settings(), resume_text="R", now=TS)

    assert llm.calls == []
    (row,) = rows(conn)
    assert (row["model"], row["fit_score"]) == ("prefilter", 0.0)
    assert "title" in row["reason"]
    assert summary.prefiltered == 1 and summary.scored == 0


def test_empty_pattern_list_screens_everything(conn):
    add_posting(conn, "VP, Research")
    llm = FakeLLM([screening(10)])

    run_screener(
        conn,
        llm,
        settings=settings(prefilter=PrefilterConfig(title_patterns=[])),
        resume_text="R",
        now=TS,
    )

    assert len(llm.calls) == 1


def test_malformed_output_is_retried_once_with_the_error_fed_back(conn):
    add_posting(conn, "ML Intern")
    llm = FakeLLM([LLMOutputError("schema violation: fit_score", "{bad"), screening(60)])

    summary = run_screener(conn, llm, settings=settings(), resume_text="R", now=TS)

    assert len(llm.calls) == 2
    assert "schema violation: fit_score" in llm.calls[1].user
    assert "{bad" in llm.calls[1].user
    assert summary.scored == 1
    assert rows(conn)[0]["fit_score"] == 60.0


def test_persistently_malformed_output_skips_posting_and_continues(conn):
    add_posting(conn, "ML Intern")
    b = add_posting(conn, "SWE Intern")
    llm = FakeLLM([LLMOutputError("bad", "x"), LLMOutputError("bad again", "y"), screening(75)])

    summary = run_screener(conn, llm, settings=settings(), resume_text="R", now=TS)

    assert summary.failed == 1 and summary.scored == 1
    assert [r["posting_id"] for r in rows(conn)] == [b]
    (ev,) = events(conn, "screener.failed")
    assert "bad again" in ev[0]


def test_transport_error_aborts_run_and_keeps_earlier_rows(conn):
    add_posting(conn, "ML Intern")
    add_posting(conn, "SWE Intern")
    llm = FakeLLM([screening(80), LLMTransportError("ollama down")])

    with pytest.raises(LLMTransportError):
        run_screener(conn, llm, settings=settings(), resume_text="R", now=TS)

    assert len(rows(conn)) == 1
    assert len(events(conn, "screener.aborted")) == 1


def test_already_screened_postings_are_skipped_unless_rescreen(conn):
    add_posting(conn, "ML Intern")
    run_screener(conn, FakeLLM([screening(80)]), settings=settings(), resume_text="R", now=TS)

    again = run_screener(conn, FakeLLM([]), settings=settings(), resume_text="R", now=TS)
    assert again.scored == 0

    third = run_screener(
        conn, FakeLLM([screening(90)]), settings=settings(), resume_text="R", rescreen=True, now=TS
    )
    assert third.scored == 1
    assert [r["fit_score"] for r in rows(conn)] == [80.0, 90.0]


def test_limit_caps_the_number_of_postings_considered(conn):
    for i in range(5):
        add_posting(conn, f"Intern {i}")
    llm = FakeLLM([screening(50)] * 2)

    summary = run_screener(conn, llm, settings=settings(), resume_text="R", limit=2, now=TS)

    assert summary.scored == 2 and len(llm.calls) == 2


def test_prompt_carries_resume_criteria_and_truncated_description(conn):
    add_posting(conn, "ML Intern", description="A" * 600 + "TAIL")
    llm = FakeLLM([screening(50)])

    run_screener(conn, llm, settings=settings(), resume_text="MY RESUME TEXT", now=TS)

    (call,) = llm.calls
    assert call.schema is Screening
    assert "Summer 2027" in call.system and "ML internship" in call.system
    assert "Montreal, QC" in call.system
    # Small models anchor on checklists and echo them back as findings. The
    # disqualifier list is applied in code and deliberately kept out of the prompt.
    assert "security clearance" not in call.system and "PhD" not in call.system
    assert "MY RESUME TEXT" in call.user
    assert "Scale AI" in call.user and "ML Intern" in call.user
    assert "TAIL" not in call.user  # cut at description_max_chars=500
    assert "truncated" in call.user.lower()


def test_disqualifier_pattern_caps_score_and_is_recorded(conn):
    """Hard disqualifiers are regexes over the posting, applied in code. A hit caps
    the stored score at 30 no matter what the model said, and the label is kept."""
    a = add_posting(conn, "ML Intern", description="Must hold an active security clearance.")
    b = add_posting(conn, "SWE Intern", description="Python, PyTorch, no clearance needed.")
    llm = FakeLLM([screening(95, "great"), screening(88, "good")])

    summary = run_screener(conn, llm, settings=settings(), resume_text="R", now=TS)

    got = rows(conn)
    assert [(r["posting_id"], r["fit_score"]) for r in got] == [(a, 30.0), (b, 88.0)]
    raw_a = json.loads(got[0]["raw_json"])
    assert raw_a["disqualifiers"] == ["security clearance"]
    assert raw_a["model_fit_score"] == 95
    assert json.loads(got[1]["raw_json"])["disqualifiers"] == []
    assert summary.results[0].fit_score == 30
    assert summary.results[0].disqualifiers == ["security clearance"]


def test_disqualifier_matching_is_case_insensitive_and_only_reads_the_posting(conn):
    add_posting(conn, "ML Intern", description="PHD REQUIRED for this role.")
    llm = FakeLLM([screening(80)])

    run_screener(conn, llm, settings=settings(), resume_text="I have a security clearance", now=TS)

    (row,) = rows(conn)
    assert json.loads(row["raw_json"])["disqualifiers"] == ["PhD required"]
    assert row["fit_score"] == 30.0
