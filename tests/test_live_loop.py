"""Opt-in live run of the whole Writer/Critic loop against the real APIs.

Skipped unless INTERNSHIP_AGENT_LIVE=1 and a credential is available.
Costs roughly 20 cents: up to three Opus drafts and three Sonnet critiques.

    INTERNSHIP_AGENT_LIVE=1 uv run pytest tests/test_live_loop.py -s

This is the test that answers the question the project exists to ask: does
the critic loop actually improve drafts, or does it plateau?
"""

import os

import pytest

from internship_agent.config import (
    DEFAULT_CRITERIA_PATH,
    DEFAULT_VOICE_PATH,
    build_backend,
    load_criteria,
    load_voice,
)
from internship_agent.orchestrator import run_loop
from tests.conftest import INTERN_EXTERNAL_ID

pytestmark = pytest.mark.skipif(
    os.environ.get("INTERNSHIP_AGENT_LIVE") != "1",
    reason="set INTERNSHIP_AGENT_LIVE=1 to run against the real APIs (costs ~$0.20)",
)


def test_live_loop_over_a_real_posting(seeded_db, fake_resume):
    conn, ids = seeded_db
    settings = load_criteria(DEFAULT_CRITERIA_PATH)

    result = run_loop(
        conn,
        writer_backend=build_backend(settings.writer),
        critic_backend=build_backend(settings.critic),
        posting_id=ids[INTERN_EXTERNAL_ID],
        resume_text=fake_resume,
        voice=load_voice(DEFAULT_VOICE_PATH),
        settings=settings,
    )

    print(f"\nstopped_because={result.stopped_because} rounds={result.rounds_completed}")
    for c in result.history:
        scores = "  ".join(f"{s.dimension.value} {s.score}" for s in c.scores)
        print(f"\n--- round {c.round_index}: overall {c.overall:.2f} ({scores})")
        print(f"    blockers={c.unsupported_claim_count} verdict={c.verdict.value}")
        for f in c.findings:
            print(f"    [{f.severity.value}/{f.dimension.value}] {f.excerpt[:70]!r}")
            print(f"        -> {f.fix_direction[:100]}")
    rows = conn.execute(
        "SELECT d.round_index, c.overall, d.usage_json FROM drafts d "
        "LEFT JOIN critiques c ON c.draft_id = d.id ORDER BY d.round_index"
    ).fetchall()
    for r in rows:
        print(f"round {r['round_index']}: overall={r['overall']} usage={r['usage_json']}")

    assert result.rounds_completed >= 1
    assert result.stopped_because != "critic_failed"
    assert result.stopped_because != "writer_failed"
    # Every round persisted its draft and its critique.
    assert len(rows) == result.rounds_completed
    assert all(r["overall"] is not None for r in rows)
    # The boundary held: the Critic's directions did not become the Writer's prose.
    import json

    for r in rows:
        assert json.loads(r["usage_json"])["critique_leaks"] == [], r["round_index"]
