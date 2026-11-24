"""job-watchdog: a run table, a tiny wrapper, and a watchdog that reads the table."""

from .alerts import Alert, ConsoleChannel, MockChannel
from .clock import FakeClock, utcnow
from .db import connect
from .registry import AllowedExit, ConfigError, JobSpec, load_jobs, parse_jobs
from .watchdog import Watchdog
from .wrapper import record_run, run_command

__all__ = [
    "Alert",
    "AllowedExit",
    "ConfigError",
    "ConsoleChannel",
    "FakeClock",
    "JobSpec",
    "MockChannel",
    "Watchdog",
    "connect",
    "load_jobs",
    "parse_jobs",
    "record_run",
    "run_command",
    "utcnow",
]

__version__ = "0.1.0"
