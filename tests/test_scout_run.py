"""Scout runner: fetches from sources, upserts postings, and the second run
over the same board must not create rows. Uses fake sources; the Greenhouse
adapter has its own tests."""

import json

import httpx
import pytest

from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.scout.models import PostingRecord
from internship_agent.scout.run import run_scout


class FakeSource:
    def __init__(self, name: str, records: list[PostingRecord] | Exception):
        self.name = name
        self._records = records

    def fetch(self, client: httpx.Client) -> list[PostingRecord]:
        if isinstance(self._records, Exception):
            raise self._records
        return self._records


def rec(
    external_id: str,
    title: str = "Software Engineering Intern (Summer 2027)",
    location: str = "San Francisco, CA",
    description: str = "Build things with Python.",
) -> PostingRecord:
    return PostingRecord(
        source="greenhouse:scaleai",
        external_id=external_id,
        company="Scale AI",
        title=title,
        location=location,
        url=f"https://example.test/jobs/{external_id}",
        description=description,
        posted_at="2026-09-01T00:00:00Z",
        raw={"id": int(external_id)},
    )


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def client():
    def deny(request: httpx.Request) -> httpx.Response:
        raise AssertionError("fake sources must not use the network")

    return httpx.Client(transport=httpx.MockTransport(deny))


def _run(conn, client, sources, **kw):
    kw.setdefault("delay_s", 0.0)
    kw.setdefault("sleep", lambda s: None)
    return run_scout(conn, sources, client=client, **kw)


def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]


def _events(conn, kind: str) -> list[dict]:
    rows = conn.execute("SELECT payload_json FROM events WHERE kind = ?", (kind,)).fetchall()
    return [json.loads(r[0]) for r in rows]


THREE = [
    rec("1", location="San Francisco, CA"),
    rec("2", location="London, UK"),
    rec("3", location="Doha, Qatar"),
]


def test_first_run_inserts_every_posting(conn, client):
    summary = _run(conn, client, [FakeSource("greenhouse:scaleai", THREE)])

    assert _count(conn) == 3
    assert (summary.new, summary.seen, summary.updated) == (3, 0, 0)
    assert _events(conn, "scout.run_finished") == [
        {"new": 3, "seen": 0, "updated": 0, "collisions": 0, "errors": 0, "sources": 1}
    ]


def test_second_run_with_cosmetic_variation_adds_no_rows(conn, client):
    _run(conn, client, [FakeSource("greenhouse:scaleai", THREE)], now="2026-09-01T00:00:00Z")
    varied = [
        rec(
            "1",
            title="software engineering intern - summer 2027 ",
            location="san francisco ca",
            description="Build things   with Python.",
        ),
        rec("2", location="London,  UK"),
        rec("3", location="Doha, Qatar "),
    ]

    summary = _run(
        conn, client, [FakeSource("greenhouse:scaleai", varied)], now="2026-09-02T00:00:00Z"
    )

    assert _count(conn) == 3
    assert (summary.new, summary.seen, summary.updated) == (0, 3, 0)
    rows = conn.execute("SELECT first_seen_at, last_seen_at FROM postings").fetchall()
    assert all(r["first_seen_at"] == "2026-09-01T00:00:00Z" for r in rows)
    assert all(r["last_seen_at"] == "2026-09-02T00:00:00Z" for r in rows)


def test_changed_description_updates_row_in_place(conn, client):
    _run(conn, client, [FakeSource("greenhouse:scaleai", [rec("1")])])

    summary = _run(
        conn, client, [FakeSource("greenhouse:scaleai", [rec("1", description="Now needs Rust.")])]
    )

    assert _count(conn) == 1
    assert (summary.new, summary.seen, summary.updated) == (0, 0, 1)
    assert conn.execute("SELECT description FROM postings").fetchone()[0] == "Now needs Rust."
    assert len(_events(conn, "posting.updated")) == 1


def test_retitled_req_with_same_external_id_is_rekeyed_not_duplicated(conn, client):
    _run(conn, client, [FakeSource("greenhouse:scaleai", [rec("1")])])

    summary = _run(
        conn, client, [FakeSource("greenhouse:scaleai", [rec("1", title="ML Engineering Intern")])]
    )

    assert _count(conn) == 1
    assert summary.updated == 1
    assert conn.execute("SELECT title FROM postings").fetchone()[0] == "ML Engineering Intern"
    assert len(_events(conn, "posting.rekeyed")) == 1


def test_distinct_req_with_identical_key_is_logged_as_collision(conn, client):
    """The spec's dedupe key collapses two reqs with the same company/title/location.
    That is accepted, but it must be visible, not silent."""
    _run(conn, client, [FakeSource("greenhouse:scaleai", [rec("1")])])

    summary = _run(conn, client, [FakeSource("greenhouse:scaleai", [rec("1"), rec("99")])])

    assert _count(conn) == 1
    assert summary.collisions == 1
    (payload,) = _events(conn, "posting.dedupe_collision")
    assert payload["existing_external_id"] == "1" and payload["incoming_external_id"] == "99"


def test_failing_source_is_recorded_and_does_not_abort_the_run(conn, client):
    sources = [
        FakeSource("greenhouse:broken", httpx.ConnectError("boom")),
        FakeSource("greenhouse:scaleai", THREE),
    ]

    summary = _run(conn, client, sources)

    assert _count(conn) == 3
    assert summary.errors == 1
    (payload,) = _events(conn, "scout.source_failed")
    assert payload["source"] == "greenhouse:broken"


def test_sleeps_between_sources_but_not_after_the_last(conn, client):
    slept: list[float] = []
    sources = [
        FakeSource("a", [rec("1")]),
        FakeSource("b", [rec("2", location="X")]),
        FakeSource("c", []),
    ]

    _run(conn, client, sources, delay_s=2.5, sleep=slept.append)

    assert slept == [2.5, 2.5]
