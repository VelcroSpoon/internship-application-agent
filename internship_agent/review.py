"""The human approval gate.

The loop parks an application at 'awaiting_review' and stops there. Every
transition past that point is a person's decision, made through these
functions and recorded as an event.

'submitted' is the human writing down that they submitted the application
themselves. Nothing in this project sends anything to an employer: no forms,
no email, no browser automation. That is the product, not a missing feature.
"""

from __future__ import annotations

import sqlite3

from internship_agent.clock import now_iso
from internship_agent.db.events import log_event

# Only these moves are legal, and each one is a person's call.
ALLOWED: dict[str, set[str]] = {
    "approved": {"awaiting_review"},
    "rejected": {"awaiting_review"},
    "submitted": {"approved"},
}

EVENT_KIND = {
    "approved": "application.approved",
    "rejected": "application.rejected",
    "submitted": "application.marked_submitted",
}


class InvalidTransition(RuntimeError):
    pass


def approve(
    conn: sqlite3.Connection, application_id: int, *, note: str = "", now: str | None = None
) -> None:
    _transition(conn, application_id, "approved", note, now)


def reject(
    conn: sqlite3.Connection, application_id: int, *, note: str = "", now: str | None = None
) -> None:
    _transition(conn, application_id, "rejected", note, now)


def mark_submitted(
    conn: sqlite3.Connection, application_id: int, *, note: str = "", now: str | None = None
) -> None:
    """Record that the human submitted it. This does not submit anything."""
    _transition(conn, application_id, "submitted", note, now)


def _transition(
    conn: sqlite3.Connection, application_id: int, to: str, note: str, now: str | None
) -> None:
    ts = now or now_iso()
    row = conn.execute("SELECT status FROM applications WHERE id = ?", (application_id,)).fetchone()
    if row is None:
        raise LookupError(f"no application with id {application_id}")
    current = row["status"]
    if current not in ALLOWED[to]:
        allowed = " or ".join(sorted(ALLOWED[to]))
        raise InvalidTransition(
            f"application {application_id} is '{current}'; only {allowed} can become '{to}'"
        )
    conn.execute(
        "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?", (to, ts, application_id)
    )
    log_event(
        conn,
        EVENT_KIND[to],
        application_id=application_id,
        payload={"from": current, "note": note},
        ts=ts,
    )


LIST_SQL = """
SELECT a.id AS application_id, a.status, a.created_at, a.updated_at,
       p.id AS posting_id, p.company, p.title, p.location, p.url,
       (SELECT COUNT(*) FROM critiques c JOIN drafts d ON d.id = c.draft_id
        WHERE d.application_id = a.id) AS rounds,
       (SELECT c.overall FROM critiques c JOIN drafts d ON d.id = c.draft_id
        WHERE d.application_id = a.id ORDER BY c.round_index DESC LIMIT 1) AS final_overall,
       (SELECT c.overall FROM critiques c JOIN drafts d ON d.id = c.draft_id
        WHERE d.application_id = a.id ORDER BY c.round_index ASC LIMIT 1) AS first_overall,
       (SELECT c.unsupported_claim_count FROM critiques c JOIN drafts d ON d.id = c.draft_id
        WHERE d.application_id = a.id ORDER BY c.round_index DESC LIMIT 1) AS final_blockers
FROM applications a
JOIN postings p ON p.id = a.posting_id
WHERE (:status IS NULL OR a.status = :status)
ORDER BY a.updated_at DESC, a.id DESC
"""


def list_applications(conn: sqlite3.Connection, *, status: str | None = None) -> list[sqlite3.Row]:
    return conn.execute(LIST_SQL, {"status": status}).fetchall()
