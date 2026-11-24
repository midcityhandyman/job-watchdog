"""SQLite schema: one table of runs, one table of alerts."""

from __future__ import annotations

import sqlite3

MIN_SQLITE = (3, 35, 0)  # the watchdog uses UPDATE ... RETURNING

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,              -- NULL while the job is still running
    ok          INTEGER,           -- 1 or 0, NULL while running
    exit_code   INTEGER,
    duration_s  REAL,
    counts      TEXT NOT NULL DEFAULT '{}',   -- JSON object of counters
    error       TEXT,
    checked_at  TEXT               -- set once the watchdog has judged this run
);
CREATE INDEX IF NOT EXISTS runs_job_started ON runs (job, started_at);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    job         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    ref         TEXT NOT NULL,     -- what the alert is about; part of the dedupe key
    message     TEXT NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    UNIQUE (job, kind, ref)
);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open the database and make sure the schema exists."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(
            f"job-watchdog needs SQLite {'.'.join(map(str, MIN_SQLITE))} or newer "
            f"(for UPDATE ... RETURNING); this Python is linked against {sqlite3.sqlite_version}"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
