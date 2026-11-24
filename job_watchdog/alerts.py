"""Alert record and alert channels. A channel is anything with send(alert)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional, Protocol

SEVERITIES = ("info", "warning", "critical")


@dataclass(frozen=True)
class Alert:
    job: str
    kind: str
    severity: str
    ref: str
    message: str
    created_at: str

    def line(self) -> str:
        return f"[{self.severity.upper():8}] {self.job}: {self.kind}: {self.message}"


class Channel(Protocol):
    def send(self, alert: Alert) -> None: ...


class MockChannel:
    """Keeps alerts in memory and, if given a path, appends them to a JSONL file.

    Set `fail = True` to simulate an outage of the alert channel.
    """

    def __init__(self, log_path: Optional[str] = None):
        self.sent: list = []
        self.log_path = log_path
        self.fail = False

    def send(self, alert: Alert) -> None:
        if self.fail:
            raise ConnectionError("mock channel is down")
        self.sent.append(alert)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(alert), sort_keys=True) + "\n")


class ConsoleChannel:
    """Prints each alert on one line."""

    def send(self, alert: Alert) -> None:
        print(alert.line())


def channel_from_env(env=None) -> Channel:
    """Build the channel named by WATCHDOG_ALERT_CHANNEL (mock or console)."""
    env = os.environ if env is None else env
    name = env.get("WATCHDOG_ALERT_CHANNEL", "console").strip().lower()
    if name == "mock":
        return MockChannel(env.get("WATCHDOG_ALERT_LOG") or "alerts.jsonl")
    if name == "console":
        return ConsoleChannel()
    raise ValueError(f"unknown alert channel {name!r}; use mock or console")
