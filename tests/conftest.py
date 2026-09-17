"""Shared fixtures: four real postings captured from a live Greenhouse board
and the fictional master resume, so the whole pipeline can be exercised with
no network and no API spend.

The stage-7 eval harness reuses these, which is why they live here rather
than inside one test module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from internship_agent.clock import now_iso
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.scout.greenhouse import parse_jobs
from internship_agent.scout.models import PostingRecord
from internship_agent.scout.run import upsert_posting

FIXTURE_DIR = Path(__file__).parent / "fixtures"
GREENHOUSE_FIXTURE = FIXTURE_DIR / "greenhouse_scaleai.json"
RESUME_PATH = Path(__file__).parent.parent / "config" / "master_resume.md"

# The Summer 2027 SWE intern posting in San Francisco: the one a candidate
# like the fixture resume would actually apply to.
INTERN_EXTERNAL_ID = "4730845005"


@pytest.fixture(scope="session")
def fake_resume() -> str:
    return RESUME_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def real_postings() -> list[PostingRecord]:
    payload = json.loads(GREENHOUSE_FIXTURE.read_text(encoding="utf-8"))
    return parse_jobs("scaleai", payload, company="Scale AI")


@pytest.fixture
def seeded_db(tmp_path, real_postings):
    """A migrated database holding the real postings. Yields (conn, ids-by-external-id)."""
    conn = connect(tmp_path / "fixture.db")
    migrate(conn)
    ts = now_iso()
    for record in real_postings:
        upsert_posting(conn, record, ts)
    ids = {
        row["external_id"]: row["id"]
        for row in conn.execute("SELECT id, external_id FROM postings")
    }
    yield conn, ids
    conn.close()
