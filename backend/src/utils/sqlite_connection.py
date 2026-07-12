"""Shared SQLite connection configuration and lifecycle helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from os import PathLike
from typing import Iterator


SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000


def connect_sqlite(
    db_path: str | PathLike[str],
    *,
    row_factory: type[sqlite3.Row] | None = None,
) -> sqlite3.Connection:
    """Open a consistently configured SQLite connection.

    WAL improves read/write coexistence for the scraper and local API. Foreign
    keys are enabled per connection (SQLite does not persist that setting), and
    busy_timeout gives short overlapping writes time to complete.
    """
    conn = sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS)
    try:
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        if row_factory is not None:
            conn.row_factory = row_factory
        return conn
    except Exception:
        conn.close()
        raise


@contextmanager
def open_sqlite(
    db_path: str | PathLike[str],
    *,
    row_factory: type[sqlite3.Row] | None = None,
) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection and always close it.

    The transaction semantics intentionally match ``with sqlite3.connect(...)``:
    successful work is committed and exceptions are rolled back. Unlike that
    built-in context manager, this helper also closes the connection.
    """
    conn = connect_sqlite(db_path, row_factory=row_factory)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
