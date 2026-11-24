"""Command line entry point.

    python -m job_watchdog run --job invoice-sync -- python sync.py
    python -m job_watchdog check
    python -m job_watchdog status
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

from .alerts import channel_from_env
from .db import connect
from .registry import ConfigError, load_jobs
from .watchdog import Watchdog
from .wrapper import run_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job_watchdog", description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.environ.get("WATCHDOG_DB", "watchdog.db"))
    parser.add_argument("--jobs", default=os.environ.get("WATCHDOG_JOBS", "jobs.json"))
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a command as a recorded job")
    run.add_argument("--job", required=True)
    run.add_argument("argv", nargs=argparse.REMAINDER, help="-- command and arguments")

    sub.add_parser("check", help="run one watchdog pass and send alerts")
    sub.add_parser("status", help="print one line per registered job")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    conn = connect(args.db)

    if args.command == "run":
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            print("nothing to run; usage: run --job NAME -- command args", file=sys.stderr)
            return 2
        try:
            return run_command(conn, args.job, command)
        except OSError as exc:
            # Already recorded as a failed run; no traceback for a typo in a crontab.
            print(f"cannot start {command[0]!r}: {exc}", file=sys.stderr)
            return 126 if isinstance(exc, PermissionError) else 127

    try:
        jobs = load_jobs(args.jobs)
    except (ConfigError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    dog = Watchdog(conn, jobs, channel_from_env())

    if args.command == "check":
        raised = dog.check()
        print(f"watchdog pass done: {len(raised)} new alert(s)")
        held = dog.undelivered()
        if held:
            print(f"{held} alert(s) could not be delivered and are held for the next pass",
                  file=sys.stderr)
        # Exit 1 when something needs a human or alerts are not getting out,
        # so the watchdog can itself be watched.
        return 1 if held or any(a.severity != "info" for a in raised) else 0

    for row in dog.status():
        flags = ", ".join(row["open_alerts"]) or "none"
        print(
            f"{row['job']:<20} {row['state']:<24} "
            f"last start {row['last_started'] or 'never':<26} open: {flags}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
