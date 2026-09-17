"""The human gate. The loop parks an application at 'awaiting_review' and
stops; everything past that point is a person's decision, recorded here.

'submitted' is the human recording that they submitted it themselves. No
code in this project sends anything anywhere."""

import json

import pytest

from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.review import (
    InvalidTransition,
    approve,
    list_applications,
    mark_submitted,
    reject,
)

TS = "2026-09-17T00:00:00Z"
LATER = "2026-09-18T00:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def seed(conn, status: str = "awaiting_review", *, rounds: int = 2) -> int:
    conn.execute(
        "INSERT INTO postings (dedupe_hash, source, company, title, location, url, "
        "first_seen_at, last_seen_at) VALUES ('h' || ?, 's', 'Scale AI', 'ML Intern', "
        "'Montreal, QC', 'https://x', ?, ?)",
        (status + str(rounds), TS, TS),
    )
    posting_id = conn.execute("SELECT MAX(id) FROM postings").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO applications (posting_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (posting_id, status, TS, TS),
    )
    app_id = cur.lastrowid
    for r in range(rounds):
        d = conn.execute(
            "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
            "writer_model, created_at) VALUES (?, ?, '[]', 'letter', 'm', ?)",
            (app_id, r, TS),
        )
        conn.execute(
            "INSERT INTO critiques (draft_id, round_index, overall, verdict, "
            "unsupported_claim_count, critique_json, created_at) "
            "VALUES (?, ?, ?, 'revise', 0, '{}', ?)",
            (d.lastrowid, r, 3.0 + r, TS),
        )
    return app_id


def status_of(conn, app_id: int) -> str:
    return conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()[0]


# --- transitions ------------------------------------------------------------


def test_approve_moves_an_application_out_of_review(conn):
    app_id = seed(conn)

    approve(conn, app_id, note="bullets are good", now=LATER)

    assert status_of(conn, app_id) == "approved"
    assert (
        conn.execute("SELECT updated_at FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
        == LATER
    )


def test_reject_moves_an_application_out_of_review(conn):
    app_id = seed(conn)

    reject(conn, app_id, note="wrong team", now=LATER)

    assert status_of(conn, app_id) == "rejected"


def test_the_decision_and_its_note_are_recorded_as_an_event(conn):
    app_id = seed(conn)

    approve(conn, app_id, note="ship it", now=LATER)

    row = conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'application.approved'"
    ).fetchone()
    assert json.loads(row[0]) == {"from": "awaiting_review", "note": "ship it"}


def test_a_draft_still_being_written_cannot_be_approved(conn):
    app_id = seed(conn, "drafting")

    with pytest.raises(InvalidTransition):
        approve(conn, app_id)
    assert status_of(conn, app_id) == "drafting"


def test_an_application_cannot_be_approved_twice(conn):
    app_id = seed(conn)
    approve(conn, app_id)

    with pytest.raises(InvalidTransition):
        approve(conn, app_id)


def test_a_rejected_application_cannot_be_approved(conn):
    app_id = seed(conn)
    reject(conn, app_id)

    with pytest.raises(InvalidTransition):
        approve(conn, app_id)


def test_an_unknown_application_raises(conn):
    with pytest.raises(LookupError):
        approve(conn, 999)


# --- submitted is a human's record, not an action ---------------------------


def test_submitted_can_only_follow_approval(conn):
    app_id = seed(conn)

    with pytest.raises(InvalidTransition):
        mark_submitted(conn, app_id)

    approve(conn, app_id)
    mark_submitted(conn, app_id, now=LATER)

    assert status_of(conn, app_id) == "submitted"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'application.marked_submitted'"
        ).fetchone()[0]
        == 1
    )


# --- listing ----------------------------------------------------------------


def test_list_shows_rounds_and_the_latest_score(conn):
    app_id = seed(conn, rounds=3)

    (row,) = list_applications(conn)

    assert row["application_id"] == app_id
    assert row["company"] == "Scale AI" and row["title"] == "ML Intern"
    assert row["status"] == "awaiting_review"
    assert row["rounds"] == 3
    assert row["final_overall"] == 5.0  # 3.0 + 2, the last round
    assert row["first_overall"] == 3.0


def test_list_includes_applications_with_no_critique_yet(conn):
    app_id = seed(conn, "drafting", rounds=0)

    (row,) = list_applications(conn)

    assert row["application_id"] == app_id
    assert row["rounds"] == 0 and row["final_overall"] is None


def test_list_can_filter_by_status(conn):
    seed(conn, "awaiting_review")
    approved = seed(conn, "approved", rounds=1)

    rows = list_applications(conn, status="approved")

    assert [r["application_id"] for r in rows] == [approved]
