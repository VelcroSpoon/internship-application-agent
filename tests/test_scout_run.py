"""Scout runner: fetches from sources, upserts postings, and the second run
over the same board must not create rows. Uses fake sources; the Greenhouse
adapter has its own tests."""

import json

import httpx
import pytest

from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.scout.models import PostingRecord
from internship_agent.scout.run import RETRY_DELAYS, run_scout


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


# --- retries ----------------------------------------------------------------


class FlakySource:
    """Raises the scripted errors in order, then returns records."""

    def __init__(self, name: str, errors: list[Exception], records: list[PostingRecord]):
        self.name = name
        self._errors = list(errors)
        self._records = records
        self.calls = 0

    def fetch(self, client: httpx.Client) -> list[PostingRecord]:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._records


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://boards-api.greenhouse.io/v1/boards/x/jobs")
    return httpx.HTTPStatusError(
        f"{code}", request=request, response=httpx.Response(code, request=request)
    )


def test_a_network_blip_is_retried_and_the_board_still_arrives(conn, client):
    """A DNS failure at 3am used to lose that board until the next night."""
    source = FlakySource("greenhouse:scaleai", [httpx.ConnectError("dns")], THREE)
    slept: list[float] = []

    summary = _run(conn, client, [source], sleep=slept.append)

    assert source.calls == 2
    assert summary.new == 3 and summary.errors == 0
    assert slept == [RETRY_DELAYS[0]]
    (payload,) = _events(conn, "scout.source_retry")
    assert payload["attempt"] == 1 and "dns" in payload["error"]


def test_a_server_error_is_retried(conn, client):
    source = FlakySource("greenhouse:scaleai", [_status_error(503)], THREE)

    summary = _run(conn, client, [source])

    assert source.calls == 2 and summary.new == 3


def test_a_board_that_stays_down_fails_after_the_last_retry(conn, client):
    errors = [httpx.ConnectError("down")] * (len(RETRY_DELAYS) + 1)
    source = FlakySource("greenhouse:scaleai", errors, THREE)

    summary = _run(conn, client, [source])

    assert source.calls == len(RETRY_DELAYS) + 1
    assert summary.errors == 1 and summary.new == 0
    assert len(_events(conn, "scout.source_retry")) == len(RETRY_DELAYS)
    assert len(_events(conn, "scout.source_failed")) == 1


def test_a_client_error_is_not_retried(conn, client):
    """A 404 means the board token is wrong. Asking again will not fix it."""
    source = FlakySource("greenhouse:nope", [_status_error(404)], THREE)

    summary = _run(conn, client, [source])

    assert source.calls == 1 and summary.errors == 1
    assert _events(conn, "scout.source_retry") == []


def test_robots_refusal_is_not_retried(conn, client):
    from internship_agent.scout.greenhouse import RobotsDisallowed

    source = FlakySource("greenhouse:scaleai", [RobotsDisallowed("no")], THREE)

    summary = _run(conn, client, [source])

    assert source.calls == 1 and summary.errors == 1
