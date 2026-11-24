from datetime import datetime, timezone

import pytest

from job_watchdog import FakeClock, MockChannel, Watchdog, connect, parse_jobs, record_run

START = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)

JOBS = {
    "jobs": [
        {"name": "hourly", "interval_minutes": 60, "grace_minutes": 10, "max_runtime_minutes": 5},
        {
            "name": "importer",
            "interval_minutes": 30,
            "allowed_exits": [
                {"code": 3, "reason": "nothing new to import", "escalate_after": 3},
                {"code": 4, "reason": "upstream closed on weekends"},
            ],
        },
    ]
}


class Rig:
    """A database, a fake clock, a mock channel and a watchdog wired together."""

    def __init__(self):
        self.conn = connect(":memory:")
        self.clock = FakeClock(START)
        self.channel = MockChannel()
        self.dog = Watchdog(self.conn, parse_jobs(JOBS), self.channel, self.clock)

    def run(self, job, exit_code=0, seconds=2, error=None, **counts):
        """Record one finished run through the real wrapper."""
        try:
            with record_run(self.conn, job, self.clock) as run:
                self.clock.advance(seconds=seconds)
                run.counts.update(counts)
                if error:
                    raise RuntimeError(error)
                run.exit_code = exit_code
        except RuntimeError:
            pass
        return run.id

    def start_only(self, job):
        """Simulate a process that dies mid run: the row never gets finished."""
        manager = record_run(self.conn, job, self.clock)
        handle = manager.__enter__()
        self._keep = getattr(self, "_keep", []) + [manager]
        return handle.id

    def kinds(self, alerts=None):
        source = self.channel.sent if alerts is None else alerts
        return [(a.job, a.kind) for a in source]


@pytest.fixture
def rig():
    return Rig()
