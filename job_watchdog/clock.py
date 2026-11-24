"""Clocks. Everything that needs the time takes a callable so tests can control it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Store timestamps as UTC ISO strings so they sort correctly as text."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


class FakeClock:
    """A clock that only moves when told to. Used by the tests and the demo."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("FakeClock needs a timezone aware start")
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now

    def set(self, moment: datetime) -> datetime:
        self.now = moment
        return self.now
