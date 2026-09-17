"""Append-only event log. Every loop writes here; nothing reads it on the
hot path. It is the audit trail for "what did the agent do last night".
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from internship_agent.clock import now_iso


def log_event(
    conn: sqlite3.Connection,
    kind: str,
    *,
    posting_id: int | None = None,
    application_id: int | None = None,
    draft_id: int | None = None,
    payload: dict[str, Any] | None = None,
    ts: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO events (ts, kind, posting_id, application_id, draft_id, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ts or now_iso(),
            kind,
            posting_id,
            application_id,
            draft_id,
            json.dumps(payload) if payload is not None else None,
        ),
    )
    return cur.lastrowid
