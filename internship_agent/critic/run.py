"""Critic runner: scores one draft, returns a Critique, persists it.

The Critic diagnoses and never writes. Its output shape is the spec's
``Critique``, which has no field capable of carrying replacement prose.

Three fields the model reports are recomputed from its own findings before
the orchestrator acts on them, because the stop gate reads them and a model
doing weighted arithmetic in its head is not reliable:

- ``overall``                 -> compute_overall(scores), the spec's WEIGHTS
- ``unsupported_claim_count`` -> count of BLOCKER-severity grounding findings,
                                 which is the field's own definition
- ``round_index``             -> the round the orchestrator is actually on

No weight and no threshold is changed; the spec's definitions are applied
rather than trusted. What the model said is kept alongside so the gap
between reported and computed is measurable (stage 7 eval).

``verdict`` is left exactly as the model set it. The rubric calls it
advisory and the gate never reads it, so it stays a clean signal of whether
the Critic's own judgement agrees with the arithmetic.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from internship_agent.agents.critic import Critique, Dimension, Severity, compute_overall
from internship_agent.clock import now_iso
from internship_agent.config import CriticConfig
from internship_agent.critic.prompt import build_system, build_user
from internship_agent.db.events import log_event
from internship_agent.llm.base import LLMResponse, StructuredLLM
from internship_agent.llm.retry import complete_with_retry
from internship_agent.writer.models import Draft

log = logging.getLogger(__name__)


@dataclass
class CriticResult:
    critique: Critique
    critique_id: int | None
    model: str
    usage: dict[str, Any]
    model_reported: dict[str, Any]


def count_blockers(critique: Critique) -> int:
    """The spec's definition of unsupported_claim_count, applied."""
    return sum(
        1
        for f in critique.findings
        if f.severity is Severity.BLOCKER and f.dimension is Dimension.GROUNDING
    )


def normalize(critique: Critique, round_index: int) -> tuple[Critique, dict[str, Any]]:
    """Return the critique the loop should act on, plus what the model claimed."""
    reported = {
        "overall": critique.overall,
        "unsupported_claim_count": critique.unsupported_claim_count,
        "round_index": critique.round_index,
        "verdict": critique.verdict.value,
    }
    fixed = critique.model_copy(
        update={
            "overall": compute_overall(critique.scores),
            "unsupported_claim_count": count_blockers(critique),
            "round_index": round_index,
        }
    )
    return fixed, reported


def run_critic(
    conn: sqlite3.Connection,
    backend: StructuredLLM,
    *,
    posting: sqlite3.Row,
    draft: Draft,
    draft_id: int | None,
    round_index: int,
    resume_text: str,
    config: CriticConfig,
    application_id: int | None = None,
    persist: bool = True,
    now: str | None = None,
) -> CriticResult:
    ts = now or now_iso()
    response = complete_with_retry(
        backend,
        system=build_system(resume_text),
        user=build_user(posting, draft, round_index),
        schema=Critique,
        max_attempts=config.max_attempts,
    )
    critique, reported = normalize(response.parsed, round_index)
    result = CriticResult(
        critique=critique,
        critique_id=None,
        model=response.model,
        usage=response.usage,
        model_reported=reported,
    )
    if persist and draft_id is not None:
        result.critique_id = _persist(conn, draft_id, application_id, result, response, ts)
    return result


def _persist(
    conn: sqlite3.Connection,
    draft_id: int,
    application_id: int | None,
    result: CriticResult,
    response: LLMResponse[Critique],
    ts: str,
) -> int:
    c = result.critique
    cur = conn.execute(
        "INSERT INTO critiques (draft_id, round_index, overall, verdict, "
        "unsupported_claim_count, critique_json, critic_model, usage_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            draft_id,
            c.round_index,
            c.overall,
            c.verdict.value,
            c.unsupported_claim_count,
            c.model_dump_json(),
            response.model,
            json.dumps({"usage": response.usage, "model_reported": result.model_reported}),
            ts,
        ),
    )
    critique_id = cur.lastrowid
    # Unpacked so "mean score per dimension per round" is one GROUP BY.
    conn.executemany(
        "INSERT INTO critique_scores (critique_id, dimension, score, reason) VALUES (?, ?, ?, ?)",
        [(critique_id, s.dimension.value, s.score, s.reason) for s in c.scores],
    )
    log_event(
        conn,
        "critic.scored",
        application_id=application_id,
        draft_id=draft_id,
        payload={
            "critique_id": critique_id,
            "round_index": c.round_index,
            "overall": c.overall,
            "unsupported_claim_count": c.unsupported_claim_count,
            "verdict": c.verdict.value,
            "findings": len(c.findings),
            "model": response.model,
            "usage": response.usage,
            "model_reported": result.model_reported,
        },
        ts=ts,
    )
    return critique_id
