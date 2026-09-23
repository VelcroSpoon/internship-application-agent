"""The nightly job.

The test that matters most here is the one asserting the scheduler never
drafts. Loop A on a timer is discovery; Loop B costs money and produces
something a person has to read, so it only ever fires on request.
"""

import json
from pathlib import Path

import httpx
import pytest

from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.llm.base import LLMTransportError
from internship_agent.scheduler import JOB_ID, run_discovery, start_discovery_scheduler
from internship_agent.screener.models import Screening
from tests.fakes import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_scaleai.json"


def screening(score: int = 80) -> Screening:
    return Screening(
        fit_score=score,
        reason="fits",
        is_internship=True,
        matched_requirements=["Python"],
        missing_requirements=[],
    )


@pytest.fixture
def paths(tmp_path, fake_resume):
    db = tmp_path / "sched.db"
    conn = connect(db)
    migrate(conn)
    conn.close()

    resume = tmp_path / "resume.md"
    resume.write_text(fake_resume, encoding="utf-8")
    config = tmp_path / "sources.toml"
    config.write_text(
        '[scout]\nuser_agent = "X/1 (+mailto:a@b)"\ndelay_seconds = 1.0\n'
        '[[scout.greenhouse]]\nboard = "scaleai"\ncompany = "Scale AI"\n'
        '[scheduler]\nhour = 3\nminute = 30\ntimezone = "America/Toronto"\n',
        encoding="utf-8",
    )
    criteria = tmp_path / "criteria.toml"
    criteria.write_text(
        f'[candidate]\nmaster_resume_path = "{resume.as_posix()}"\n'
        '[criteria]\ntarget_cycle = "Summer 2027"\n'
        '[prefilter]\ntitle_patterns = ["intern"]\n'
        '[screener]\nmodel = "fake"\n[writer]\nmodel = "fake"\n[critic]\nmodel = "fake"\n',
        encoding="utf-8",
    )
    return db, config, criteria


def board_client() -> httpx.Client:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /embed/\n")
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def counts_of(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- the job ----------------------------------------------------------------


def test_discovery_scouts_then_screens(paths):
    db, config, criteria = paths

    counts = run_discovery(
        db_path=db,
        config_path=config,
        criteria_path=criteria,
        client=board_client(),
        screener_backend=FakeLLM([screening()] * 4),
    )

    assert counts["scout_new"] == 4
    assert counts["screen_considered"] == 4
    conn = connect(db)
    assert counts_of(conn, "postings") == 4
    assert counts_of(conn, "screenings") == 4
    conn.close()


def test_the_nightly_job_never_drafts_an_application(paths):
    """The product boundary: discovery runs on a timer, drafting does not.
    Loop B costs money and produces text a person must read before it is
    worth anything, so it fires only on an explicit request."""
    db, config, criteria = paths

    run_discovery(
        db_path=db,
        config_path=config,
        criteria_path=criteria,
        client=board_client(),
        screener_backend=FakeLLM([screening(99)] * 4),
    )

    conn = connect(db)
    assert counts_of(conn, "applications") == 0
    assert counts_of(conn, "drafts") == 0
    assert counts_of(conn, "critiques") == 0
    kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM events")}
    assert not any(k.startswith(("writer.", "critic.", "loop.")) for k in kinds)
    conn.close()


def test_a_second_night_adds_no_duplicate_postings(paths):
    db, config, criteria = paths
    args = dict(db_path=db, config_path=config, criteria_path=criteria)
    run_discovery(**args, client=board_client(), screener_backend=FakeLLM([screening()] * 4))

    counts = run_discovery(
        **args, client=board_client(), screener_backend=FakeLLM([screening()] * 4)
    )

    assert counts["scout_new"] == 0 and counts["scout_seen"] == 4
    assert counts["screen_considered"] == 0  # already screened
    conn = connect(db)
    assert counts_of(conn, "postings") == 4
    conn.close()


def test_a_screener_outage_keeps_the_scouted_postings(paths):
    db, config, criteria = paths

    counts = run_discovery(
        db_path=db,
        config_path=config,
        criteria_path=criteria,
        client=board_client(),
        screener_backend=FakeLLM([LLMTransportError("ollama is not running")]),
    )

    assert counts["scout_new"] == 4
    conn = connect(db)
    assert counts_of(conn, "postings") == 4
    payload = conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'scheduler.partial'"
    ).fetchone()
    assert "ollama is not running" in payload[0]
    conn.close()


def test_a_dead_board_degrades_at_the_scout_layer_not_the_job(paths):
    """run_scout already isolates each source, so a network failure is one
    recorded source error and the night still screens whatever else arrived."""
    db, config, criteria = paths

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS exploded")

    counts = run_discovery(
        db_path=db,
        config_path=config,
        criteria_path=criteria,
        client=httpx.Client(transport=httpx.MockTransport(broken)),
        screener_backend=FakeLLM([]),
        sleep=lambda _: None,  # the scout retries a dead board; do not wait for it
    )

    assert counts["scout_errors"] == 1 and counts["scout_new"] == 0
    conn = connect(db)
    assert (
        conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'scout.source_retry'").fetchone()[0]
        == 2
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'scout.source_failed'").fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'scheduler.discovery_finished'"
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_an_unexpected_failure_is_swallowed_and_recorded(paths, tmp_path):
    """A scheduled job that raises dies quietly and stops running, so the
    night after that never happens either. It must record and return instead.
    Here the master resume has been moved out from under the config."""
    db, config, _ = paths
    criteria = tmp_path / "missing_resume.toml"
    criteria.write_text(
        '[candidate]\nmaster_resume_path = "/nope/not/here.md"\n'
        '[criteria]\ntarget_cycle = "Summer 2027"\n'
        '[screener]\nmodel = "fake"\n',
        encoding="utf-8",
    )

    counts = run_discovery(
        db_path=db,
        config_path=config,
        criteria_path=criteria,
        client=board_client(),
        screener_backend=FakeLLM([]),
    )

    assert counts["scout_new"] == 4  # the scouting still happened
    conn = connect(db)
    payload = conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'scheduler.failed'"
    ).fetchone()
    assert payload is not None and "FileNotFoundError" in payload[0]
    conn.close()


# --- registration -----------------------------------------------------------


def test_the_scheduler_registers_one_discovery_job_at_the_configured_time(paths):
    db, config, criteria = paths

    sched = start_discovery_scheduler(db_path=db, config_path=config, criteria_path=criteria)
    try:
        (job,) = sched.get_jobs()
        assert job.id == JOB_ID
        assert job.func is run_discovery
        assert str(job.trigger) == "cron[hour='3', minute='30']"
        assert job.next_run_time is not None
    finally:
        sched.shutdown(wait=False)


def test_disabling_the_scheduler_registers_nothing(paths, tmp_path):
    db, _, criteria = paths
    config = tmp_path / "off.toml"
    config.write_text(
        '[scout]\nuser_agent = "X/1"\n[scheduler]\nenabled = false\n', encoding="utf-8"
    )

    sched = start_discovery_scheduler(db_path=db, config_path=config, criteria_path=criteria)
    try:
        assert sched.get_jobs() == []
        assert sched.running
    finally:
        sched.shutdown(wait=False)
