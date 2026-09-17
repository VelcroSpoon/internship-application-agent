"""Opt-in live test against the real Anthropic API. Skipped unless
INTERNSHIP_AGENT_LIVE=1 and a credential is available. Costs a few cents.

Run with:
    INTERNSHIP_AGENT_LIVE=1 uv run pytest tests/test_live_writer.py -s
"""

import json
import os
from pathlib import Path

import pytest

from internship_agent.config import (
    DEFAULT_VOICE_PATH,
    WriterConfig,
    build_backend,
    load_voice,
)
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.scout.greenhouse import parse_jobs
from internship_agent.scout.run import upsert_posting
from internship_agent.writer.run import run_writer

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_scaleai.json"
RESUME = Path(__file__).parent.parent / "config" / "master_resume.md"

pytestmark = pytest.mark.skipif(
    os.environ.get("INTERNSHIP_AGENT_LIVE") != "1",
    reason="set INTERNSHIP_AGENT_LIVE=1 to run against the real API",
)


def test_live_writer_drafts_the_sf_intern_posting(tmp_path: Path):
    conn = connect(tmp_path / "live.db")
    migrate(conn)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = parse_jobs("scaleai", payload, company="Scale AI")
    target = next(r for r in records if r.external_id == "4730845005")  # SF SWE intern
    upsert_posting(conn, target, "2026-09-17T00:00:00Z")
    posting_id = conn.execute("SELECT id FROM postings").fetchone()[0]

    cfg = WriterConfig()  # anthropic / claude-opus-5 / medium
    result = run_writer(
        conn,
        build_backend(cfg),
        posting_id=posting_id,
        resume_text=RESUME.read_text(encoding="utf-8"),
        voice=load_voice(DEFAULT_VOICE_PATH),
        config=cfg,
    )

    print("\n" + result.draft.as_text())
    print("anchors:", [b.resume_anchor for b in result.draft.bullets])
    print("voice hits:", result.voice_hits)
    print("usage:", result.usage)

    assert result.draft_id is not None
    assert 3 <= len(result.draft.bullets) <= 6
    assert result.usage["completion_tokens"] > 0
    # Grounding smoke check: no bullet may mention a technology absent from the resume.
    resume_lower = RESUME.read_text(encoding="utf-8").lower()
    for b in result.draft.bullets:
        for tech in ("mongodb", "react", "kubernetes", "rust", "go "):
            assert tech not in b.text.lower() or tech in resume_lower, b.text
