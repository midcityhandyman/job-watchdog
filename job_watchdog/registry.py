"""Job registry: what should run, how often, and which non zero exits are by design."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional


class ConfigError(ValueError):
    """The jobs file is wrong. Raised at load time, never at 3 a.m."""


@dataclass(frozen=True)
class AllowedExit:
    """A non zero exit code that is expected. The reason is mandatory."""

    code: int
    reason: str
    escalate_after: Optional[int] = None  # warn if it repeats this many times in a row


@dataclass(frozen=True)
class JobSpec:
    name: str
    interval_minutes: float
    grace_minutes: float = 0
    max_runtime_minutes: Optional[float] = None
    allowed_exits: tuple = ()

    @property
    def silence_after(self) -> timedelta:
        return timedelta(minutes=self.interval_minutes + self.grace_minutes)

    def allowed(self, exit_code: Optional[int]) -> Optional[AllowedExit]:
        for entry in self.allowed_exits:
            if entry.code == exit_code:
                return entry
        return None


def _positive(job: str, key: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"job {job!r}: {key} must be a positive number")
    return value


def _parse_exit(job: str, raw: dict) -> AllowedExit:
    code = raw.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise ConfigError(f"job {job!r}: allowed exit needs an integer code")
    if code == 0:
        raise ConfigError(f"job {job!r}: exit 0 is already success, do not whitelist it")
    reason = raw.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise ConfigError(
            f"job {job!r}: exit {code} needs a written reason (10 characters or more)"
        )
    escalate = raw.get("escalate_after")
    if escalate is not None:
        if isinstance(escalate, bool) or not isinstance(escalate, int) or escalate < 2:
            raise ConfigError(f"job {job!r}: escalate_after must be an integer of 2 or more")
    return AllowedExit(code=code, reason=reason.strip(), escalate_after=escalate)


def parse_jobs(data: dict) -> list:
    """Validate a config dict and return a list of JobSpec."""
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ConfigError('config must be an object with a "jobs" list')
    specs, seen = [], set()
    for raw in data["jobs"]:
        name = raw.get("name") if isinstance(raw, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("every job needs a name")
        if name in seen:
            raise ConfigError(f"job {name!r} is listed twice")
        seen.add(name)
        if "interval_minutes" not in raw:
            raise ConfigError(f"job {name!r}: interval_minutes is required")
        max_runtime = raw.get("max_runtime_minutes")
        exits = tuple(_parse_exit(name, item) for item in raw.get("allowed_exits", []))
        if len({e.code for e in exits}) != len(exits):
            raise ConfigError(f"job {name!r}: an exit code is whitelisted twice")
        grace = raw.get("grace_minutes", 0)
        if isinstance(grace, bool) or not isinstance(grace, (int, float)) or grace < 0:
            raise ConfigError(f"job {name!r}: grace_minutes must be zero or more")
        specs.append(
            JobSpec(
                name=name,
                interval_minutes=_positive(name, "interval_minutes", raw["interval_minutes"]),
                grace_minutes=grace,
                max_runtime_minutes=(
                    None
                    if max_runtime is None
                    else _positive(name, "max_runtime_minutes", max_runtime)
                ),
                allowed_exits=exits,
            )
        )
    return specs


def load_jobs(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: not valid JSON ({exc})") from exc
    return parse_jobs(data)
