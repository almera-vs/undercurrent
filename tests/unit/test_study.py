import math
from datetime import UTC, datetime

import numpy as np
import pytest

from tests.study_fixtures import synthetic
from undercurrent import study

END = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def stats():
    ev, rt, bench, _ = synthetic()
    inputs = study.cut_inputs(ev, rt, bench, END)
    return study.compute_signal_stats(inputs)


def test_planted_effect_detected(stats):
    df, _ = stats
    ii = df.filter((df["signal_key"] == "inside_info:large_contract") & (df["horizon"] == 1))
    row = ii.row(0, named=True)
    assert row["mean_car"] == pytest.approx(0.03, abs=0.006)
    assert row["ci_lo"] > 0 and row["q_value"] < 0.05
    assert row["verdict"] == "holds_up"
    ht = df.filter(df["signal_key"] == "holder_threshold")
    assert set(ht["verdict"]) == {"noise"}
    assert ht["q_value"].min() > 0.05


def test_paths_have_band(stats):
    _, paths = stats
    p = paths.filter(paths["signal_key"] == "inside_info:large_contract")
    assert p["day"].to_list() == list(range(-5, 21))
    assert (p["lo"] <= p["mean_car"]).all() and (p["mean_car"] <= p["hi"]).all()


def test_benjamini_hochberg_matches_reference():
    p = np.array([0.01, 0.04, 0.03, 0.005, np.nan])
    q = study.benjamini_hochberg(p)
    expected = [0.02, 0.04, 0.04, 0.02]
    assert np.allclose(q[:4], expected) and math.isnan(q[4])


def test_deflated_sharpe_in_unit_interval():
    r = np.random.default_rng(1).normal(0.01, 0.05, 200)
    d = study.deflated_sharpe(r, n_trials=40, sr_trials_var=0.01)
    assert 0.0 <= d <= 1.0
    worse = study.deflated_sharpe(r, n_trials=4000, sr_trials_var=0.05)
    assert worse < d


def test_walk_forward_uses_only_past():
    ev, rt, bench, _ = synthetic(n_events=120)
    inputs = study.cut_inputs(ev, rt, bench, END)
    ars = [e for e in study.compute_event_ars(inputs) if e.signal_key.startswith("inside")]
    years = sorted({e.event_date.year for e in ars})
    first = [e for e in ars if e.event_date.year == years[0]]
    # the first year has no history, so no rule can be chosen for it
    assert study.choose_rule([e for e in ars if e.event_date.year < years[0]]) is None
    oos, _ = study.walk_forward(ars)
    assert len(oos) <= len(ars) - len(first)
