"""The watchdog: reads the run table, compares it to the registry, raises alerts.

It never runs jobs and never trusts a job to report its own death. Each call
to check() is one pass; schedule it more often than your most frequent job.

Alert kinds:
    failure               a run ended non zero and the code is not whitelisted
    silence               no run started within interval + grace
    stuck                 a run has been "running" longer than max_runtime
    expected_exit         a whitelisted non zero exit (info, carries the reason)
    expected_exit_streak  a whitelisted exit repeated escalate_after times in a row
    unregistered          the run table has a job the registry does not know
    recovered             a healthy run closed earlier open alerts
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Callable, Iterable, Optional

from .alerts import Alert, Channel
from .clock import from_iso, to_iso, utcnow
from .registry import JobSpec

# Kinds that stay open until a later healthy run closes them.
OPEN_KINDS = ("failure", "silence", "stuck", "expected_exit_streak")


def _span(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = [f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m"]
    return " ".join(p for p in parts if p)


class Watchdog:
    def __init__(
        self,
        conn: sqlite3.Connection,
        jobs: Iterable[JobSpec],
        channel: Channel,
        clock: Callable = utcnow,
    ):
        self.conn = conn
        self.jobs = list(jobs)
        self.channel = channel
        self.clock = clock
        self._raised: list = []

    # ------------------------------------------------------------------ pass

    def check(self) -> list:
        """Run one pass. Returns the alerts raised for the first time in it."""
        now = self.clock()
        self._raised = []
        self._retry_undelivered()
        for spec in self.jobs:
            self._judge_finished_runs(spec, now)
            self._check_stuck(spec, now)
            self._check_silence(spec, now)
        self._check_unregistered(now)
        self.conn.commit()
        return list(self._raised)

    # ---------------------------------------------------------------- checks

    def _judge_finished_runs(self, spec: JobSpec, now) -> None:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE job = ? AND finished_at IS NOT NULL "
            "AND checked_at IS NULL ORDER BY id",
            (spec.name,),
        ).fetchall()
        for run in rows:
            ref = f"run:{run['id']}"
            allowed = None if run["ok"] else spec.allowed(run["exit_code"])
            if run["ok"]:
                self._recover(spec.name, run, now, OPEN_KINDS)
            elif allowed is not None:
                self._raise(
                    spec.name, "expected_exit", "info", ref, now,
                    f"exit {allowed.code} is by design: {allowed.reason}",
                )
                # A by design exit still proves the job is alive and behaving.
                self._recover(spec.name, run, now, ("failure", "silence", "stuck"))
                self._check_streak(spec, run, allowed, now)
            else:
                detail = run["error"] or "no error text recorded"
                self._raise(
                    spec.name, "failure", "critical", ref, now,
                    f"run {run['id']} failed with exit {run['exit_code']} after "
                    f"{run['duration_s']:.1f}s: {detail}",
                )
            self.conn.execute(
                "UPDATE runs SET checked_at = ? WHERE id = ?", (to_iso(now), run["id"])
            )

    def _check_streak(self, spec: JobSpec, run, allowed, now) -> None:
        if not allowed.escalate_after:
            return
        history = self.conn.execute(
            "SELECT id, exit_code FROM runs WHERE job = ? AND finished_at IS NOT NULL "
            "AND id <= ? ORDER BY id DESC",
            (spec.name, run["id"]),
        )
        count, first_id = 0, run["id"]
        for past in history:
            if past["exit_code"] != allowed.code:
                break
            count, first_id = count + 1, past["id"]
        if count >= allowed.escalate_after:
            self._raise(
                spec.name, "expected_exit_streak", "warning", f"streak-from-run:{first_id}", now,
                f"exit {allowed.code} is whitelisted ({allowed.reason}) but has now happened "
                f"{count} runs in a row; check that it is still by design",
            )

    def _check_stuck(self, spec: JobSpec, now) -> None:
        if spec.max_runtime_minutes is None:
            return
        limit = timedelta(minutes=spec.max_runtime_minutes)
        rows = self.conn.execute(
            "SELECT id, started_at FROM runs WHERE job = ? AND finished_at IS NULL ORDER BY id",
            (spec.name,),
        ).fetchall()
        for run in rows:
            age = now - from_iso(run["started_at"])
            if age > limit:
                self._raise(
                    spec.name, "stuck", "warning", f"run:{run['id']}", now,
                    f"run {run['id']} started {_span(age)} ago and never finished "
                    f"(limit {_span(limit)}); it is hung or it died without reporting",
                )

    def _check_silence(self, spec: JobSpec, now) -> None:
        last = self.conn.execute(
            "SELECT id, started_at FROM runs WHERE job = ? ORDER BY started_at DESC, id DESC "
            "LIMIT 1",
            (spec.name,),
        ).fetchone()
        if last is None:
            self._raise(
                spec.name, "silence", "warning", "never", now,
                "registered but no run has ever been recorded; is it scheduled, "
                "and does it go through the wrapper?",
            )
            return
        quiet = now - from_iso(last["started_at"])
        if quiet > spec.silence_after:
            self._raise(
                spec.name, "silence", "critical", f"after-run:{last['id']}", now,
                f"no run for {_span(quiet)}; expected every "
                f"{_span(timedelta(minutes=spec.interval_minutes))} "
                f"(grace {_span(timedelta(minutes=spec.grace_minutes))}), "
                f"overdue by {_span(quiet - spec.silence_after)}",
            )

    def _check_unregistered(self, now) -> None:
        known = {spec.name for spec in self.jobs}
        for row in self.conn.execute("SELECT DISTINCT job FROM runs ORDER BY job").fetchall():
            if row["job"] not in known:
                self._raise(
                    row["job"], "unregistered", "warning", "job", now,
                    "runs are being recorded for a job the registry does not list, "
                    "so nobody is watching it for failure or silence",
                )

    # -------------------------------------------------------------- plumbing

    def _recover(self, job: str, run, now, kinds) -> None:
        marks = ",".join("?" for _ in kinds)
        closed = self.conn.execute(
            f"UPDATE alerts SET resolved_at = ? WHERE job = ? AND resolved_at IS NULL "
            f"AND kind IN ({marks}) RETURNING kind",
            (to_iso(now), job, *kinds),
        ).fetchall()
        if closed:
            names = ", ".join(sorted({row["kind"] for row in closed}))
            self._raise(
                job, "recovered", "info", f"run:{run['id']}", now,
                f"run {run['id']} finished cleanly; closed: {names}",
            )

    def _raise(self, job, kind, severity, ref, now, message) -> Optional[Alert]:
        """Record an alert once per (job, kind, ref) and try to deliver it."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO alerts (created_at, job, kind, severity, ref, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (to_iso(now), job, kind, severity, ref, message),
        )
        if cur.rowcount == 0:
            return None  # already alerted about exactly this
        alert = Alert(job, kind, severity, ref, message, to_iso(now))
        self._raised.append(alert)
        self._deliver(cur.lastrowid, alert)
        return alert

    def _deliver(self, alert_id: int, alert: Alert) -> bool:
        try:
            self.channel.send(alert)
        except Exception:
            return False  # stays delivered = 0 and is retried on the next pass
        self.conn.execute("UPDATE alerts SET delivered = 1 WHERE id = ?", (alert_id,))
        return True

    def _retry_undelivered(self) -> None:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE delivered = 0 ORDER BY id"
        ).fetchall()
        for row in rows:
            alert = Alert(
                row["job"], row["kind"], row["severity"], row["ref"],
                row["message"], row["created_at"],
            )
            if not self._deliver(row["id"], alert):
                break  # channel is still down; do not hammer it

    # ---------------------------------------------------------------- status

    def status(self) -> list:
        """One summary dict per registered job, for the CLI and the demo."""
        out = []
        for spec in self.jobs:
            last = self.conn.execute(
                "SELECT * FROM runs WHERE job = ? ORDER BY started_at DESC, id DESC LIMIT 1",
                (spec.name,),
            ).fetchone()
            if last is None:
                state = "never ran"
            elif last["finished_at"] is None:
                state = "running"
            elif last["ok"]:
                state = "ok"
            elif spec.allowed(last["exit_code"]):
                state = f"exit {last['exit_code']} (by design)"
            else:
                state = f"FAILED exit {last['exit_code']}"
            open_kinds = [
                row["kind"]
                for row in self.conn.execute(
                    "SELECT DISTINCT kind FROM alerts WHERE job = ? AND resolved_at IS NULL "
                    f"AND kind IN ({','.join('?' for _ in OPEN_KINDS)}) ORDER BY kind",
                    (spec.name, *OPEN_KINDS),
                )
            ]
            out.append(
                {
                    "job": spec.name,
                    "last_started": last["started_at"] if last else None,
                    "state": state,
                    "open_alerts": open_kinds,
                }
            )
        return out
