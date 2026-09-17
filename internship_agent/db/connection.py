"""SQLite connection factory.

Plain sqlite3 rather than an ORM: single-user, single-process app whose
value is a handful of analytics queries. Keeping the schema visible as SQL
is worth more here than object-relational convenience.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas this app relies on.

    - foreign_keys=ON: SQLite ignores FK constraints unless asked, per connection.
    - journal_mode=WAL: lets the FastAPI reader coexist with the nightly writer.
    - Row factory so callers can index by column name.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; explicit BEGIN below
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
