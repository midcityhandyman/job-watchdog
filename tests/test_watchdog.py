"""Behaviour of one watchdog pass. Both registered jobs get a healthy run first
unless the test is about silence, so each test sees only the alert it is about."""


def healthy(rig):
    rig.run("hourly")
    rig.run("importer")


def by_kind(rig, kind):
    return [a for a in rig.channel.sent if a.kind == kind]


# ------------------------------------------------------------------ failure


def test_healthy_runs_raise_nothing(rig):
    healthy(rig)
    assert rig.dog.check() == []
    assert rig.channel.sent == []


def test_failed_run_raises_one_critical_alert(rig):
    healthy(rig)
    run_id = rig.run("hourly", error="disk full", seconds=9)
    raised = rig.dog.check()
    assert rig.kinds(raised) == [("hourly", "failure")]
    alert = raised[0]
    assert alert.severity == "critical" and alert.ref == f"run:{run_id}"
    assert "disk full" in alert.message and "9.0s" in alert.message


def test_same_failure_is_not_alerted_twice(rig):
    healthy(rig)
    rig.run("hourly", error="disk full")
    rig.dog.check()
    assert rig.dog.check() == []
    assert len(rig.channel.sent) == 1


def test_two_failures_are_two_alerts(rig):
    healthy(rig)
    rig.run("hourly", error="first")
    rig.run("hourly", error="second")
    assert rig.kinds(rig.dog.check()) == [("hourly", "failure")] * 2


# ------------------------------------------------------------------ silence


def test_no_silence_inside_interval_plus_grace(rig):
    healthy(rig)
    rig.clock.advance(minutes=29)  # importer: 30 minute interval, no grace
    assert rig.dog.check() == []


def test_silence_past_the_expected_interval(rig):
    healthy(rig)
    rig.clock.advance(minutes=45)
    raised = rig.dog.check()
    assert rig.kinds(raised) == [("importer", "silence")]
    assert raised[0].severity == "critical"
    assert "overdue by 15m" in raised[0].message
    rig.clock.advance(minutes=30)  # now the hourly job is late too
    assert rig.kinds(rig.dog.check()) == [("hourly", "silence")]  # importer not repeated


def test_a_new_silence_after_a_new_run_alerts_again(rig):
    healthy(rig)
    rig.clock.advance(minutes=45)
    rig.dog.check()
    rig.run("importer")
    rig.run("hourly")
    rig.clock.advance(minutes=45)
    assert ("importer", "silence") in rig.kinds(rig.dog.check())
    assert len(by_kind(rig, "silence")) == 2


def test_job_that_never_ran_is_flagged_once(rig):
    rig.run("hourly")
    assert rig.kinds(rig.dog.check()) == [("importer", "silence")]
    assert rig.channel.sent[0].ref == "never"
    assert rig.dog.check() == []


def test_failed_runs_do_not_hide_as_silence(rig):
    healthy(rig)
    rig.clock.advance(minutes=20)
    rig.run("importer", error="boom")
    assert rig.kinds(rig.dog.check()) == [("importer", "failure")]


# ------------------------------------------------------ whitelisted exits


def test_whitelisted_exit_is_info_with_the_reason_not_a_failure(rig):
    healthy(rig)
    rig.run("importer", exit_code=3)
    raised = rig.dog.check()
    assert rig.kinds(raised) == [("importer", "expected_exit")]
    assert raised[0].severity == "info"
    assert "nothing new to import" in raised[0].message


def test_non_whitelisted_code_on_the_same_job_still_fails(rig):
    healthy(rig)
    rig.run("importer", exit_code=5)
    raised = rig.dog.check()
    assert rig.kinds(raised) == [("importer", "failure")]
    assert raised[0].severity == "critical"


def test_whitelist_is_per_job(rig):
    healthy(rig)
    rig.run("hourly", exit_code=3)  # exit 3 is only whitelisted for importer
    assert rig.kinds(rig.dog.check()) == [("hourly", "failure")]


def test_whitelisted_exit_counts_as_alive(rig):
    healthy(rig)
    for _ in range(2):
        rig.clock.advance(minutes=25)
        rig.run("importer", exit_code=4)
    assert by_kind(rig, "silence") == []
    assert all(a.kind == "expected_exit" for a in rig.dog.check())


def test_whitelisted_exit_escalates_after_a_streak_and_only_once(rig):
    healthy(rig)
    rig.run("importer", exit_code=3)
    rig.run("importer", exit_code=3)
    rig.dog.check()
    assert by_kind(rig, "expected_exit_streak") == []
    rig.run("importer", exit_code=3)  # third in a row, escalate_after is 3
    rig.dog.check()
    rig.run("importer", exit_code=3)  # fourth: same streak, no second warning
    rig.dog.check()
    streaks = by_kind(rig, "expected_exit_streak")
    assert len(streaks) == 1 and streaks[0].severity == "warning"
    assert "3 runs in a row" in streaks[0].message
    assert len(by_kind(rig, "expected_exit")) == 4


def test_streak_is_broken_by_a_clean_run(rig):
    healthy(rig)
    rig.run("importer", exit_code=3)
    rig.run("importer", exit_code=3)
    rig.run("importer")
    rig.run("importer", exit_code=3)
    rig.dog.check()
    assert by_kind(rig, "expected_exit_streak") == []


def test_exit_without_escalate_after_never_escalates(rig):
    healthy(rig)
    for _ in range(6):
        rig.run("importer", exit_code=4)
    rig.dog.check()
    assert by_kind(rig, "expected_exit_streak") == []


# -------------------------------------------------- stuck and unregistered


def test_run_that_never_finishes_is_stuck(rig):
    healthy(rig)
    run_id = rig.start_only("hourly")
    rig.clock.advance(minutes=4)
    assert rig.dog.check() == []  # still inside max_runtime
    rig.clock.advance(minutes=2)
    raised = rig.dog.check()
    assert rig.kinds(raised) == [("hourly", "stuck")]
    assert raised[0].ref == f"run:{run_id}"
    assert rig.dog.check() == []


def test_job_without_max_runtime_is_never_stuck(rig):
    healthy(rig)
    rig.start_only("importer")
    rig.clock.advance(minutes=20)
    assert rig.dog.check() == []


def test_unregistered_job_is_reported_once(rig):
    healthy(rig)
    rig.run("ghost")
    rig.run("ghost", error="nobody is watching")
    assert rig.kinds(rig.dog.check()) == [("ghost", "unregistered")]
    assert rig.dog.check() == []


# ----------------------------------------------------------------- recovery


def test_clean_run_closes_open_alerts_and_says_so(rig):
    healthy(rig)
    rig.run("hourly", error="disk full")
    rig.dog.check()
    assert rig.dog.status()[0]["open_alerts"] == ["failure"]
    rig.run("hourly")
    raised = rig.dog.check()
    assert rig.kinds(raised) == [("hourly", "recovered")]
    assert "failure" in raised[0].message
    assert rig.dog.status()[0]["open_alerts"] == []
    assert rig.dog.status()[0]["state"] == "ok"


def test_no_recovery_notice_when_nothing_was_open(rig):
    healthy(rig)
    rig.dog.check()
    rig.run("hourly")
    assert rig.dog.check() == []


def test_by_design_exit_recovers_silence_but_not_its_own_streak(rig):
    healthy(rig)
    rig.clock.advance(minutes=45)
    rig.dog.check()  # importer silence
    rig.run("importer", exit_code=3)
    assert ("importer", "recovered") in rig.kinds(rig.dog.check())
    rig.run("importer", exit_code=3)
    rig.run("importer", exit_code=3)
    rig.dog.check()
    assert rig.dog.status()[1]["open_alerts"] == ["expected_exit_streak"]
    assert rig.dog.status()[1]["state"] == "exit 3 (by design)"
    rig.run("importer")
    rig.dog.check()
    assert rig.dog.status()[1]["open_alerts"] == []


# ----------------------------------------------------------------- delivery


def test_alert_is_held_and_retried_when_the_channel_is_down(rig):
    healthy(rig)
    rig.run("hourly", error="disk full")
    rig.channel.fail = True
    raised = rig.dog.check()
    assert len(raised) == 1 and rig.channel.sent == []
    pending = rig.conn.execute("SELECT COUNT(*) FROM alerts WHERE delivered = 0").fetchone()[0]
    assert pending == 1
    rig.channel.fail = False
    assert rig.dog.check() == []  # nothing new raised
    assert rig.kinds() == [("hourly", "failure")]  # but the held one went out
    assert rig.conn.execute("SELECT COUNT(*) FROM alerts WHERE delivered = 0").fetchone()[0] == 0
    rig.dog.check()
    assert len(rig.channel.sent) == 1  # and only once


def test_mock_channel_writes_jsonl(tmp_path, rig):
    import json

    rig.channel.log_path = str(tmp_path / "alerts.jsonl")
    healthy(rig)
    rig.run("hourly", error="disk full")
    rig.dog.check()
    lines = (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "failure"
