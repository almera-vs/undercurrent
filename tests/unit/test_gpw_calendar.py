"""Exchange holidays: the calendar, the nightly date, the gate, the run and the watchdog."""

import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from undercurrent import gpw_calendar, pipeline, publish

WARSAW = ZoneInfo("Europe/Warsaw")
GATE = Path(__file__).parents[2] / "scripts" / "nightly_gate.sh"


def test_2026_matches_the_exchange_resolution():
    """GPW resolution 753/2025: ten weekdays without a session in 2026."""
    closed = sorted(d for d in gpw_calendar.closures(2026) if d.weekday() < 5)
    assert closed == [
        date(2026, 1, 1),
        date(2026, 1, 6),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 6, 4),
        date(2026, 11, 11),
        date(2026, 12, 24),
        date(2026, 12, 25),
        date(2026, 12, 31),
    ]


def test_easter_and_one_off_closures():
    assert gpw_calendar.easter(2026) == date(2026, 4, 5)
    assert gpw_calendar.easter(2016) == date(2016, 3, 27)
    assert gpw_calendar.closed_reason(date(2018, 11, 12)) == "Independence centenary"
    assert gpw_calendar.closed_reason(date(2026, 11, 12)) is None
    assert gpw_calendar.closed_reason(date(2026, 11, 14)) is None  # a Saturday: not a holiday
    assert gpw_calendar.last_session(date(2026, 12, 27)) == date(2026, 12, 23)


def test_cli_for_the_gate(capsys):
    assert gpw_calendar.main(["closed", "2026-11-11"]) == 0
    assert capsys.readouterr().out.strip() == "Independence Day"
    assert gpw_calendar.main(["closed", "2026-11-12"]) == 1
    assert gpw_calendar.main(["last-session", "2026-11-11"]) == 0
    assert capsys.readouterr().out.strip() == "2026-11-10"


def test_nightly_date_and_watchdog_skip_holidays():
    at = datetime(2026, 11, 11, 18, 47, tzinfo=WARSAW)
    assert pipeline.nightly_date(at) == date(2026, 11, 10)
    assert pipeline.nightly_date(datetime(2026, 11, 12, 9, 0, tzinfo=WARSAW)) == date(2026, 11, 10)
    assert publish.expected_run_date(datetime(2026, 11, 11, 21, 0, tzinfo=WARSAW)) == date(
        2026, 11, 10
    )


def test_a_holiday_run_is_not_a_slo_miss():
    run = {
        "run_date": date(2026, 11, 11),
        "finished_at": datetime(2026, 11, 11, 17, 37, tzinfo=UTC),
        "status": "ok",
        "notes": "GPW closed: Independence Day",
    }
    assert publish.within_slo(run) is None
    bits = [("Run {s}", {"s": "ok"}), ("exchange closed ({h})", {"h": "Independence Day"})]
    assert (
        publish.health_text(bits, "pl") == "Przebieg: ok · giełda nieczynna (Święto Niepodległości)"
    )


def _gate(event: str, now: str, digest_out: bool, requested: str = "today") -> dict[str, str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    env = {
        **os.environ,
        "NOW": now,
        "DIGEST_EXISTS": "true" if digest_out else "false",
        "PYTHON": sys.executable,
    }
    out = subprocess.run(
        [bash, str(GATE), event, requested], env=env, capture_output=True, text=True, check=True
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def test_gate_skips_a_holiday_evening():
    g = _gate("schedule", "2026-11-11 18:47", digest_out=True)
    assert g["date"] == "2026-11-10" and g["go"] == "false"
    assert "GPW closed (Independence Day)" in g["reason"]


def test_gate_runs_the_last_session_if_its_digest_is_missing():
    g = _gate("schedule", "2026-11-11 18:47", digest_out=False)
    assert (g["date"], g["go"]) == ("2026-11-10", "true")


def test_gate_on_a_session_day_is_unchanged():
    assert _gate("schedule", "2026-11-12 18:47", digest_out=False)["date"] == "2026-11-12"
    assert _gate("schedule", "2026-11-12 18:00", digest_out=False)["go"] == "false"
    by_hand = _gate("workflow_dispatch", "2026-11-11 18:47", digest_out=True)
    assert (by_hand["date"], by_hand["go"]) == ("2026-11-11", "true")
