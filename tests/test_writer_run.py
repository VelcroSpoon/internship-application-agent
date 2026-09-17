"""Writer runner against the scripted fake model: one posting in, one
round-0 draft out, persisted with its application, usage, and voice hits."""

import json

import pytest

from internship_agent.config import VoiceConfig, WriterConfig
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.llm.base import LLMOutputError
from internship_agent.writer.models import Bullet, Draft
from internship_agent.writer.run import AlreadyDrafted, run_writer
from tests.fakes import FakeLLM

TS = "2026-09-17T00:00:00Z"
LETTER = "I built a MinHash deduper in pandas for 40k tickets. " * 8


def draft(letter: str = LETTER) -> Draft:
    return Draft(
        bullets=[
            Bullet(text=f"Bullet {i} about PyTorch.", resume_anchor=f"Resume line {i}")
            for i in range(3)
        ],
        cover_letter=letter,
    )


def voice() -> VoiceConfig:
    return VoiceConfig(
        banned_phrases=["passionate about"],
        banned_patterns=[" — "],
        style_notes=["Plain sentences."],
    )


def writer_cfg() -> WriterConfig:
    return WriterConfig(backend="anthropic", model="fake", max_attempts=2)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def add_posting(conn, title="ML Intern", description="Needs PyTorch and Python.") -> int:
    cur = conn.execute(
        "INSERT INTO postings (dedupe_hash, source, external_id, company, title, location, url, "
        "description, content_hash, first_seen_at, last_seen_at) "
        "VALUES (?, 'greenhouse:x', ?, 'Scale AI', ?, 'Montreal, QC', 'https://x', ?, 'h', ?, ?)",
        (f"h-{title}", title, title, description, TS, TS),
    )
    return cur.lastrowid


def _run(conn, llm, posting_id, **kw):
    kw.setdefault("resume_text", "RESUME TEXT")
    kw.setdefault("voice", voice())
    kw.setdefault("config", writer_cfg())
    kw.setdefault("now", TS)
    return run_writer(conn, llm, posting_id=posting_id, **kw)


def test_creates_application_and_round_zero_draft(conn):
    pid = add_posting(conn)
    llm = FakeLLM([draft()])

    result = _run(conn, llm, pid)

    app = conn.execute("SELECT * FROM applications").fetchone()
    assert app["posting_id"] == pid and app["status"] == "drafting"
    row = conn.execute("SELECT * FROM drafts").fetchone()
    assert row["application_id"] == app["id"] and row["round_index"] == 0
    assert row["writer_model"] == "fake-model"
    assert json.loads(row["bullets_json"])[0] == {
        "text": "Bullet 0 about PyTorch.",
        "resume_anchor": "Resume line 0",
    }
    assert row["cover_letter"] == LETTER
    usage = json.loads(row["usage_json"])
    assert usage["usage"]["prompt_tokens"] == 100 and usage["voice_hits"] == []
    assert result.application_id == app["id"] and result.draft_id == row["id"]
    assert result.round_index == 0
    kinds = [r[0] for r in conn.execute("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["application.created", "writer.drafted"]


def test_dry_run_returns_draft_and_writes_nothing(conn):
    pid = add_posting(conn)

    result = _run(conn, FakeLLM([draft()]), pid, dry_run=True)

    assert result.draft.cover_letter == LETTER and result.draft_id is None
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_second_draft_for_same_posting_is_refused(conn):
    pid = add_posting(conn)
    _run(conn, FakeLLM([draft()]), pid)

    with pytest.raises(AlreadyDrafted):
        _run(conn, FakeLLM([draft()]), pid)
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1


def test_existing_application_without_drafts_is_reused(conn):
    pid = add_posting(conn)
    conn.execute(
        "INSERT INTO applications (posting_id, status, created_at, updated_at) "
        "VALUES (?, 'drafting', ?, ?)",
        (pid, TS, TS),
    )

    result = _run(conn, FakeLLM([draft()]), pid)

    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    assert result.application_id == 1


def test_voice_hits_are_recorded_not_rewritten(conn):
    pid = add_posting(conn)
    bad = "I am passionate about ML — truly. " * 10
    llm = FakeLLM([draft(letter=bad)])

    result = _run(conn, llm, pid)

    assert result.voice_hits == ["phrase: passionate about", "pattern:  — "]
    row = conn.execute("SELECT cover_letter, usage_json FROM drafts").fetchone()
    assert row["cover_letter"] == bad
    assert json.loads(row["usage_json"])["voice_hits"] == result.voice_hits


def test_malformed_output_is_retried_then_raises_with_event(conn):
    pid = add_posting(conn)
    llm = FakeLLM([LLMOutputError("bad", "{"), LLMOutputError("bad again", "{{")])

    with pytest.raises(LLMOutputError):
        _run(conn, llm, pid)

    assert len(llm.calls) == 2 and "bad" in llm.calls[1].user
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    kinds = [r[0] for r in conn.execute("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["application.created", "writer.failed"]


def test_unknown_posting_raises(conn):
    with pytest.raises(LookupError):
        _run(conn, FakeLLM([]), 999)


def test_prompt_carries_resume_voice_rules_and_posting(conn):
    pid = add_posting(conn, description="Requires PyTorch and MongoDB.")
    llm = FakeLLM([draft()])

    _run(conn, llm, pid, resume_text="MY RESUME")

    (call,) = llm.calls
    assert call.schema is Draft
    assert "MY RESUME" in call.system
    assert "passionate about" in call.system and "Plain sentences." in call.system
    assert "Scale AI" in call.user and "ML Intern" in call.user
    assert "Requires PyTorch and MongoDB." in call.user
    # The stable material (resume, rules) is in system so it caches; the posting is not.
    assert "Requires PyTorch" not in call.system
