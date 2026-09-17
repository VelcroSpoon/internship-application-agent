"""The drafting queue: what Loop A hands to the human.

One query, reused by the CLI now and the API later. "Latest screening wins"
is done with a correlated subquery on created_at rather than a window
function so it stays readable; the table is small.
"""

from __future__ import annotations

import sqlite3

QUEUE_SQL = """
SELECT p.id AS posting_id, p.company, p.title, p.location, p.url, p.source,
       p.posted_at, p.first_seen_at,
       s.fit_score, s.reason, s.model, s.created_at AS screened_at
FROM postings p
JOIN screenings s ON s.posting_id = p.id
WHERE p.status = 'active'
  AND s.id = (
      SELECT s2.id FROM screenings s2
      WHERE s2.posting_id = p.id
      ORDER BY s2.created_at DESC, s2.id DESC
      LIMIT 1
  )
  AND s.fit_score >= :threshold
  AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.posting_id = p.id)
ORDER BY s.fit_score DESC, p.first_seen_at DESC, p.id DESC
"""


def queue_postings(conn: sqlite3.Connection, *, threshold: float) -> list[sqlite3.Row]:
    return conn.execute(QUEUE_SQL, {"threshold": threshold}).fetchall()
