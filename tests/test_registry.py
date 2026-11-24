import os

import pytest

from job_watchdog import ConfigError, load_jobs, parse_jobs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def job(**overrides):
    base = {"name": "importer", "interval_minutes": 30}
    base.update(overrides)
    return {"jobs": [base]}


def test_example_file_loads():
    specs = load_jobs(os.path.join(ROOT, "jobs.example.json"))
    names = [s.name for s in specs]
    assert "lead-import" in names and len(names) == len(set(names))
    lead = specs[names.index("lead-import")]
    assert lead.allowed(3).reason.startswith("source inbox")
    assert lead.allowed(1) is None and lead.allowed(0) is None


def test_silence_window_is_interval_plus_grace():
    spec = parse_jobs(job(grace_minutes=10))[0]
    assert spec.silence_after.total_seconds() == 40 * 60


@pytest.mark.parametrize(
    "exits, fragment",
    [
        ([{"code": 3}], "written reason"),
        ([{"code": 3, "reason": "  "}], "written reason"),
        ([{"code": 3, "reason": "expected"}], "written reason"),  # too short to mean anything
        ([{"code": 0, "reason": "zero is fine really"}], "already success"),
        ([{"code": "3", "reason": "nothing new to import"}], "integer code"),
        ([{"code": 3, "reason": "nothing new to import", "escalate_after": 1}], "escalate_after"),
        (
            [
                {"code": 3, "reason": "nothing new to import"},
                {"code": 3, "reason": "also nothing to import"},
            ],
            "whitelisted twice",
        ),
    ],
)
def test_bad_whitelist_entries_are_rejected(exits, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_jobs(job(allowed_exits=exits))


@pytest.mark.parametrize(
    "config, fragment",
    [
        ({"jobs": [{"interval_minutes": 5}]}, "needs a name"),
        ({"jobs": [{"name": "a"}]}, "interval_minutes is required"),
        (job(interval_minutes=0), "positive number"),
        (job(interval_minutes="60"), "positive number"),
        (job(grace_minutes=-1), "zero or more"),
        (job(max_runtime_minutes=0), "positive number"),
        ({"jobs": [job()["jobs"][0], job()["jobs"][0]]}, "listed twice"),
        ([], "jobs"),
    ],
)
def test_bad_jobs_are_rejected(config, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_jobs(config)


def test_invalid_json_is_a_config_error(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_jobs(str(path))
