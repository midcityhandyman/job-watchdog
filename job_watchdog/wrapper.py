"""The tiny wrapper every scheduled job goes through.

    with record_run(conn, "invoice-sync") as run:
        run.counts["invoices"] = sync_invoices()

A row is written when the job starts and completed when it ends, so a job
that dies without finishing leaves a visible "still running" row behind.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional, Sequence

from .clock import to_iso, utcnow


@dataclass
class RunHandle:
    """What the job body gets: a place for counters and an optional exit code."""

    id: int
    job: str
    counts: dict = field(default_factory=dict)
    exit_code: int = 0


def _finish(conn, run: RunHandle, started, clock, exit_code: int, error: Optional[str]) -> None:
    finished = clock()
    conn.execute(
        "UPDATE runs SET finished_at = ?, ok = ?, exit_code = ?, duration_s = ?, "
        "counts = ?, error = ? WHERE id = ?",
        (
            to_iso(finished),
            1 if exit_code == 0 else 0,
            exit_code,
            round((finished - started).total_seconds(), 3),
            json.dumps(run.counts, sort_keys=True),
            error,
            run.id,
        ),
    )
    conn.commit()


def _exit_code_of(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    return exc.code if isinstance(exc.code, int) else 1


@contextmanager
def record_run(
    conn: sqlite3.Connection, job: str, clock: Callable = utcnow
) -> Iterator[RunHandle]:
    """Record one run of `job`. Exceptions are recorded and then re-raised."""
    started = clock()
    cur = conn.execute(
        "INSERT INTO runs (job, started_at) VALUES (?, ?)", (job, to_iso(started))
    )
    conn.commit()
    run = RunHandle(id=cur.lastrowid, job=job)
    try:
        yield run
    except SystemExit as exc:
        code = _exit_code_of(exc)
        _finish(conn, run, started, clock, code, None if code == 0 else f"exited with {code}")
        raise
    except KeyboardInterrupt:
        # Ctrl-C or SIGINT: record it, or the row would look like a hung job.
        _finish(conn, run, started, clock, 130, "KeyboardInterrupt: interrupted")
        raise
    except Exception as exc:
        _finish(conn, run, started, clock, run.exit_code or 1, f"{type(exc).__name__}: {exc}")
        raise
    else:
        code = run.exit_code
        _finish(conn, run, started, clock, code, None if code == 0 else f"exited with {code}")


def run_command(
    conn: sqlite3.Connection, job: str, argv: Sequence[str], clock: Callable = utcnow
) -> int:
    """Run an external command as `job` and record its exit code.

    A command that cannot be started (missing, not executable) is recorded as
    a failed run with the shell's codes, 127 and 126, and the OSError is re-raised.
    """
    with record_run(conn, job, clock) as run:
        try:
            run.exit_code = subprocess.run(list(argv), check=False).returncode
        except OSError as exc:
            run.exit_code = 126 if isinstance(exc, PermissionError) else 127
            raise
    return run.exit_code
