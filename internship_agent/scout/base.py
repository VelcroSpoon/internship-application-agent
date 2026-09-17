"""Source interface. A source knows how to fetch one board and turn it into
PostingRecords. It does not touch the database; the runner does that."""

from __future__ import annotations

from typing import Protocol

import httpx

from internship_agent.scout.models import PostingRecord


class Source(Protocol):
    name: str  # e.g. 'greenhouse:scaleai'

    def fetch(self, client: httpx.Client) -> list[PostingRecord]: ...
