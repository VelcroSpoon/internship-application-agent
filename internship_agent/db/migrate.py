"""Forward-only SQL migrations.

Each file in ``migrations/`` is named ``NNNN_description.sql``. The runner
applies every file whose number is not yet in ``schema_migrations``, in
numeric order, each inside its own transaction. SQLite DDL is transactional,
so a failing migration leaves the database untouched.

No down-migrations: this is a personal app with a single SQLite file, and a
backup copy of the file is a better rollback than hand-written reverse SQL.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationError(RuntimeError):
    pass


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        m = _FILENAME.match(path.name)
        if not m:
            raise MigrationError(f"bad migration filename: {path.name}")
        found.append((int(m.group(1)), path))
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migrations must be numbered 1..N without gaps, got {versions}")
    return found


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    conn.execute(_BOOTSTRAP)
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return [r[0] for r in rows]


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this call."""
    done = set(applied_versions(conn))
    applied: list[int] = []
    for version, path in discover(migrations_dir):
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.execute("BEGIN")
        try:
            _execute_statements(conn, sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, path.stem, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def _execute_statements(conn: sqlite3.Connection, sql: str) -> None:
    # sqlite3.executescript() issues an implicit COMMIT first, which would break
    # the per-migration transaction. Accumulate lines and let SQLite's own
    # tokenizer decide where a statement ends, so ';' inside comments or
    # string literals is handled correctly.
    buffer: list[str] = []
    for line in sql.splitlines(keepends=True):
        buffer.append(line)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            conn.execute(candidate)
            buffer = []
    trailing = "".join(buffer)
    if trailing.strip():  # comment-only tails are harmless; real fragments raise here
        conn.execute(trailing)
