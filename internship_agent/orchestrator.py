"""The Writer/Critic revision loop.

    draft -> critique -> (stop?) -> revise -> critique -> ... -> human review

The orchestrator owns the stop decision, and it makes that decision by
calling ``should_continue`` from the spec file. Nothing here re-implements
a stop condition; ``describe_stop`` only labels which one fired, and a test
holds it to agreeing with ``should_continue`` on every history.

Every round is persisted before the next begins: the draft, its full
Critique, and the per-dimension scores. If the process dies mid-loop, what
finished is still on disk.

Model failures do not propagate. A Critic that will not return valid JSON,
a Writer that fails a revision, a backend that is unreachable: each stops
the loop, is recorded as an event, and leaves the application in a state a
human can open. The one exception is a first draft that never appears, in
which case there is nothing to review and the application stays 'drafting'.

The loop ends at a human gate. It never advances past 'awaiting_review' on
its own, and nothing in this project submits an application.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from internship_agent.agents.critic import (
    ACCEPT_THRESHOLD,
    MAX_ROUNDS,
    MIN_DELTA,
    Critique,
    should_continue,
)
from internship_agent.clock import now_iso
from internship_agent.config import CriteriaFile, VoiceConfig
from internship_agent.critic.run import run_critic
from internship_agent.db.events import log_event
from internship_agent.llm.base import LLMOutputError, LLMTransportError, StructuredLLM
from internship_agent.writer.models import Bullet, Draft
from internship_agent.writer.run import (
    draft_count,
    get_or_create_application,
    run_writer,
    write_revision,
)

log = logging.getLogger(__name__)

# The loop only runs on an application still being drafted. Anything further
# along belongs to the human.
RESUMABLE_STATUS = "drafting"


class LoopRefused(RuntimeError):
    pass


@dataclass
class LoopResult:
    application_id: int | None
    rounds_completed: int
    stopped_because: str
    history: list[Critique] = field(default_factory=list)
    final_overall: float | None = None
    final_unsupported_claims: int | None = None
    usage: list[dict[str, Any]] = field(default_factory=list)


def describe_stop(history: list[Critique]) -> str:
    """Which gate fired, in ``should_continue``'s own order.

    Reporting only. A test asserts this returns 'continuing' exactly when
    ``should_continue`` returns True, so the two cannot drift apart.
    """
    latest = history[-1]
    if latest.unsupported_claim_count > 0:
        if latest.round_index + 1 < MAX_ROUNDS:
            return "continuing"
        return "blockers_unresolved_at_round_cap"
    if latest.round_index + 1 >= MAX_ROUNDS:
        return "round_cap"
    if latest.overall >= ACCEPT_THRESHOLD:
        return "quality_bar"
    if len(history) >= 2 and (latest.overall - history[-2].overall) < MIN_DELTA:
        return "plateau"
    return "continuing"


def run_loop(
    conn: sqlite3.Connection,
    *,
    writer_backend: StructuredLLM,
    critic_backend: StructuredLLM,
    posting_id: int,
    resume_text: str,
    voice: VoiceConfig,
    settings: CriteriaFile,
    now: str | None = None,
) -> LoopResult:
    ts = now or now_iso()
    posting = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if posting is None:
        raise LookupError(f"no posting with id {posting_id}")

    application_id = get_or_create_application(conn, posting_id, ts)
    current_status = conn.execute(
        "SELECT status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()[0]
    if current_status != RESUMABLE_STATUS:
        raise LoopRefused(
            f"application {application_id} is '{current_status}', not '{RESUMABLE_STATUS}'"
        )

    log_event(
        conn,
        "loop.started",
        posting_id=posting_id,
        application_id=application_id,
        payload={"max_rounds": MAX_ROUNDS},
        ts=ts,
    )

    history: list[Critique] = []
    usage: list[dict[str, Any]] = []
    draft, draft_id, round_index, stopped = _first_draft(
        conn,
        writer_backend,
        posting_id=posting_id,
        application_id=application_id,
        resume_text=resume_text,
        voice=voice,
        settings=settings,
        usage=usage,
        ts=ts,
    )
    if stopped is not None:
        return _finish(conn, application_id, posting_id, 0, stopped, history, usage, ts)

    assert draft is not None
    while True:
        try:
            critic_result = run_critic(
                conn,
                critic_backend,
                posting=posting,
                draft=draft,
                draft_id=draft_id,
                round_index=round_index,
                resume_text=resume_text,
                config=settings.critic,
                application_id=application_id,
                now=ts,
            )
        except LLMOutputError as exc:
            _log_failure(conn, "critic.failed", posting_id, application_id, round_index, exc, ts)
            return _finish(
                conn,
                application_id,
                posting_id,
                round_index + 1,
                "critic_failed",
                history,
                usage,
                ts,
            )
        except LLMTransportError as exc:
            _log_failure(conn, "critic.failed", posting_id, application_id, round_index, exc, ts)
            return _finish(
                conn,
                application_id,
                posting_id,
                round_index + 1,
                "backend_unreachable",
                history,
                usage,
                ts,
            )

        history.append(critic_result.critique)
        usage.append({"agent": "critic", "round": round_index, **critic_result.usage})

        if not should_continue(history):
            return _finish(
                conn,
                application_id,
                posting_id,
                round_index + 1,
                describe_stop(history),
                history,
                usage,
                ts,
            )

        next_round = round_index + 1
        try:
            revision = write_revision(
                conn,
                writer_backend,
                application_id=application_id,
                posting=posting,
                previous_draft=draft,
                critique=critic_result.critique,
                round_index=next_round,
                resume_text=resume_text,
                voice=voice,
                config=settings.writer,
                now=ts,
            )
        except LLMOutputError:
            return _finish(
                conn, application_id, posting_id, next_round, "writer_failed", history, usage, ts
            )
        except LLMTransportError as exc:
            _log_failure(conn, "writer.failed", posting_id, application_id, next_round, exc, ts)
            return _finish(
                conn,
                application_id,
                posting_id,
                next_round,
                "backend_unreachable",
                history,
                usage,
                ts,
            )

        draft, draft_id, round_index = revision.draft, revision.draft_id, next_round
        usage.append({"agent": "writer", "round": next_round, **revision.usage})


# --- steps --------------------------------------------------------------------


def _first_draft(
    conn: sqlite3.Connection,
    writer_backend: StructuredLLM,
    *,
    posting_id: int,
    application_id: int,
    resume_text: str,
    voice: VoiceConfig,
    settings: CriteriaFile,
    usage: list[dict[str, Any]],
    ts: str,
) -> tuple[Draft | None, int | None, int, str | None]:
    """Reuse the newest existing draft, or write round 0. A draft made by
    `writer draft` is picked up here rather than duplicated."""
    existing = conn.execute(
        "SELECT * FROM drafts WHERE application_id = ? ORDER BY round_index DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    if existing is not None:
        return _load_draft(existing), existing["id"], existing["round_index"], None

    try:
        result = run_writer(
            conn,
            writer_backend,
            posting_id=posting_id,
            resume_text=resume_text,
            voice=voice,
            config=settings.writer,
            now=ts,
        )
    except LLMOutputError:
        return None, None, 0, "writer_failed"
    except LLMTransportError as exc:
        _log_failure(conn, "writer.failed", posting_id, application_id, 0, exc, ts)
        return None, None, 0, "backend_unreachable"

    usage.append({"agent": "writer", "round": 0, **result.usage})
    return result.draft, result.draft_id, 0, None


def _load_draft(row: sqlite3.Row) -> Draft:
    import json

    return Draft(
        bullets=[Bullet.model_validate(b) for b in json.loads(row["bullets_json"])],
        cover_letter=row["cover_letter"],
    )


def _log_failure(
    conn: sqlite3.Connection,
    kind: str,
    posting_id: int,
    application_id: int,
    round_index: int,
    exc: Exception,
    ts: str,
) -> None:
    log.warning("loop: %s at round %s: %s", kind, round_index, exc)
    log_event(
        conn,
        kind,
        posting_id=posting_id,
        application_id=application_id,
        payload={"round_index": round_index, "error": f"{type(exc).__name__}: {exc}"},
        ts=ts,
    )


def _finish(
    conn: sqlite3.Connection,
    application_id: int,
    posting_id: int,
    rounds_completed: int,
    stopped_because: str,
    history: list[Critique],
    usage: list[dict[str, Any]],
    ts: str,
) -> LoopResult:
    latest = history[-1] if history else None
    # Hand it to the human only if there is something to look at.
    if draft_count(conn, application_id):
        conn.execute(
            "UPDATE applications SET status = 'awaiting_review', updated_at = ? WHERE id = ?",
            (ts, application_id),
        )
    log_event(
        conn,
        "loop.finished",
        posting_id=posting_id,
        application_id=application_id,
        payload={
            "rounds_completed": rounds_completed,
            "stopped_because": stopped_because,
            "final_overall": latest.overall if latest else None,
            "final_unsupported_claims": latest.unsupported_claim_count if latest else None,
            "scores_by_round": [{"round": c.round_index, "overall": c.overall} for c in history],
        },
        ts=ts,
    )
    return LoopResult(
        application_id=application_id,
        rounds_completed=rounds_completed,
        stopped_because=stopped_because,
        history=history,
        final_overall=latest.overall if latest else None,
        final_unsupported_claims=latest.unsupported_claim_count if latest else None,
        usage=usage,
    )
