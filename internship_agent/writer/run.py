"""Writer runner: one posting in, one round-0 draft out, persisted.

Creates the ``applications`` row on first use (status 'drafting'), refuses
to write a second round-0 draft for the same posting (revisions are the
Critic loop's job), and records VOICE tripwire hits alongside token usage
on the draft. ``dry_run`` performs the model call and returns the draft
without touching the database.

Nothing here submits anything. The output is text for a human to review.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from internship_agent.clock import now_iso
from internship_agent.config import VoiceConfig, WriterConfig
from internship_agent.db.events import log_event
from internship_agent.llm.base import LLMOutputError, StructuredLLM
from internship_agent.llm.retry import complete_with_retry
from internship_agent.writer.models import Draft
from internship_agent.writer.prompt import build_system, build_user
from internship_agent.writer.voice import find_voice_hits

log = logging.getLogger(__name__)


class AlreadyDrafted(RuntimeError):
    pass


@dataclass
class WriterResult:
    application_id: int | None
    draft_id: int | None
    round_index: int
    draft: Draft
    model: str
    usage: dict[str, Any]
    voice_hits: list[str] = field(default_factory=list)


def run_writer(
    conn: sqlite3.Connection,
    backend: StructuredLLM,
    *,
    posting_id: int,
    resume_text: str,
    voice: VoiceConfig,
    config: WriterConfig,
    dry_run: bool = False,
    now: str | None = None,
) -> WriterResult:
    ts = now or now_iso()
    posting = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if posting is None:
        raise LookupError(f"no posting with id {posting_id}")

    application_id: int | None = None
    if not dry_run:
        application_id = _get_or_create_application(conn, posting_id, ts)
        if _draft_count(conn, application_id):
            raise AlreadyDrafted(
                f"posting {posting_id} already has a draft; revisions go through the critic loop"
            )

    system = build_system(resume_text, voice)
    user = build_user(posting)
    try:
        response = complete_with_retry(
            backend, system=system, user=user, schema=Draft, max_attempts=config.max_attempts
        )
    except LLMOutputError as exc:
        log.warning("writer: posting %s produced no valid draft: %s", posting_id, exc)
        if not dry_run:
            log_event(
                conn,
                "writer.failed",
                posting_id=posting_id,
                application_id=application_id,
                payload={"error": str(exc), "raw_text": exc.raw_text[:1000]},
                ts=ts,
            )
        raise

    draft = response.parsed
    hits = find_voice_hits(draft.as_text(), voice)
    result = WriterResult(
        application_id=application_id,
        draft_id=None,
        round_index=0,
        draft=draft,
        model=response.model,
        usage=response.usage,
        voice_hits=hits,
    )
    if dry_run:
        return result

    assert application_id is not None
    cur = conn.execute(
        "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
        "writer_model, usage_json, created_at) VALUES (?, 0, ?, ?, ?, ?, ?)",
        (
            application_id,
            json.dumps([b.model_dump() for b in draft.bullets]),
            draft.cover_letter,
            response.model,
            json.dumps({"usage": response.usage, "voice_hits": hits}),
            ts,
        ),
    )
    result.draft_id = cur.lastrowid
    log_event(
        conn,
        "writer.drafted",
        posting_id=posting_id,
        application_id=application_id,
        draft_id=result.draft_id,
        payload={
            "round_index": 0,
            "model": response.model,
            "usage": response.usage,
            "voice_hits": hits,
        },
        ts=ts,
    )
    return result


def _get_or_create_application(conn: sqlite3.Connection, posting_id: int, ts: str) -> int:
    row = conn.execute(
        "SELECT id FROM applications WHERE posting_id = ? ORDER BY id LIMIT 1", (posting_id,)
    ).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO applications (posting_id, status, created_at, updated_at) "
        "VALUES (?, 'drafting', ?, ?)",
        (posting_id, ts, ts),
    )
    log_event(
        conn, "application.created", posting_id=posting_id, application_id=cur.lastrowid, ts=ts
    )
    return cur.lastrowid


def _draft_count(conn: sqlite3.Connection, application_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM drafts WHERE application_id = ?", (application_id,)
    ).fetchone()[0]
