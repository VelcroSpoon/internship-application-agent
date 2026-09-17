"""Greenhouse job board source.

Uses the official public JSON API rather than scraping HTML:
    GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true

Before fetching, robots.txt on the API host is read and honoured for our
User-Agent. Greenhouse currently only disallows /embed/, but the check is
cheap and the rule is "always ask". The description arrives as
HTML-entity-escaped HTML; it is unescaped and flattened to plain text here so
nothing downstream has to know about markup.
"""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib import robotparser

import httpx
from pydantic import ValidationError

from internship_agent.scout.models import PostingRecord

log = logging.getLogger(__name__)

API_BASE = "https://boards-api.greenhouse.io"


class RobotsDisallowed(RuntimeError):
    pass


# --- HTML -> text -------------------------------------------------------------

_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}
)
_BLANK_RUN = re.compile(r"\n\s*\n+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(escaped_html: str) -> str:
    """Greenhouse double-encodes: the JSON string holds entity-escaped HTML."""
    markup = html.unescape(escaped_html or "")
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    text = "".join(extractor.parts).replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    return _BLANK_RUN.sub("\n", "\n".join(lines)).strip()


# --- parsing ------------------------------------------------------------------


def parse_jobs(
    board: str, payload: dict[str, Any], company: str | None = None
) -> list[PostingRecord]:
    """Pure: API payload -> records. Jobs that fail validation are logged and skipped
    so one malformed entry never aborts a whole board."""
    records: list[PostingRecord] = []
    for job in payload.get("jobs", []):
        try:
            records.append(
                PostingRecord(
                    source=f"greenhouse:{board}",
                    external_id=str(job["id"]),
                    company=company or job.get("company_name") or board,
                    title=job["title"],
                    location=(job.get("location") or {}).get("name"),
                    url=job["absolute_url"],
                    description=html_to_text(job.get("content", "")),
                    posted_at=job.get("first_published") or job.get("updated_at"),
                    raw=job,
                )
            )
        except (KeyError, TypeError, ValidationError) as exc:
            log.warning("greenhouse:%s skipping job %r: %s", board, job.get("id"), exc)
    return records


# --- source -------------------------------------------------------------------


class GreenhouseSource:
    def __init__(self, board: str, user_agent: str, company: str | None = None) -> None:
        self.board = board
        self.company = company
        self.user_agent = user_agent
        self.name = f"greenhouse:{board}"

    @property
    def jobs_url(self) -> str:
        return f"{API_BASE}/v1/boards/{self.board}/jobs"

    def fetch(self, client: httpx.Client) -> list[PostingRecord]:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        self._check_robots(client, headers)
        resp = client.get(self.jobs_url, params={"content": "true"}, headers=headers, timeout=30)
        resp.raise_for_status()
        return parse_jobs(self.board, resp.json(), company=self.company)

    def _check_robots(self, client: httpx.Client, headers: dict[str, str]) -> None:
        resp = client.get(f"{API_BASE}/robots.txt", headers=headers, timeout=15)
        if resp.status_code >= 400:
            return  # no robots.txt (or unreadable) means no restrictions
        rp = robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        if not rp.can_fetch(self.user_agent, self.jobs_url):
            raise RobotsDisallowed(f"robots.txt forbids {self.jobs_url} for {self.user_agent!r}")
