"""Writer runner: drafts round 0 for a posting, and revises for rounds 1+.

``run_writer`` is the standalone entry point: it creates the application,
writes the round-0 draft, and refuses to write a second one. ``write_revision``
is what the orchestrator calls for each later round, given the previous draft
and the Critique that prompted the revision.

Both persist through the same path, so every draft row carries its round
index, model, token usage, VOICE hits, and (for revisions) any Critic text
that leaked into the prose.

Nothing here submits anything. The output is text for a human to review.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from internship_agent.agents.critic import Critique
from internship_agent.clock import now_iso
from internship_agent.config import VoiceConfig, WriterConfig
from internship_agent.critic.leaks import find_critique_leaks
from internship_agent.db.events import log_event
from internship_agent.llm.base import LLMOutputError, LLMResponse, StructuredLLM
from internship_agent.llm.retry import complete_with_retry
from internship_agent.writer.models import Draft
from internship_agent.writer.prompt import build_revision_user, build_system, build_user
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
    critique_leaks: list[str] = field(default_factory=list)


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
        application_id = get_or_create_application(conn, posting_id, ts)
        if draft_count(conn, application_id):
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
                payload={"error": str(exc), "raw_text": exc.raw_text[:1000], "round_index": 0},
                ts=ts,
            )
        raise

    return _finish(
        conn,
        response=response,
        application_id=application_id,
        posting_id=posting_id,
        round_index=0,
        voice=voice,
        critique=None,
        dry_run=dry_run,
        event="writer.drafted",
        ts=ts,
    )


def write_revision(
    conn: sqlite3.Connection,
    backend: StructuredLLM,
    *,
    application_id: int,
    posting: sqlite3.Row,
    previous_draft: Draft,
    critique: Critique,
    round_index: int,
    resume_text: str,
    voice: VoiceConfig,
    config: WriterConfig,
    dry_run: bool = False,
    now: str | None = None,
) -> WriterResult:
    ts = now or now_iso()
    system = build_system(resume_text, voice)
    user = build_revision_user(posting, previous_draft, critique)
    try:
        response = complete_with_retry(
            backend, system=system, user=user, schema=Draft, max_attempts=config.max_attempts
        )
    except LLMOutputError as exc:
        log.warning("writer: revision %s failed validation: %s", round_index, exc)
        if not dry_run:
            log_event(
                conn,
                "writer.failed",
                posting_id=posting["id"],
                application_id=application_id,
                payload={
                    "error": str(exc),
                    "raw_text": exc.raw_text[:1000],
                    "round_index": round_index,
                },
                ts=ts,
            )
        raise

    return _finish(
        conn,
        response=response,
        application_id=application_id,
        posting_id=posting["id"],
        round_index=round_index,
        voice=voice,
        critique=critique,
        dry_run=dry_run,
        event="writer.revised",
        ts=ts,
    )


# --- shared -------------------------------------------------------------------


def _finish(
    conn: sqlite3.Connection,
    *,
    response: LLMResponse[Draft],
    application_id: int | None,
    posting_id: int,
    round_index: int,
    voice: VoiceConfig,
    critique: Critique | None,
    dry_run: bool,
    event: str,
    ts: str,
) -> WriterResult:
    draft = response.parsed
    text = draft.as_text()
    result = WriterResult(
        application_id=application_id,
        draft_id=None,
        round_index=round_index,
        draft=draft,
        model=response.model,
        usage=response.usage,
        voice_hits=find_voice_hits(text, voice),
        critique_leaks=find_critique_leaks(text, critique) if critique else [],
    )
    if dry_run:
        return result

    assert application_id is not None
    cur = conn.execute(
        "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
        "writer_model, usage_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            application_id,
            round_index,
            json.dumps([b.model_dump() for b in draft.bullets]),
            draft.cover_letter,
            response.model,
            json.dumps(
                {
                    "usage": response.usage,
                    "voice_hits": result.voice_hits,
                    "critique_leaks": result.critique_leaks,
                }
            ),
            ts,
        ),
    )
    result.draft_id = cur.lastrowid
    log_event(
        conn,
        event,
        posting_id=posting_id,
        application_id=application_id,
        draft_id=result.draft_id,
        payload={
            "round_index": round_index,
            "model": response.model,
            "usage": response.usage,
            "voice_hits": result.voice_hits,
            "critique_leaks": result.critique_leaks,
        },
        ts=ts,
    )
    return result


def get_or_create_application(conn: sqlite3.Connection, posting_id: int, ts: str) -> int:
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


def draft_count(conn: sqlite3.Connection, application_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM drafts WHERE application_id = ?", (application_id,)
    ).fetchone()[0]
