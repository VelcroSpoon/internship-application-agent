"""Record and replay entry points, and the report.

    record  runs both arms against the real models once and saves every
            response to a cassette. Costs money; needs a credential.
    replay  runs both arms against the cassette. Free, offline, deterministic.

The report is written as Markdown for reading and JSON for anything that
wants the numbers.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path

from internship_agent.agents.critic import MAX_ROUNDS, Dimension
from internship_agent.config import CriteriaFile, VoiceConfig
from internship_agent.evals.cassette import Cassette, RecordingBackend, ReplayBackend
from internship_agent.evals.harness import CONTROL, LOOP, PostingRun, Summary, evaluate, summarise
from internship_agent.llm.base import StructuredLLM
from internship_agent.scout.models import PostingRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASSETTE = REPO_ROOT / "evals" / "cassettes" / "fixtures.json"
DEFAULT_RESULTS = REPO_ROOT / "evals" / "results"


def _per_tag(factory: Callable[[str], StructuredLLM]) -> Callable[[str], StructuredLLM]:
    """One backend per arm for the whole run, so a replay cursor or a scripted
    test backend is consumed across postings rather than reset per posting."""
    cache: dict[str, StructuredLLM] = {}

    def get(tag: str) -> StructuredLLM:
        if tag not in cache:
            cache[tag] = factory(tag)
        return cache[tag]

    return get


def record(
    records: Sequence[PostingRecord],
    *,
    writer_for: Callable[[str], StructuredLLM],
    critic_for: Callable[[str], StructuredLLM],
    resume_text: str,
    voice: VoiceConfig,
    settings: CriteriaFile,
    cassette_path: Path = DEFAULT_CASSETTE,
) -> tuple[list[PostingRun], Cassette]:
    """Run both arms live and save every response. The factories take an arm
    tag; a live run can return the same real backend for both arms."""
    cassette = Cassette()
    runs = evaluate(
        records,
        writer_for=_per_tag(lambda tag: RecordingBackend(writer_for(tag), cassette, tag=tag)),
        critic_for=_per_tag(lambda tag: RecordingBackend(critic_for(tag), cassette, tag=tag)),
        resume_text=resume_text,
        voice=voice,
        settings=settings,
    )
    cassette.save(cassette_path)
    return runs, cassette


def replay(
    records: Sequence[PostingRecord],
    *,
    cassette: Cassette,
    writer_model: str,
    critic_model: str,
    resume_text: str,
    voice: VoiceConfig,
    settings: CriteriaFile,
) -> list[PostingRun]:
    return evaluate(
        records,
        writer_for=_per_tag(lambda tag: ReplayBackend(cassette, tag=tag, model=writer_model)),
        critic_for=_per_tag(lambda tag: ReplayBackend(cassette, tag=tag, model=critic_model)),
        resume_text=resume_text,
        voice=voice,
        settings=settings,
    )


# --- report --------------------------------------------------------------------


def _f(value: float | None, fmt: str = ".2f") -> str:
    return "—" if value is None else format(value, fmt)


def render_markdown(summary: Summary, runs: Sequence[PostingRun], cassette: Cassette) -> str:
    dims = list(Dimension)
    out: list[str] = []
    out.append("# Critic loop eval\n")
    out.append(f"**{summary.verdict}**\n")
    out.append(
        f"{summary.postings} postings, up to {MAX_ROUNDS} rounds each. Arms: `{LOOP}` "
        f"(draft, critique, revise) and `{CONTROL}` (one draft, one critique, no revision). "
        "Both scored by the same Critic and rubric.\n"
    )

    out.append("## Headline\n")
    out.append("| measure | value |\n|---|---|")
    out.append(f"| single pass (control) mean overall | {_f(summary.mean_control)} |")
    out.append(f"| loop round 0 mean overall | {_f(summary.mean_round_zero)} |")
    out.append(f"| loop final round mean overall | {_f(summary.mean_final)} |")
    lift = summary.lift_over_control
    out.append(f"| lift over control | {'—' if lift is None else format(lift, '+.2f')} |")
    out.append(
        f"| noise floor: mean abs(control − round 0) | {_f(summary.control_minus_round_zero)} |\n"
    )
    out.append(
        "The control and round 0 are both single-pass drafts of the same prompt, recorded "
        "separately, so the gap between them is sampling noise. A lift no larger than it is "
        "not evidence the loop does anything.\n"
    )

    out.append("## By round\n")
    head = "| round | postings | mean overall | paired Δ | mean edit | blockers | "
    head += " | ".join(d.value for d in dims) + " |"
    out.append(head)
    out.append("|" + "---|" * (6 + len(dims)))
    for r in summary.by_round:
        delta = summary.round_deltas.get(r.round_index)
        cells = [
            str(r.round_index),
            str(r.postings),
            _f(r.mean_overall),
            "—" if delta is None else format(delta, "+.2f"),
            _f(r.mean_edit_magnitude),
            str(r.total_blockers),
            *(_f(r.mean_by_dimension[d]) for d in dims),
        ]
        out.append("| " + " | ".join(cells) + " |")
    out.append(
        "\n*Paired Δ* uses only postings that reached that round, each against its own "
        "previous round. Later rounds contain only drafts the loop kept working on, which "
        "started worse, so comparing raw round means would reward survivorship. *Mean edit* "
        "is the share of words changed since the previous round.\n"
    )

    if summary.final_by_dimension and summary.control_by_dimension:
        out.append("## Final round vs control, per dimension\n")
        out.append("| dimension | control | final | Δ |\n|---|---|---|---|")
        for d in dims:
            c, f = summary.control_by_dimension[d], summary.final_by_dimension[d]
            out.append(f"| {d.value} | {c:.2f} | {f:.2f} | {f - c:+.2f} |")
        out.append("")

    out.append("## Per posting\n")
    out.append("| # | posting | control | loop scores by round | stopped because |")
    out.append("|---|---|---|---|---|")
    for run in runs:
        control = _f(run.control.rounds[0].overall) if run.control.rounds else "—"
        path = " → ".join(f"{s.overall:.2f}" for s in run.loop.rounds) or "—"
        out.append(
            f"| {run.posting_id} | {run.company} — {run.title} | {control} | {path} | "
            f"{run.loop.stopped_because} |"
        )

    costs = cassette.cost_summary()
    if costs:
        out.append("\n## What the recording cost\n")
        out.append("| model | calls | input tokens | output tokens |\n|---|---|---|---|")
        for model, c in sorted(costs.items()):
            out.append(f"| {model} | {c['calls']} | {c['in']} | {c['out']} |")
    return "\n".join(out) + "\n"


def summary_json(summary: Summary, runs: Sequence[PostingRun]) -> dict:
    def dim_map(m):
        return {d.value: v for d, v in m.items()}

    data = asdict(summary)
    data["final_by_dimension"] = dim_map(summary.final_by_dimension)
    data["control_by_dimension"] = dim_map(summary.control_by_dimension)
    for r in data["by_round"]:
        r["mean_by_dimension"] = {d.value: v for d, v in r["mean_by_dimension"].items()}
    data["runs"] = [
        {
            "posting": f"{run.company} — {run.title}",
            "control_overall": run.control.rounds[0].overall if run.control.rounds else None,
            "loop_overall_by_round": [s.overall for s in run.loop.rounds],
            "loop_edit_by_round": [s.edit_magnitude for s in run.loop.rounds],
            "stopped_because": run.loop.stopped_because,
        }
        for run in runs
    ]
    return data


def write_report(
    runs: Sequence[PostingRun], cassette: Cassette, out_dir: Path = DEFAULT_RESULTS
) -> tuple[Summary, Path]:
    summary = summarise(runs)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.md"
    report.write_text(render_markdown(summary, runs, cassette), encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary_json(summary, runs), indent=1, default=str), encoding="utf-8"
    )
    return summary, report
