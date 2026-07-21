"""Hardened SQLite connection and schema initialization helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_MS = 5_000


class ManagedConnection(sqlite3.Connection):
    """A connection that closes when used as a context manager.

    ``sqlite3.Connection.__exit__`` only commits or rolls back.  Request handlers
    in this project use ``with connect(...)`` and expect deterministic closing,
    so this small subclass makes that ownership explicit.
    """

    def __exit__(self, exc_type, exc_value, traceback):  # type: ignore[no-untyped-def]
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> str:
    """Return a timezone-aware UTC timestamp suitable for SQLite TEXT columns."""

    return datetime.now(UTC).isoformat(timespec="microseconds")


def connect(settings: Settings) -> sqlite3.Connection:
    """Open one configured connection.

    Connections may cross FastAPI's worker-thread boundary, but a connection is
    never shared concurrently.  WAL plus a busy timeout improves short write
    contention; financial operations still acquire ``BEGIN IMMEDIATE``.
    """

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        settings.database_path,
        timeout=BUSY_TIMEOUT_MS / 1_000,
        isolation_level="DEFERRED",
        check_same_thread=False,
        factory=ManagedConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA secure_delete = ON")
    return conn


def init_database(settings: Settings) -> None:
    """Create all tables, constraints, indexes, and immutability triggers."""

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(settings) as conn:
        conn.executescript(schema)
        # Conservative in-place migration for databases created by version 1.
        # Existing manual moderation decisions remain source='none' and are never
        # automatically reversed.
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "status_source" not in user_columns:
            conn.execute(
                """
                ALTER TABLE users ADD COLUMN status_source TEXT NOT NULL DEFAULT 'none'
                CHECK (status_source IN ('none', 'auto_report', 'admin'))
                """
            )
        product_columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        if "visibility_source" not in product_columns:
            conn.execute(
                """
                ALTER TABLE products ADD COLUMN visibility_source TEXT NOT NULL DEFAULT 'none'
                CHECK (visibility_source IN ('none', 'auto_report', 'admin'))
                """
            )
        report_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
        if "auto_eligible" not in report_columns:
            conn.execute(
                """
                ALTER TABLE reports ADD COLUMN auto_eligible INTEGER NOT NULL DEFAULT 0
                CHECK (auto_eligible IN (0, 1))
                """
            )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
