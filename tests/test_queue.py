"""The drafting queue: latest screening per posting, at or above threshold,
still active, and not yet turned into an application."""

import pytest

from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.screener.queue import queue_postings

TS = "2026-09-17T00:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def posting(conn, title: str, status: str = "active") -> int:
    cur = conn.execute(
        "INSERT INTO postings (dedupe_hash, source, company, title, location, url, "
        "first_seen_at, last_seen_at, status) VALUES (?, 's', 'Co', ?, 'SF', 'u', ?, ?, ?)",
        (f"h-{title}", title, TS, TS, status),
    )
    return cur.lastrowid


def screen(conn, posting_id: int, score: float, at: str, model: str = "m") -> None:
    conn.execute(
        "INSERT INTO screenings (posting_id, model, fit_score, reason, created_at) "
        "VALUES (?, ?, ?, 'r', ?)",
        (posting_id, model, score, at),
    )


def ids(rows) -> list[int]:
    return [r["posting_id"] for r in rows]


def test_only_postings_at_or_above_threshold_are_queued(conn):
    a, b, c = posting(conn, "A"), posting(conn, "B"), posting(conn, "C")
    screen(conn, a, 70, TS)
    screen(conn, b, 69.9, TS)
    screen(conn, c, 95, TS)

    assert ids(queue_postings(conn, threshold=70)) == [c, a]  # highest score first


def test_latest_screening_wins(conn):
    a = posting(conn, "A")
    screen(conn, a, 90, "2026-09-01T00:00:00Z")
    screen(conn, a, 30, "2026-09-02T00:00:00Z")

    assert queue_postings(conn, threshold=70) == []


def test_postings_with_an_application_are_excluded(conn):
    a, b = posting(conn, "A"), posting(conn, "B")
    screen(conn, a, 90, TS)
    screen(conn, b, 90, TS)
    conn.execute(
        "INSERT INTO applications (posting_id, status, created_at, updated_at) "
        "VALUES (?, 'drafting', ?, ?)",
        (a, TS, TS),
    )

    assert ids(queue_postings(conn, threshold=70)) == [b]


def test_dismissed_postings_are_excluded(conn):
    a = posting(conn, "A", status="dismissed")
    screen(conn, a, 90, TS)

    assert queue_postings(conn, threshold=70) == []


def test_unscreened_postings_are_not_queued(conn):
    posting(conn, "A")

    assert queue_postings(conn, threshold=0) == []


def test_queue_rows_carry_what_the_dashboard_needs(conn):
    a = posting(conn, "A")
    screen(conn, a, 88, TS)

    (row,) = queue_postings(conn, threshold=70)
    assert row["company"] == "Co" and row["title"] == "A" and row["url"] == "u"
    assert row["fit_score"] == 88 and row["reason"] == "r" and row["screened_at"] == TS
