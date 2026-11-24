import json
import sys

import pytest

from job_watchdog import record_run, run_command


def last_run(conn):
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def test_ok_run_records_counts_and_duration(rig):
    with record_run(rig.conn, "hourly", rig.clock) as run:
        rig.clock.advance(seconds=12.5)
        run.counts["rows"] = 40
    row = last_run(rig.conn)
    assert row["ok"] == 1 and row["exit_code"] == 0
    assert row["duration_s"] == 12.5
    assert json.loads(row["counts"]) == {"rows": 40}
    assert row["error"] is None
    assert row["finished_at"] > row["started_at"]


def test_row_exists_while_the_job_is_still_running(rig):
    with record_run(rig.conn, "hourly", rig.clock):
        row = last_run(rig.conn)
        assert row["finished_at"] is None and row["ok"] is None


def test_exception_is_recorded_and_reraised(rig):
    with pytest.raises(ValueError):
        with record_run(rig.conn, "hourly", rig.clock) as run:
            run.counts["rows"] = 7
            raise ValueError("bad row 8")
    row = last_run(rig.conn)
    assert row["ok"] == 0 and row["exit_code"] == 1
    assert row["error"] == "ValueError: bad row 8"
    assert json.loads(row["counts"]) == {"rows": 7}  # partial counts survive


def test_sys_exit_code_is_recorded(rig):
    with pytest.raises(SystemExit):
        with record_run(rig.conn, "importer", rig.clock):
            sys.exit(3)
    row = last_run(rig.conn)
    assert row["ok"] == 0 and row["exit_code"] == 3


def test_sys_exit_zero_is_success(rig):
    with pytest.raises(SystemExit):
        with record_run(rig.conn, "importer", rig.clock):
            sys.exit(0)
    assert last_run(rig.conn)["ok"] == 1


def test_body_can_set_a_by_design_exit_code(rig):
    with record_run(rig.conn, "importer", rig.clock) as run:
        run.exit_code = 3
    row = last_run(rig.conn)
    assert row["ok"] == 0 and row["exit_code"] == 3


def test_run_command_records_the_real_exit_code(rig):
    code = run_command(rig.conn, "importer", [sys.executable, "-c", "import sys; sys.exit(4)"])
    assert code == 4
    assert last_run(rig.conn)["exit_code"] == 4
    assert run_command(rig.conn, "importer", [sys.executable, "-c", "pass"]) == 0
    assert last_run(rig.conn)["ok"] == 1


def test_keyboard_interrupt_is_recorded_not_left_running(rig):
    with pytest.raises(KeyboardInterrupt):
        with record_run(rig.conn, "hourly", rig.clock):
            raise KeyboardInterrupt
    row = last_run(rig.conn)
    assert row["finished_at"] is not None
    assert row["ok"] == 0 and row["exit_code"] == 130
    assert row["error"].startswith("KeyboardInterrupt")


def test_run_command_records_a_command_that_cannot_start(rig):
    with pytest.raises(OSError):
        run_command(rig.conn, "hourly", ["no-such-command-job-watchdog-test"])
    row = last_run(rig.conn)
    assert row["ok"] == 0 and row["exit_code"] == 127
    assert "Error" in row["error"]
