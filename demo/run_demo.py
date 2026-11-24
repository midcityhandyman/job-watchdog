"""Offline demo: replay a fake job history and watch the watchdog react.

Everything here is invented. The clock is fake and fixed, so the output is
the same on every machine and every run:

    python demo/run_demo.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from job_watchdog import FakeClock, MockChannel, Watchdog, connect, load_jobs, record_run  # noqa: E402

UTC = timezone.utc
HISTORY_START = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=UTC)


# job name -> (period in minutes, minute offset of the first run)
SCHEDULE = {
    "nightly-backup": (1440, 120),
    "invoice-sync": (60, 5),
    "lead-import": (30, 0),
    "report-export": (1440, 360),
    "cache-warm": (15, 0),
}
ONE_OFFS = [
    (at(1, 12, 0), "tmp-cleanup"),    # a job nobody registered
    (at(2, 9, 20), "report-export"),  # someone reruns the failed export by hand
]
LEAD_IMPORT_EMPTY = {at(1, 4, 30), at(1, 13, 0)} | {
    at(2, 6, 0) + timedelta(minutes=30 * i) for i in range(6)
}


def events(start: datetime, end: datetime) -> list:
    """Every scheduled start time in [start, end), in order."""
    out = [(when, job) for when, job in ONE_OFFS if start <= when < end]
    for job, (period, offset) in SCHEDULE.items():
        when = HISTORY_START + timedelta(minutes=offset)
        while when < end:
            if when >= start:
                out.append((when, job))
            when += timedelta(minutes=period)
    return sorted(out)


def play(conn, clock: FakeClock, hung: list, when: datetime, job: str) -> None:
    """Run one fake job through the real wrapper."""
    clock.set(when)
    n = int((when - HISTORY_START).total_seconds() // 60)

    if job == "invoice-sync" and at(2, 3, 5) < when < at(2, 9, 5):
        return  # the scheduler entry was lost: the job simply stops running

    if job == "cache-warm" and when == at(2, 8, 45):
        # The process is killed mid run: the wrapper never gets to finish the row.
        manager = record_run(conn, job, clock)
        manager.__enter__()
        hung.append(manager)
        return

    try:
        with record_run(conn, job, clock) as run:
            if job == "nightly-backup":
                clock.advance(seconds=700 + n % 90)
                run.counts.update(files=1840 + n % 37, megabytes=512 + n % 11)
            elif job == "invoice-sync":
                clock.advance(seconds=20 + n % 9)
                if when == at(2, 10, 5):
                    raise ConnectionError("accounting API timed out after 30s")
                run.counts.update(invoices=n % 7, payments=n % 4)
            elif job == "lead-import":
                clock.advance(seconds=4 + n % 5)
                if when in LEAD_IMPORT_EMPTY:
                    run.exit_code = 3  # by design: nothing to import
                else:
                    run.counts.update(leads=1 + n % 3)
            elif job == "report-export":
                clock.advance(seconds=95 + n % 20)
                if when == at(2, 6, 0):
                    raise PermissionError("cannot write /srv/reports/daily.csv")
                run.counts.update(rows=310 + n % 50)
            elif job == "cache-warm":
                clock.advance(seconds=30 + n % 12)
                run.counts.update(keys=200 + n % 25)
            else:
                clock.advance(seconds=3)
    except (ConnectionError, PermissionError):
        pass  # the fake scheduler does not care; the run table already knows


def main(out=sys.stdout) -> int:
    def say(text: str = "") -> None:
        print(text, file=out)

    conn = connect(":memory:")
    jobs = load_jobs(os.path.join(ROOT, "jobs.example.json"))
    clock = FakeClock(HISTORY_START)
    channel = MockChannel()
    dog = Watchdog(conn, jobs, channel, clock)
    hung: list = []
    played_until = HISTORY_START

    def watchdog_pass(title: str, when: datetime, channel_down: bool = False) -> None:
        nonlocal played_until
        for event_time, job in events(played_until, when):
            play(conn, clock, hung, event_time, job)
        played_until = when
        clock.set(when)
        channel.fail = channel_down
        before = len(channel.sent)
        raised = dog.check()
        delivered = channel.sent[before:]
        say(f"== {when:%a %H:%M} UTC  {title}")
        say(f"   raised {len(raised)}, delivered to mock channel {len(delivered)}")
        for alert in delivered:
            say("   " + alert.line())
        say()

    say("job-watchdog demo (synthetic history, fake clock, mock alert channel)")
    say(f"registry: {len(jobs)} jobs from jobs.example.json")
    say()

    watchdog_pass("first pass over 33 hours of history", at(2, 8, 59))
    total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    say(f"   (run table held {total_runs} runs at that point; healthy ones raised nothing)")
    say()
    watchdog_pass("second pass, nothing changed: no repeats", at(2, 9, 0))
    watchdog_pass("jobs fixed and rerun: recoveries", at(2, 9, 30))
    watchdog_pass("new failure while the alert channel is down", at(2, 10, 10), channel_down=True)
    watchdog_pass("channel back: the held alert is delivered", at(2, 10, 15))

    say("== status")
    for row in dog.status():
        flags = ", ".join(row["open_alerts"]) or "none"
        say(f"   {row['job']:<16} {row['state']:<22} open: {flags}")
    say()
    say("== alert table by kind")
    for row in conn.execute(
        "SELECT kind, severity, COUNT(*) AS n FROM alerts GROUP BY kind, severity ORDER BY kind"
    ):
        say(f"   {row['kind']:<22} {row['severity']:<9} {row['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
