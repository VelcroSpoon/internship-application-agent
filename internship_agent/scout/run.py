"""Scout runner: fetch every configured source, upsert into ``postings``.

Upsert resolution order for an incoming record:

1. Match on ``dedupe_hash`` (company|title|location). Same posting.
   - external_id differs from the stored one -> two distinct reqs share the
     key. Kept as one row (the spec's choice) but logged as a collision.
   - description changed -> update text in place, log ``posting.updated``.
   - otherwise -> bump ``last_seen_at`` only.
2. Else match on ``(source, external_id)``. Same req, new title or location:
   re-key the row to the new hash and log ``posting.rekeyed``. Without this
   a retitled req would fail the unique index forever.
3. Else insert.

Each source is one transaction. A source that raises is logged and skipped;
the run continues with the next one.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

import httpx

from internship_agent.clock import now_iso
from internship_agent.db.events import log_event
from internship_agent.scout.base import Source
from internship_agent.scout.models import PostingRecord

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    new: int = 0
    seen: int = 0
    updated: int = 0
    collisions: int = 0
    errors: int = 0
    sources: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def run_scout(
    conn: sqlite3.Connection,
    sources: Sequence[Source],
    *,
    client: httpx.Client,
    delay_s: float,
    sleep: Callable[[float], None] = time.sleep,
    now: str | None = None,
) -> RunSummary:
    ts = now or now_iso()
    summary = RunSummary(sources=len(sources))
    log_event(conn, "scout.run_started", payload={"sources": [s.name for s in sources]}, ts=ts)

    for i, source in enumerate(sources):
        if i > 0:
            sleep(delay_s)
        try:
            records = source.fetch(client)
        except Exception as exc:  # any one board failing must not kill the nightly run
            log.warning("scout: source %s failed: %s", source.name, exc)
            summary.errors += 1
            log_event(
                conn,
                "scout.source_failed",
                payload={"source": source.name, "error": f"{type(exc).__name__}: {exc}"},
                ts=ts,
            )
            continue

        conn.execute("BEGIN")
        try:
            for record in records:
                outcome = upsert_posting(conn, record, ts)
                setattr(summary, outcome, getattr(summary, outcome) + 1)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    log_event(conn, "scout.run_finished", payload=summary.as_dict(), ts=ts)
    return summary


def upsert_posting(conn: sqlite3.Connection, rec: PostingRecord, ts: str) -> str:
    """Returns one of 'new', 'seen', 'updated', 'collisions' (a RunSummary field)."""
    dh, ch = rec.dedupe_hash, rec.content_hash

    row = conn.execute(
        "SELECT id, external_id, content_hash FROM postings WHERE dedupe_hash = ?", (dh,)
    ).fetchone()
    if row is not None:
        if rec.external_id is not None and row["external_id"] not in (None, rec.external_id):
            log_event(
                conn,
                "posting.dedupe_collision",
                posting_id=row["id"],
                payload={
                    "existing_external_id": row["external_id"],
                    "incoming_external_id": rec.external_id,
                    "incoming_url": rec.url,
                },
                ts=ts,
            )
            conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?", (ts, row["id"]))
            return "collisions"
        if row["content_hash"] != ch:
            _update_content(conn, row["id"], rec, ch, ts)
            log_event(
                conn,
                "posting.updated",
                posting_id=row["id"],
                payload={"old_content_hash": row["content_hash"], "new_content_hash": ch},
                ts=ts,
            )
            return "updated"
        conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?", (ts, row["id"]))
        return "seen"

    if rec.external_id is not None:
        row = conn.execute(
            "SELECT id, dedupe_hash, title, location FROM postings "
            "WHERE source = ? AND external_id = ?",
            (rec.source, rec.external_id),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE postings SET dedupe_hash = ?, company = ?, title = ?, location = ? "
                "WHERE id = ?",
                (dh, rec.company, rec.title, rec.location, row["id"]),
            )
            _update_content(conn, row["id"], rec, ch, ts)
            log_event(
                conn,
                "posting.rekeyed",
                posting_id=row["id"],
                payload={
                    "old": {"title": row["title"], "location": row["location"]},
                    "new": {"title": rec.title, "location": rec.location},
                },
                ts=ts,
            )
            return "updated"

    cur = conn.execute(
        "INSERT INTO postings (dedupe_hash, source, external_id, company, title, location, "
        "url, description, content_hash, raw_json, posted_at, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dh,
            rec.source,
            rec.external_id,
            rec.company,
            rec.title,
            rec.location,
            rec.url,
            rec.description,
            ch,
            json.dumps(rec.raw),
            rec.posted_at,
            ts,
            ts,
        ),
    )
    log_event(conn, "posting.new", posting_id=cur.lastrowid, ts=ts)
    return "new"


def _update_content(
    conn: sqlite3.Connection, posting_id: int, rec: PostingRecord, ch: str, ts: str
) -> None:
    conn.execute(
        "UPDATE postings SET description = ?, content_hash = ?, raw_json = ?, url = ?, "
        "posted_at = COALESCE(?, posted_at), last_seen_at = ? WHERE id = ?",
        (rec.description, ch, json.dumps(rec.raw), rec.url, rec.posted_at, ts, posting_id),
    )
