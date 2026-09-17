"""Screener runner: one structured LLM call per posting, persisted as a
``screenings`` row.

Per posting:
1. Title pre-filter (regex, no model). A miss writes a zero-score row from
   the pseudo-model 'prefilter' so the decision is auditable and the posting
   is not reconsidered nightly.
2. Otherwise call the backend. On LLMOutputError, retry up to
   ``max_attempts`` with the validation error appended to the prompt. If
   every attempt fails, log ``screener.failed`` and move on: the posting is
   picked up again next run.
3. LLMTransportError aborts the whole run after logging ``screener.aborted``.
   If the server is down, every further call would fail identically.

``dry_run`` performs the calls and returns results but writes nothing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field

from internship_agent.clock import now_iso
from internship_agent.config import DISQUALIFIED_SCORE_CAP, CriteriaFile, Disqualifier
from internship_agent.db.events import log_event
from internship_agent.llm.base import LLMOutputError, LLMResponse, LLMTransportError, StructuredLLM
from internship_agent.llm.retry import complete_with_retry
from internship_agent.screener.models import Screening
from internship_agent.screener.prompt import build_system, build_user

log = logging.getLogger(__name__)

PREFILTER_MODEL = "prefilter"


@dataclass
class ScreenResult:
    posting_id: int
    company: str
    title: str
    location: str | None
    model: str
    fit_score: int  # after any disqualifier cap
    reason: str
    is_internship: bool
    disqualifiers: list[str] = field(default_factory=list)


def find_disqualifiers(description: str | None, rules: list[Disqualifier]) -> list[str]:
    """Labels of every hard-disqualifier regex that matches the posting text."""
    text = description or ""
    return [d.label for d in rules if d.compiled().search(text)]


@dataclass
class ScreenerSummary:
    considered: int = 0
    scored: int = 0
    prefiltered: int = 0
    failed: int = 0
    results: list[ScreenResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "scored": self.scored,
            "prefiltered": self.prefiltered,
            "failed": self.failed,
        }


def select_postings(
    conn: sqlite3.Connection, *, limit: int | None, rescreen: bool
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM postings p WHERE p.status = 'active'"
    if not rescreen:
        sql += " AND NOT EXISTS (SELECT 1 FROM screenings s WHERE s.posting_id = p.id)"
    sql += " ORDER BY p.id ASC"  # discovery order: deterministic and easy to reason about
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def run_screener(
    conn: sqlite3.Connection,
    backend: StructuredLLM,
    *,
    settings: CriteriaFile,
    resume_text: str,
    dry_run: bool = False,
    limit: int | None = None,
    rescreen: bool = False,
    now: str | None = None,
) -> ScreenerSummary:
    ts = now or now_iso()
    patterns = settings.prefilter.compiled()
    system = build_system(settings.criteria)
    summary = ScreenerSummary()
    postings = select_postings(conn, limit=limit, rescreen=rescreen)
    summary.considered = len(postings)

    if not dry_run:
        log_event(conn, "screener.run_started", payload={"considered": len(postings)}, ts=ts)

    for posting in postings:
        if patterns and not any(p.search(posting["title"]) for p in patterns):
            summary.prefiltered += 1
            result = ScreenResult(
                posting_id=posting["id"],
                company=posting["company"],
                title=posting["title"],
                location=posting["location"],
                model=PREFILTER_MODEL,
                fit_score=0,
                reason="title matched no prefilter pattern",
                is_internship=False,
            )
            summary.results.append(result)
            if not dry_run:
                _persist_prefilter(conn, posting["id"], result.reason, ts)
            continue

        user = build_user(posting, resume_text, settings.screener.description_max_chars)
        try:
            response = complete_with_retry(
                backend,
                system=system,
                user=user,
                schema=Screening,
                max_attempts=settings.screener.max_attempts,
            )
        except LLMOutputError as exc:
            summary.failed += 1
            log.warning("screener: posting %s failed validation: %s", posting["id"], exc)
            if not dry_run:
                log_event(
                    conn,
                    "screener.failed",
                    posting_id=posting["id"],
                    payload={"error": str(exc), "raw_text": exc.raw_text[:1000]},
                    ts=ts,
                )
            continue
        except LLMTransportError as exc:
            log.error("screener: backend unreachable, aborting: %s", exc)
            if not dry_run:
                log_event(
                    conn, "screener.aborted", payload={"error": str(exc), **summary.counts()}, ts=ts
                )
            raise

        s = response.parsed
        summary.scored += 1
        disqualifiers = find_disqualifiers(
            posting["description"], settings.criteria.hard_disqualifiers
        )
        fit_score = min(s.fit_score, DISQUALIFIED_SCORE_CAP) if disqualifiers else s.fit_score
        result = ScreenResult(
            posting_id=posting["id"],
            company=posting["company"],
            title=posting["title"],
            location=posting["location"],
            model=response.model,
            fit_score=fit_score,
            reason=s.reason,
            is_internship=s.is_internship,
            disqualifiers=disqualifiers,
        )
        summary.results.append(result)
        if not dry_run:
            _persist_screening(conn, posting["id"], response, result, ts)

    if not dry_run:
        log_event(conn, "screener.run_finished", payload=summary.counts(), ts=ts)
    return summary


def _persist_prefilter(conn: sqlite3.Connection, posting_id: int, reason: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO screenings (posting_id, model, fit_score, reason, raw_json, created_at) "
        "VALUES (?, ?, 0, ?, NULL, ?)",
        (posting_id, PREFILTER_MODEL, reason, ts),
    )
    log_event(conn, "screener.prefiltered", posting_id=posting_id, ts=ts)


def _persist_screening(
    conn: sqlite3.Connection,
    posting_id: int,
    response: LLMResponse[Screening],
    result: ScreenResult,
    ts: str,
) -> None:
    s = response.parsed
    raw = {
        "screening": s.model_dump(),
        "model_fit_score": s.fit_score,  # pre-cap, so the cap's effect is visible
        "disqualifiers": result.disqualifiers,
        "usage": response.usage,
        "raw_text": response.raw_text,
    }
    cur = conn.execute(
        "INSERT INTO screenings (posting_id, model, fit_score, reason, raw_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (posting_id, response.model, result.fit_score, s.reason, json.dumps(raw), ts),
    )
    log_event(
        conn,
        "screener.scored",
        posting_id=posting_id,
        payload={
            "screening_id": cur.lastrowid,
            "fit_score": result.fit_score,
            "model_fit_score": s.fit_score,
            "disqualifiers": result.disqualifiers,
            "model": response.model,
            "usage": response.usage,
        },
        ts=ts,
    )
