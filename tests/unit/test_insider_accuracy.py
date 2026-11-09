"""Accuracy gate for insider-field extraction on the hand-labelled set."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]
THRESHOLD = 0.98


def _scorer():
    spec = importlib.util.spec_from_file_location(
        "score_insider", ROOT / "scripts/score_insider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_field_level_accuracy_meets_threshold():
    s = _scorer().score()
    assert s["reports"] >= 15
    assert s["field_accuracy"] >= THRESHOLD, s["misses"]
