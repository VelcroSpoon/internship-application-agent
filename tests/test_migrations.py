"""Migration runner: applies numbered SQL files once, in order, and the
resulting schema supports the one-query score-by-round analysis."""

import sqlite3

import pytest

from internship_agent.db.connection import connect
from internship_agent.db.migrate import applied_versions, migrate

EXPECTED_TABLES = {
    "schema_migrations",
    "postings",
    "screenings",
    "applications",
    "drafts",
    "critiques",
    "critique_scores",
    "events",
}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def test_migrate_fresh_db_creates_all_tables(conn):
    applied = migrate(conn)

    assert applied == [1, 2]
    assert EXPECTED_TABLES <= table_names(conn)


def test_migrate_is_idempotent(conn):
    migrate(conn)
    second_run = migrate(conn)

    assert second_run == []
    assert applied_versions(conn) == [1, 2]


def test_connection_enforces_foreign_keys(conn):
    migrate(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO applications (posting_id, status, created_at, updated_at) "
            "VALUES (999, 'drafting', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )


def test_draft_round_index_unique_per_application(conn):
    migrate(conn)
    app_id = _seed_application(conn)
    _insert_draft(conn, app_id, round_index=0)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_draft(conn, app_id, round_index=0)


def test_mean_overall_by_round_is_one_query(conn):
    """The whole point of the schema: score-vs-round across all applications."""
    migrate(conn)
    app_a = _seed_application(conn)
    app_b = _seed_application(conn)
    for app_id, overalls in ((app_a, [3.0, 4.0]), (app_b, [2.0, 3.0, 4.5])):
        for round_index, overall in enumerate(overalls):
            draft_id = _insert_draft(conn, app_id, round_index)
            _insert_critique(conn, draft_id, round_index, overall)

    rows = conn.execute(
        "SELECT round_index, AVG(overall) AS mean_overall, COUNT(*) AS n "
        "FROM critiques GROUP BY round_index ORDER BY round_index"
    ).fetchall()

    assert [tuple(r) for r in rows] == [(0, 2.5, 2), (1, 3.5, 2), (2, 4.5, 1)]


def test_mean_score_per_dimension_per_round_is_one_query(conn):
    migrate(conn)
    app_id = _seed_application(conn)
    for round_index, grounding in enumerate([2, 4]):
        draft_id = _insert_draft(conn, app_id, round_index)
        critique_id = _insert_critique(conn, draft_id, round_index, overall=3.0)
        conn.execute(
            "INSERT INTO critique_scores (critique_id, dimension, score, reason) "
            "VALUES (?, 'grounding', ?, 'r')",
            (critique_id, grounding),
        )

    rows = conn.execute(
        "SELECT c.round_index, s.dimension, AVG(s.score) FROM critique_scores s "
        "JOIN critiques c ON c.id = s.critique_id "
        "GROUP BY c.round_index, s.dimension ORDER BY c.round_index"
    ).fetchall()

    assert [tuple(r) for r in rows] == [(0, "grounding", 2.0), (1, "grounding", 4.0)]


# --- helpers ---------------------------------------------------------------

_TS = "2026-01-01T00:00:00Z"


def _seed_application(conn) -> int:
    cur = conn.execute(
        "INSERT INTO postings (dedupe_hash, source, external_id, company, title, location, "
        "url, description, content_hash, first_seen_at, last_seen_at) "
        "VALUES (?, 'greenhouse:x', ?, 'X', 'SWE Intern', 'SF', 'https://x', 'd', 'h', ?, ?)",
        (f"hash-{_seed_application.counter}", str(_seed_application.counter), _TS, _TS),
    )
    _seed_application.counter += 1
    posting_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO applications (posting_id, status, created_at, updated_at) "
        "VALUES (?, 'drafting', ?, ?)",
        (posting_id, _TS, _TS),
    )
    return cur.lastrowid


_seed_application.counter = 0


def _insert_draft(conn, app_id: int, round_index: int) -> int:
    cur = conn.execute(
        "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
        "writer_model, created_at) VALUES (?, ?, '[]', 'letter', 'm', ?)",
        (app_id, round_index, _TS),
    )
    return cur.lastrowid


def _insert_critique(conn, draft_id: int, round_index: int, overall: float) -> int:
    cur = conn.execute(
        "INSERT INTO critiques (draft_id, round_index, overall, verdict, "
        "unsupported_claim_count, critique_json, created_at) "
        "VALUES (?, ?, ?, 'revise', 0, '{}', ?)",
        (draft_id, round_index, overall, _TS),
    )
    return cur.lastrowid


# --- 0002: human-authored revisions -----------------------------------------


def test_drafts_are_attributed_to_the_writer_by_default(conn):
    migrate(conn)
    app_id = _seed_application(conn)

    draft_id = _insert_draft(conn, app_id, round_index=0)

    assert (
        conn.execute("SELECT authored_by FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0]
        == "writer"
    )


def test_a_draft_can_be_attributed_to_the_human(conn):
    migrate(conn)
    app_id = _seed_application(conn)

    conn.execute(
        "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
        "authored_by, created_at) VALUES (?, 0, '[]', 'mine', 'human', ?)",
        (app_id, _TS),
    )

    assert conn.execute("SELECT authored_by FROM drafts").fetchone()[0] == "human"


def test_no_other_author_is_allowed(conn):
    migrate(conn)
    app_id = _seed_application(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO drafts (application_id, round_index, bullets_json, cover_letter, "
            "authored_by, created_at) VALUES (?, 0, '[]', 'x', 'chatgpt', ?)",
            (app_id, _TS),
        )


def test_migrating_an_existing_v1_database_applies_only_the_new_migration(conn):
    from internship_agent.db.migrate import MIGRATIONS_DIR, _execute_statements, discover

    applied_versions(conn)  # creates the bookkeeping table
    (v1,) = [p for v, p in discover(MIGRATIONS_DIR) if v == 1]
    conn.execute("BEGIN")
    _execute_statements(conn, v1.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, 'x', ?)", (_TS,)
    )
    conn.execute("COMMIT")
    assert "authored_by" not in {r[1] for r in conn.execute("PRAGMA table_info(drafts)")}, (
        "v1 alone must not have the column"
    )

    applied = migrate(conn)

    assert applied == [2]
    assert applied_versions(conn) == [1, 2]
