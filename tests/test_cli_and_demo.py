import io
import json
import os
import sys

from job_watchdog import connect
from job_watchdog.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_jobs(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": "importer",
                        "interval_minutes": 30,
                        "allowed_exits": [{"code": 3, "reason": "nothing new to import"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_cli_run_check_status(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "w.db")
    base = ["--db", db, "--jobs", write_jobs(tmp_path)]
    monkeypatch.setenv("WATCHDOG_ALERT_CHANNEL", "console")
    py = sys.executable

    assert main(base + ["run", "--job", "importer", "--", py, "-c", "pass"]) == 0
    assert main(base + ["check"]) == 0
    assert "0 new alert(s)" in capsys.readouterr().out

    # A by design exit: the code is passed through, the check stays green.
    assert main(base + ["run", "--job", "importer", "--", py, "-c", "import sys; sys.exit(3)"]) == 3
    assert main(base + ["check"]) == 0
    assert "by design: nothing new to import" in capsys.readouterr().out

    # A real failure: the check exits 1 so the watchdog itself can be watched.
    assert main(base + ["run", "--job", "importer", "--", py, "-c", "import sys; sys.exit(9)"]) == 9
    assert main(base + ["check"]) == 1
    assert "CRITICAL" in capsys.readouterr().out

    assert main(base + ["status"]) == 0
    out = capsys.readouterr().out
    assert "importer" in out and "FAILED exit 9" in out and "open: failure" in out

    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3


def test_cli_rejects_a_bad_registry(tmp_path, capsys):
    bad = tmp_path / "jobs.json"
    bad.write_text('{"jobs": [{"name": "x", "interval_minutes": 5, '
                   '"allowed_exits": [{"code": 2}]}]}', encoding="utf-8")
    assert main(["--db", str(tmp_path / "w.db"), "--jobs", str(bad), "check"]) == 2
    assert "written reason" in capsys.readouterr().err


def test_cli_run_without_a_command(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "w.db"), "run", "--job", "x"]) == 2


def test_cli_run_of_a_missing_command_is_one_line_and_a_recorded_failure(tmp_path, capsys):
    db = str(tmp_path / "w.db")
    code = main(["--db", db, "run", "--job", "x", "--", "no-such-command-job-watchdog-test"])
    assert code == 127
    assert "cannot start" in capsys.readouterr().err
    row = connect(db).execute("SELECT ok, exit_code FROM runs").fetchone()
    assert (row["ok"], row["exit_code"]) == (0, 127)


def test_cli_check_stays_red_while_alerts_are_undelivered(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "w.db")
    base = ["--db", db, "--jobs", write_jobs(tmp_path)]
    monkeypatch.setenv("WATCHDOG_ALERT_CHANNEL", "mock")
    # The alert log path is a directory, so every send fails like a dead channel.
    monkeypatch.setenv("WATCHDOG_ALERT_LOG", str(tmp_path))
    py = sys.executable
    assert main(base + ["run", "--job", "importer", "--", py, "-c", "import sys; sys.exit(9)"]) == 9
    assert main(base + ["check"]) == 1
    # Second pass raises nothing new, but the failure still has not reached anyone.
    assert main(base + ["check"]) == 1
    captured = capsys.readouterr()
    assert "0 new alert(s)" in captured.out
    assert "1 alert(s) could not be delivered" in captured.err

    monkeypatch.setenv("WATCHDOG_ALERT_LOG", str(tmp_path / "alerts.jsonl"))
    assert main(base + ["check"]) == 0
    assert "importer" in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8")


def test_connect_refuses_an_sqlite_too_old_for_returning(monkeypatch):
    import sqlite3

    import pytest

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 31, 1))
    with pytest.raises(RuntimeError, match="SQLite 3.35"):
        connect(":memory:")


def test_demo_output_matches_the_saved_capture():
    sys.path.insert(0, os.path.join(ROOT, "demo"))
    try:
        import run_demo
    finally:
        sys.path.pop(0)
    buffer = io.StringIO()
    assert run_demo.main(out=buffer) == 0
    with open(os.path.join(ROOT, "demo", "output.txt"), encoding="utf-8") as handle:
        assert buffer.getvalue() == handle.read()
