"""Accuracy gates for the calendar and holder extractors on the hand-labelled sets."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _scorer():
    spec = importlib.util.spec_from_file_location(
        "score_calendar", ROOT / "scripts/score_calendar.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dividend_fields_meet_the_gate():
    s = _scorer().score_dividends()
    assert s["reports"] >= 25
    assert s["field_accuracy"] >= 0.95, s["misses"]


def test_threshold_notice_fields_meet_the_gate():
    s = _scorer().score_notices()
    assert s["reports"] >= 20
    assert s["field_accuracy"] >= 0.93, s["misses"]


def test_meeting_list_rows_meet_the_gate():
    s = _scorer().score_lists()
    assert s["reports"] >= 8
    assert s["precision"] >= 0.9 and s["recall"] >= 0.9, s["misses"]
