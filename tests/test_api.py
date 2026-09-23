"""FastAPI layer, driven through TestClient against a real temp database.

The loop endpoint is deliberately blocking: this is a single-user tool, and
a job table plus polling would be more moving parts than the problem has.
"""

import pytest
from fastapi.testclient import TestClient

from internship_agent.api.app import create_app
from internship_agent.clock import now_iso
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.scout.run import upsert_posting
from internship_agent.writer.models import Bullet, Draft
from tests.conftest import INTERN_EXTERNAL_ID
from tests.critique_factory import critique, finding
from tests.fakes import FakeLLM

TS = "2026-09-17T00:00:00Z"


@pytest.fixture
def paths(tmp_path, real_postings, fake_resume):
    """A database seeded with the real postings, plus config files on disk."""
    db = tmp_path / "api.db"
    conn = connect(db)
    migrate(conn)
    for record in real_postings:
        upsert_posting(conn, record, now_iso())
    conn.close()

    resume = tmp_path / "resume.md"
    resume.write_text(fake_resume, encoding="utf-8")
    criteria = tmp_path / "criteria.toml"
    criteria.write_text(
        f'[candidate]\nmaster_resume_path = "{resume.as_posix()}"\n'
        '[criteria]\ntarget_cycle = "Summer 2027"\nqueue_threshold = 70\n'
        '[screener]\nmodel = "fake"\n[writer]\nmodel = "fake"\n[critic]\nmodel = "fake"\n',
        encoding="utf-8",
    )
    voice = tmp_path / "voice.toml"
    voice.write_text('[voice]\nbanned_phrases = ["passionate about"]\n', encoding="utf-8")
    return db, criteria, voice


def draft(tag: str = "a") -> Draft:
    return Draft(
        bullets=[
            Bullet(text=f"Bullet {i} {tag} on PyTorch.", resume_anchor=f"resume line {i}")
            for i in range(3)
        ],
        cover_letter=f"Draft {tag}. I built a MinHash deduper in pandas. " * 10,
    )


def make_client(paths, writer=None, critic=None) -> TestClient:
    db, criteria, voice = paths
    app = create_app(
        db_path=db,
        criteria_path=criteria,
        voice_path=voice,
        writer_backend=writer,
        critic_backend=critic,
        start_scheduler=False,
    )
    return TestClient(app)


def posting_id_for(paths, external_id: str) -> int:
    conn = connect(paths[0])
    row = conn.execute("SELECT id FROM postings WHERE external_id = ?", (external_id,)).fetchone()
    conn.close()
    return row["id"]


def screen(paths, posting_id: int, score: float) -> None:
    conn = connect(paths[0])
    conn.execute(
        "INSERT INTO screenings (posting_id, model, fit_score, reason, created_at) "
        "VALUES (?, 'fake', ?, 'a reason', ?)",
        (posting_id, score, TS),
    )
    conn.close()


# --- basics -----------------------------------------------------------------


def test_health_reports_the_database_it_is_using(paths):
    client = make_client(paths)

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["postings"] == 4
    assert body["schema_version"] == 2  # latest applied migration


def test_postings_are_listable(paths):
    client = make_client(paths)

    body = client.get("/postings", params={"limit": 2}).json()

    assert len(body) == 2
    assert body[0]["company"] == "Scale AI"


# --- the queue --------------------------------------------------------------


def test_queue_returns_only_postings_above_the_configured_threshold(paths):
    high = posting_id_for(paths, INTERN_EXTERNAL_ID)
    low = posting_id_for(paths, "4712321005")
    screen(paths, high, 88)
    screen(paths, low, 20)
    client = make_client(paths)

    body = client.get("/queue").json()

    assert [q["posting_id"] for q in body] == [high]
    assert body[0]["fit_score"] == 88
    assert body[0]["reason"] == "a reason"
    assert "Software Engineering Intern" in body[0]["title"]


def test_queue_is_empty_before_anything_is_screened(paths):
    assert make_client(paths).get("/queue").json() == []


# --- the loop ---------------------------------------------------------------


def test_loop_run_blocks_and_returns_the_finished_result(paths):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    client = make_client(
        paths,
        writer=FakeLLM([draft("a"), draft("b")]),
        critic=FakeLLM(
            [critique(0, default_score=3, findings=[finding()]), critique(1, default_score=4)]
        ),
    )

    resp = client.post("/loop/run", json={"posting_id": pid})

    assert resp.status_code == 200
    body = resp.json()
    assert body["rounds_completed"] == 2
    assert body["stopped_because"] == "quality_bar"
    assert body["status"] == "awaiting_review"
    assert [r["overall"] for r in body["scores_by_round"]] == [3.0, 4.0]
    assert body["scores_by_round"][0]["unsupported_claim_count"] == 1


def test_loop_run_on_an_unknown_posting_is_a_404(paths):
    client = make_client(paths)

    assert client.post("/loop/run", json={"posting_id": 9999}).status_code == 404


def test_loop_run_twice_is_a_conflict_not_a_second_loop(paths):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    client = make_client(
        paths, writer=FakeLLM([draft()]), critic=FakeLLM([critique(0, default_score=5)])
    )
    client.post("/loop/run", json={"posting_id": pid})

    resp = client.post("/loop/run", json={"posting_id": pid})

    assert resp.status_code == 409
    assert "awaiting_review" in resp.json()["detail"]


def test_a_model_failure_returns_200_with_the_reason_not_a_500(paths):
    """The loop degrades rather than raising, and the API reports that
    faithfully: the application exists and is reviewable."""
    from internship_agent.llm.base import LLMTransportError

    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    client = make_client(
        paths, writer=FakeLLM([draft()]), critic=FakeLLM([LLMTransportError("no credential")])
    )

    body = client.post("/loop/run", json={"posting_id": pid}).json()

    assert body["stopped_because"] == "backend_unreachable"
    assert "no credential" in body["stopped_detail"]
    assert body["rounds_completed"] == 1


# --- applications and the human gate ----------------------------------------


def _run_one(paths, rounds: int = 2):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    client = make_client(
        paths,
        writer=FakeLLM([draft("a"), draft("b")]),
        critic=FakeLLM(
            [critique(0, default_score=3, findings=[finding()]), critique(1, default_score=4)]
        ),
    )
    client.post("/loop/run", json={"posting_id": pid})
    return client


def test_applications_list_carries_what_the_queue_view_needs(paths):
    client = _run_one(paths)

    (app,) = client.get("/applications").json()

    assert app["status"] == "awaiting_review"
    assert app["company"] == "Scale AI"
    assert app["rounds"] == 2
    assert app["first_overall"] == 3.0 and app["final_overall"] == 4.0


def test_application_detail_carries_every_round_with_its_critique(paths):
    client = _run_one(paths)

    body = client.get("/applications/1").json()

    assert body["rounds"] == 2
    assert len(body["drafts"]) == 2
    first = body["drafts"][0]
    assert first["round_index"] == 0
    assert first["bullets"][0]["text"] == "Bullet 0 a on PyTorch."
    assert first["bullets"][0]["resume_anchor"] == "resume line 0"
    assert first["critique"]["overall"] == 3.0
    assert first["critique"]["findings"][0]["severity"] == "blocker"
    assert first["critique"]["findings"][0]["fix_direction"].startswith("Cut the user-count")
    assert first["voice_hits"] == [] and first["critique_leaks"] == []
    # The diff view in the dashboard needs both revisions' text.
    assert body["drafts"][1]["cover_letter"] != first["cover_letter"]


def test_unknown_application_is_a_404(paths):
    assert make_client(paths).get("/applications/999").status_code == 404


def test_approve_moves_it_out_of_review(paths):
    client = _run_one(paths)

    resp = client.post("/applications/1/approve", json={"note": "good"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert client.get("/applications/1").json()["status"] == "approved"


def test_reject_moves_it_out_of_review(paths):
    client = _run_one(paths)

    assert client.post("/applications/1/reject", json={"note": "no"}).json()["status"] == "rejected"


def test_an_illegal_transition_is_a_conflict(paths):
    client = _run_one(paths)
    client.post("/applications/1/approve", json={})

    resp = client.post("/applications/1/reject", json={})

    assert resp.status_code == 409
    assert "approved" in resp.json()["detail"]


def test_marking_submitted_is_recorded_after_approval(paths):
    client = _run_one(paths)
    client.post("/applications/1/approve", json={})

    resp = client.post("/applications/1/submitted", json={"note": "sent 2026-09-18"})

    assert resp.json()["status"] == "submitted"


# --- stats for the chart ----------------------------------------------------


def test_scores_by_round_is_the_chart_the_project_exists_for(paths):
    _run_one(paths)
    client = make_client(paths)

    body = client.get("/stats/scores-by-round").json()

    assert body == [
        {"round_index": 0, "mean_overall": 3.0, "applications": 1},
        {"round_index": 1, "mean_overall": 4.0, "applications": 1},
    ]


def test_scores_by_dimension_breaks_the_chart_down(paths):
    _run_one(paths)
    client = make_client(paths)

    body = client.get("/stats/scores-by-dimension").json()

    grounding = [r for r in body if r["dimension"] == "grounding"]
    assert [(r["round_index"], r["mean_score"]) for r in grounding] == [(0, 3.0), (1, 4.0)]
    assert len(body) == 10  # five dimensions over two rounds


def test_stats_are_empty_before_any_loop_runs(paths):
    client = make_client(paths)

    assert client.get("/stats/scores-by-round").json() == []


# --- stage 6: what the dashboard needs --------------------------------------


def test_queue_carries_source_and_both_dates_so_age_can_be_shown(paths):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    screen(paths, pid, 88)
    client = make_client(paths)

    (item,) = client.get("/queue").json()

    assert item["source"] == "greenhouse:scaleai"
    # posted_at is the board's own publish date: the honest basis for age.
    # first_seen_at is when we found it, the fallback when a board reports nothing.
    assert item["posted_at"] is not None and item["posted_at"].startswith("20")
    assert item["first_seen_at"].startswith("20")


def test_posting_detail_carries_the_full_text_and_the_screening(paths):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    screen(paths, pid, 88)
    client = make_client(paths)

    body = client.get(f"/postings/{pid}").json()

    assert body["posting_id"] == pid
    assert body["source"] == "greenhouse:scaleai"
    assert "Available for a Summer 2027 internship" in body["description"]
    assert "<p>" not in body["description"]
    assert body["screening"]["fit_score"] == 88
    assert body["screening"]["reason"] == "a reason"
    assert body["application"] is None


def test_posting_detail_reports_an_existing_application(paths):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    client = _run_one(paths)

    body = client.get(f"/postings/{pid}").json()

    assert body["application"] == {"application_id": 1, "status": "awaiting_review"}


def test_posting_detail_without_a_screening_is_still_served(paths):
    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)

    body = make_client(paths).get(f"/postings/{pid}").json()

    assert body["screening"] is None and body["application"] is None


def test_unknown_posting_detail_is_a_404(paths):
    assert make_client(paths).get("/postings/9999").status_code == 404


# --- human edits ------------------------------------------------------------


def _edit_body(letter="My own letter, written by me, kept short."):
    return {
        "bullets": [{"text": "I wrote this myself.", "resume_anchor": "resume line 0"}],
        "cover_letter": letter,
        "note": "tightened the opening",
    }


def test_an_edit_is_persisted_as_a_new_human_authored_round(paths):
    client = _run_one(paths)

    resp = client.post("/applications/1/drafts", json=_edit_body())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["drafts"]) == 3
    latest = body["drafts"][-1]
    assert latest["round_index"] == 2
    assert latest["authored_by"] == "human"
    assert latest["writer_model"] is None
    assert latest["cover_letter"].startswith("My own letter")
    assert latest["critique"] is None  # nothing critiques a human edit
    assert body["status"] == "awaiting_review"


def test_the_model_rounds_are_still_attributed_to_the_writer(paths):
    client = _run_one(paths)
    client.post("/applications/1/drafts", json=_edit_body())

    body = client.get("/applications/1").json()

    assert [d["authored_by"] for d in body["drafts"]] == ["writer", "writer", "human"]


def test_a_human_round_never_enters_the_eval_numbers(paths):
    """The point of the attribution column: my own editing must not show up as
    the model improving."""
    client = _run_one(paths)
    client.post("/applications/1/drafts", json=_edit_body())

    by_round = client.get("/stats/scores-by-round").json()

    assert [r["round_index"] for r in by_round] == [0, 1]
    by_dim = client.get("/stats/scores-by-dimension").json()
    assert {r["round_index"] for r in by_dim} == {0, 1}


def test_an_edit_is_not_held_to_the_writer_prompt_contract(paths):
    """The Draft schema is what the model is asked for, not a rule for the
    human. A 40-character letter would fail the model's 300-char minimum."""
    client = _run_one(paths)

    resp = client.post("/applications/1/drafts", json=_edit_body(letter="Short. Deliberately."))

    assert resp.status_code == 200
    assert resp.json()["drafts"][-1]["cover_letter"] == "Short. Deliberately."


def test_an_empty_edit_is_rejected(paths):
    client = _run_one(paths)

    resp = client.post(
        "/applications/1/drafts", json={"bullets": [], "cover_letter": "", "note": ""}
    )

    assert resp.status_code == 422


def test_editing_a_decided_application_is_a_conflict(paths):
    client = _run_one(paths)
    client.post("/applications/1/approve", json={})

    resp = client.post("/applications/1/drafts", json=_edit_body())

    assert resp.status_code == 409
    assert "approved" in resp.json()["detail"]


def test_editing_an_unknown_application_is_a_404(paths):
    assert make_client(paths).post("/applications/1/drafts", json=_edit_body()).status_code == 404


def test_a_failed_first_draft_leaves_the_posting_startable(paths):
    """Without a key the loop stops before round 0 and leaves an empty
    application. The posting page must offer drafting again, not link to a
    review of nothing, and the posting must still be in the queue."""
    from internship_agent.llm.base import LLMTransportError

    pid = posting_id_for(paths, INTERN_EXTERNAL_ID)
    screen(paths, pid, 88)
    client = make_client(paths, writer=FakeLLM([LLMTransportError("no credential")]))
    client.post("/loop/run", json={"posting_id": pid})

    assert client.get(f"/postings/{pid}").json()["application"] is None
    assert [q["posting_id"] for q in client.get("/queue").json()] == [pid]
