"""Greenhouse board source. Parsing is pure and tested against a captured
API response; HTTP behaviour is tested through httpx's MockTransport so
robots.txt compliance and the User-Agent header are asserted, not assumed."""

import json
from pathlib import Path

import httpx
import pytest

from internship_agent.scout.greenhouse import (
    GreenhouseSource,
    RobotsDisallowed,
    html_to_text,
    parse_jobs,
)
from internship_agent.scout.models import PostingRecord

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_scaleai.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- parsing ----------------------------------------------------------------


def test_parse_jobs_returns_one_record_per_job(payload):
    records = parse_jobs("scaleai", payload)
    assert len(records) == 4
    assert all(isinstance(r, PostingRecord) for r in records)


def test_parse_jobs_maps_board_fields(payload):
    rec = next(r for r in parse_jobs("scaleai", payload) if r.external_id == "4730845005")
    assert rec.source == "greenhouse:scaleai"
    assert rec.company == "Scale AI"
    assert rec.title == "Software Engineering Intern (Summer 2027)"  # trailing space stripped
    assert rec.location == "San Francisco, CA"
    assert rec.url == "https://job-boards.greenhouse.io/scaleai/jobs/4730845005"
    assert rec.posted_at is not None and rec.posted_at.startswith("20")


def test_parse_jobs_description_is_plain_text(payload):
    rec = parse_jobs("scaleai", payload)[0]
    assert "<" not in rec.description and "&lt;" not in rec.description
    assert "&amp;" not in rec.description
    assert len(rec.description) > 500


def test_parse_jobs_keeps_raw_payload_for_reprocessing(payload):
    rec = parse_jobs("scaleai", payload)[0]
    assert rec.raw["id"] == int(rec.external_id)


def test_parse_jobs_company_override_wins_over_board_name(payload):
    rec = parse_jobs("scaleai", payload, company="Scale")[0]
    assert rec.company == "Scale"


def test_parse_jobs_skips_jobs_missing_required_fields(payload):
    payload["jobs"].append({"id": 1, "title": None})
    records = parse_jobs("scaleai", payload)
    assert len(records) == 4


def test_html_to_text_unescapes_and_strips_tags():
    raw = (
        "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;li&gt;Two&lt;/li&gt;&lt;/ul&gt;"
    )
    assert html_to_text(raw) == "Hello & welcome\nOne\nTwo"


def test_html_to_text_collapses_runs_of_blank_lines():
    raw = "&lt;p&gt;A&lt;/p&gt;&lt;p&gt;&lt;/p&gt;&lt;br&gt;&lt;br&gt;&lt;p&gt;B&lt;/p&gt;"
    assert html_to_text(raw) == "A\nB"


# --- HTTP behaviour ---------------------------------------------------------


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_hits_boards_api_with_content_and_real_user_agent(payload):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /embed/\n")
        return httpx.Response(200, json=payload)

    source = GreenhouseSource("scaleai", user_agent="InternshipAgent/0.1 (+mailto:me@x)")
    records = source.fetch(client=_client(handler))

    assert len(records) == 4
    jobs_req = next(r for r in seen if r.url.path.startswith("/v1/boards/"))
    assert jobs_req.url.host == "boards-api.greenhouse.io"
    assert jobs_req.url.path == "/v1/boards/scaleai/jobs"
    assert jobs_req.url.params["content"] == "true"
    assert jobs_req.headers["user-agent"] == "InternshipAgent/0.1 (+mailto:me@x)"


def test_fetch_refuses_when_robots_disallows_the_endpoint(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /v1/\n")
        raise AssertionError("jobs endpoint must not be requested when disallowed")

    source = GreenhouseSource("scaleai", user_agent="InternshipAgent/0.1")
    with pytest.raises(RobotsDisallowed):
        source.fetch(client=_client(handler))


def test_fetch_treats_missing_robots_as_allow(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    source = GreenhouseSource("scaleai", user_agent="InternshipAgent/0.1")
    assert len(source.fetch(client=_client(handler))) == 4


def test_fetch_raises_on_http_error(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503)

    source = GreenhouseSource("scaleai", user_agent="InternshipAgent/0.1")
    with pytest.raises(httpx.HTTPStatusError):
        source.fetch(client=_client(handler))
