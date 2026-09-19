"""The eval: does the Critic loop improve drafts, or plateau?

Two arms over the same fixture postings:

- loop: the real orchestrator. Draft, critique, revise, up to MAX_ROUNDS.
- control: one Writer call and one Critic call. No revision.

Both arms are scored by the same Critic with the same rubric.

Three numbers answer the question, and each is chosen to avoid a specific
way of fooling yourself:

1. Round deltas are *paired*: for round r, only postings that reached round r
   contribute, and each contributes (score at r) minus (its own score at r-1).
   Averaging each round over whoever happens to be in it would compare
   different populations, because the loop only continues on drafts that
   started badly. Survivors look like improvement even when nothing improved.

2. Lift over control is the mean final-round score minus the mean
   single-pass score. That is the whole loop's contribution.

3. The noise floor is the mean absolute gap between the control and the
   loop's own round 0. Both are single-pass drafts of the same prompt, so any
   gap between them is sampling, not method. A lift no bigger than that has
   not been shown to be anything.

Edit magnitude is computed here, in Python, per revision, as a number. The
dashboard renders diffs for reading; this measures how much changed. They are
different consumers with different outputs and deliberately share no code.
"""

from __future__ import annotations

import difflib
import json
import re
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from internship_agent.agents.critic import MIN_DELTA, Critique, Dimension
from internship_agent.clock import now_iso
from internship_agent.config import CriteriaFile, VoiceConfig
from internship_agent.critic.run import run_critic
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.llm.base import StructuredLLM
from internship_agent.orchestrator import run_loop
from internship_agent.scout.models import PostingRecord
from internship_agent.scout.run import upsert_posting
from internship_agent.writer.models import Bullet, Draft
from internship_agent.writer.run import run_writer

LOOP, CONTROL = "loop", "control"

BackendFor = Callable[[str], StructuredLLM]


# --- per-posting results -------------------------------------------------------


@dataclass
class RoundStat:
    round_index: int
    overall: float
    unsupported_claim_count: int
    dimension_scores: dict[Dimension, int]
    edit_magnitude: float | None  # vs the previous round; None for round 0
    voice_hits: int


@dataclass
class ArmResult:
    rounds: list[RoundStat] = field(default_factory=list)
    stopped_because: str = ""


@dataclass
class PostingRun:
    posting_id: int
    company: str
    title: str
    loop: ArmResult
    control: ArmResult


# --- edit magnitude ------------------------------------------------------------

_WORD = re.compile(r"\S+")


def edit_magnitude(before: str, after: str) -> float:
    """Share of the text that changed, word level, 0.0 (identical) to 1.0 (rewritten).

    Case and whitespace are ignored so a reflowed paragraph is not an edit.
    """
    a = [w.lower() for w in _WORD.findall(before)]
    b = [w.lower() for w in _WORD.findall(after)]
    if not a and not b:
        return 0.0
    return round(1.0 - difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio(), 4)


# --- running the arms ----------------------------------------------------------


def run_posting(
    record: PostingRecord,
    *,
    writer_for: BackendFor,
    critic_for: BackendFor,
    resume_text: str,
    voice: VoiceConfig,
    settings: CriteriaFile,
) -> PostingRun:
    """Both arms for one posting, each in a throwaway in-memory database so a
    run never touches data/agent.db and arms cannot see each other's rows."""
    return PostingRun(
        posting_id=0,
        company=record.company,
        title=record.title,
        loop=_run_loop_arm(record, writer_for, critic_for, resume_text, voice, settings),
        control=_run_control_arm(record, writer_for, critic_for, resume_text, voice, settings),
    )


def evaluate(
    records: Sequence[PostingRecord],
    *,
    writer_for: BackendFor,
    critic_for: BackendFor,
    resume_text: str,
    voice: VoiceConfig,
    settings: CriteriaFile,
) -> list[PostingRun]:
    runs = []
    for i, record in enumerate(records):
        run = run_posting(
            record,
            writer_for=writer_for,
            critic_for=critic_for,
            resume_text=resume_text,
            voice=voice,
            settings=settings,
        )
        run.posting_id = i + 1
        runs.append(run)
    return runs


def _fresh_db(record: PostingRecord):
    conn = connect(":memory:")
    migrate(conn)
    upsert_posting(conn, record, now_iso())
    posting_id = conn.execute("SELECT id FROM postings").fetchone()[0]
    return conn, posting_id


def _run_loop_arm(record, writer_for, critic_for, resume_text, voice, settings) -> ArmResult:
    conn, posting_id = _fresh_db(record)
    try:
        result = run_loop(
            conn,
            writer_backend=writer_for(LOOP),
            critic_backend=critic_for(LOOP),
            posting_id=posting_id,
            resume_text=resume_text,
            voice=voice,
            settings=settings,
        )
        rows = conn.execute(
            "SELECT d.round_index, d.bullets_json, d.cover_letter, d.usage_json, "
            "c.critique_json FROM drafts d JOIN critiques c ON c.draft_id = d.id "
            "WHERE d.authored_by = 'writer' ORDER BY d.round_index"
        ).fetchall()
    finally:
        conn.close()

    rounds: list[RoundStat] = []
    previous_text: str | None = None
    for row in rows:
        draft = Draft.model_construct(
            bullets=[Bullet.model_validate(b) for b in json.loads(row["bullets_json"])],
            cover_letter=row["cover_letter"],
        )
        text = draft.as_text()
        extra = json.loads(row["usage_json"]) if row["usage_json"] else {}
        rounds.append(
            _stat(
                Critique.model_validate_json(row["critique_json"]),
                edit=None if previous_text is None else edit_magnitude(previous_text, text),
                voice_hits=len(extra.get("voice_hits", [])),
            )
        )
        previous_text = text
    return ArmResult(rounds=rounds, stopped_because=result.stopped_because)


def _run_control_arm(record, writer_for, critic_for, resume_text, voice, settings) -> ArmResult:
    conn, posting_id = _fresh_db(record)
    try:
        written = run_writer(
            conn,
            writer_for(CONTROL),
            posting_id=posting_id,
            resume_text=resume_text,
            voice=voice,
            config=settings.writer,
            dry_run=True,
        )
        posting = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        scored = run_critic(
            conn,
            critic_for(CONTROL),
            posting=posting,
            draft=written.draft,
            draft_id=None,
            round_index=0,
            resume_text=resume_text,
            config=settings.critic,
            persist=False,
        )
    finally:
        conn.close()
    return ArmResult(
        rounds=[_stat(scored.critique, edit=None, voice_hits=len(written.voice_hits))],
        stopped_because="single_pass",
    )


def _stat(critique: Critique, *, edit: float | None, voice_hits: int) -> RoundStat:
    return RoundStat(
        round_index=critique.round_index,
        overall=critique.overall,
        unsupported_claim_count=critique.unsupported_claim_count,
        dimension_scores={s.dimension: s.score for s in critique.scores},
        edit_magnitude=edit,
        voice_hits=voice_hits,
    )


# --- aggregation ---------------------------------------------------------------


@dataclass
class RoundSummary:
    round_index: int
    postings: int
    mean_overall: float
    mean_by_dimension: dict[Dimension, float]
    mean_edit_magnitude: float | None
    total_blockers: int


@dataclass
class Summary:
    postings: int
    by_round: list[RoundSummary]
    round_deltas: dict[int, float]  # paired: see module docstring
    mean_control: float | None
    mean_final: float | None
    mean_round_zero: float | None
    lift_over_control: float | None
    control_minus_round_zero: float | None  # the noise floor
    final_by_dimension: dict[Dimension, float]
    control_by_dimension: dict[Dimension, float]
    verdict: str


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summarise(runs: Sequence[PostingRun]) -> Summary:
    loop_runs = [r for r in runs if r.loop.rounds]
    max_round = max((s.round_index for r in loop_runs for s in r.loop.rounds), default=-1)

    by_round: list[RoundSummary] = []
    round_deltas: dict[int, float] = {}
    for index in range(max_round + 1):
        stats = [s for r in loop_runs for s in r.loop.rounds if s.round_index == index]
        if not stats:
            continue
        edits = [s.edit_magnitude for s in stats if s.edit_magnitude is not None]
        by_round.append(
            RoundSummary(
                round_index=index,
                postings=len(stats),
                mean_overall=statistics.fmean(s.overall for s in stats),
                mean_by_dimension={
                    d: statistics.fmean(s.dimension_scores[d] for s in stats) for d in Dimension
                },
                mean_edit_magnitude=_mean(edits),
                total_blockers=sum(s.unsupported_claim_count for s in stats),
            )
        )
        if index > 0:
            pairs = [
                (by_index[index].overall - by_index[index - 1].overall)
                for r in loop_runs
                if index in (by_index := {s.round_index: s for s in r.loop.rounds})
                and index - 1 in by_index
            ]
            if pairs:
                round_deltas[index] = statistics.fmean(pairs)

    finals = [r.loop.rounds[-1] for r in loop_runs]
    controls = [r.control.rounds[0] for r in runs if r.control.rounds]
    zeros = [r.loop.rounds[0] for r in loop_runs]
    noise = [
        abs(r.control.rounds[0].overall - r.loop.rounds[0].overall)
        for r in runs
        if r.control.rounds and r.loop.rounds
    ]

    mean_final = _mean([s.overall for s in finals])
    mean_control = _mean([s.overall for s in controls])
    lift = None if mean_final is None or mean_control is None else mean_final - mean_control
    noise_floor = _mean(noise)

    summary = Summary(
        postings=len(runs),
        by_round=by_round,
        round_deltas=round_deltas,
        mean_control=mean_control,
        mean_final=mean_final,
        mean_round_zero=_mean([s.overall for s in zeros]),
        lift_over_control=lift,
        control_minus_round_zero=noise_floor,
        final_by_dimension=_dimension_means(finals),
        control_by_dimension=_dimension_means(controls),
        verdict="",
    )
    summary.verdict = _verdict(summary)
    return summary


def _dimension_means(stats: Sequence[RoundStat]) -> dict[Dimension, float]:
    if not stats:
        return {}
    return {d: statistics.fmean(s.dimension_scores[d] for s in stats) for d in Dimension}


def _verdict(s: Summary) -> str:
    if s.postings == 0 or s.mean_final is None:
        return "No postings were evaluated, so there is no result."
    if s.lift_over_control is None:
        return "The control arm produced no scores, so the loop cannot be compared to anything."

    noise = s.control_minus_round_zero or 0.0
    if s.lift_over_control <= noise:
        return (
            f"The loop's lift over a single pass ({s.lift_over_control:+.2f}) is no larger than "
            f"the noise of an independent redraw ({noise:.2f}). On this evidence the critic "
            f"loop has not been shown to improve drafts."
        )

    deltas = [s.round_deltas[k] for k in sorted(s.round_deltas)]
    if len(deltas) >= 2 and deltas[-1] < MIN_DELTA:
        last = max(s.round_deltas)
        return (
            f"The loop beats a single pass by {s.lift_over_control:+.2f}, but it plateaus: "
            f"round {last} adds {deltas[-1]:+.2f}, under the loop's own "
            f"{MIN_DELTA} stopping threshold. The gain arrives by round {last - 1}."
        )
    return (
        f"The loop beats a single pass by {s.lift_over_control:+.2f}, against a noise floor "
        f"of {noise:.2f}, and each observed round still adds at least {MIN_DELTA}."
    )
