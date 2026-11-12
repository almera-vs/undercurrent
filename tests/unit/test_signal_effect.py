"""Shrunk event-study effects as a point-in-time input of the forecast."""

from datetime import date

import numpy as np
import pytest

from tests.study_fixtures import sessions
from undercurrent import signal_effect as se
from undercurrent.signal_effect import Event


def _events(rng, key, n, mean, sd, cal_len, stocks=40, start=0, stop=400):
    out = []
    for k in range(n):
        entry = int(rng.integers(start, stop))
        out.append(
            Event(
                key,
                f"S{k % stocks:03d}",
                entry,
                entry + se.HORIZON,
                mean + sd * rng.standard_normal(),
            )
        )
    return out


CAL = sessions(800)


def test_a_real_kind_keeps_its_effect_and_a_noisy_one_does_not():
    rng = np.random.default_rng(1)
    evs = (
        _events(rng, "strong", 400, 0.05, 0.05, len(CAL))
        + _events(rng, "zero_a", 400, 0.0, 0.05, len(CAL))
        + _events(rng, "zero_b", 400, 0.0, 0.05, len(CAL))
        + _events(rng, "noisy", 15, 0.02, 0.15, len(CAL))
    )
    eff = se.estimate(evs, 500, 2019, CAL)
    assert eff["strong"].shrunk > 0.03
    assert abs(eff["zero_a"].shrunk) < 0.01
    # a kind with few, noisy events is pulled almost all the way to the common part
    assert abs(eff["noisy"].shrunk - eff["noisy"].common) < abs(eff["noisy"].mean - eff["*"].mean)


def test_a_common_drift_is_one_effect_not_many():
    rng = np.random.default_rng(2)
    evs = [e for k in ("a", "b", "c", "d") for e in _events(rng, k, 300, -0.02, 0.04, len(CAL))]
    eff = se.estimate(evs, 500, 2019, CAL)
    assert eff["*"].common == pytest.approx(-0.02, abs=0.006)
    for k in "abcd":
        assert eff[k].shrink == pytest.approx(0.0, abs=0.3)
        assert eff[k].shrunk == pytest.approx(eff["*"].common, abs=0.004)


def test_no_evidence_counts_as_nothing():
    rng = np.random.default_rng(3)
    evs = [e for k in ("a", "b", "c") for e in _events(rng, k, 200, 0.0, 0.05, len(CAL))]
    eff = se.estimate(evs, 500, 2019, CAL)
    assert abs(eff["*"].common) < 0.003
    assert se.estimate(evs[:5], 500, 2019, CAL) == {}  # too few events at all


def test_only_trades_closed_before_the_cut_count():
    rng = np.random.default_rng(4)
    early = _events(rng, "a", 200, 0.0, 0.03, len(CAL), stop=300)
    late = _events(rng, "a", 200, 0.30, 0.03, len(CAL), start=500, stop=700)
    eff = se.estimate(early + late, 450, 2019, CAL)
    assert eff["a"].n == len(early)
    assert abs(eff["a"].mean) < 0.01


def test_daily_effect_counts_the_latest_report_of_each_kind_and_fades():
    cal = sessions(300, start=date(2017, 6, 1))  # history in 2017, the burst in 2018
    rng = np.random.default_rng(5)
    history = [
        e for k in ("a", "b", "c") for e in _events(rng, k, 300, -0.03, 0.02, len(cal), stop=100)
    ]
    t0 = 260
    burst = [Event("a", "X", t0 + d, t0 + d + se.HORIZON, float("nan")) for d in range(5)]
    eff, by_year = se.daily_effect(history + burst, cal, ["X"])
    a = se.effect_of(by_year[cal[t0].year], "a")
    assert a < 0
    # five daily filings of one kind are one effect, from the latest, not five
    assert eff[0, t0 + 4] == pytest.approx(a)
    assert eff[0, t0 + 4 + se.HORIZON // 2] == pytest.approx(a / 2)
    assert eff[0, t0 - 1] == 0.0 and eff[0, t0 + 4 + se.HORIZON] == 0.0
    frame = se.effects_frame(by_year)
    assert set(frame.columns) >= {"year", "signal_key", "shrunk", "common"}


def test_effects_are_point_in_time_under_truncation():
    cal = sessions(700)
    rng = np.random.default_rng(6)
    evs = [e for k in ("a", "b", "c") for e in _events(rng, k, 300, 0.01, 0.04, len(cal), stop=650)]
    full, _ = se.daily_effect(evs, cal, [f"S{i:03d}" for i in range(40)])
    cut = 500
    seen = [
        e if e.exit_t < cut else Event(e.key, e.isin, e.entry_t, 10_000, float("nan"))
        for e in evs
        if e.entry_t < cut
    ]
    part, _ = se.daily_effect(seen, cal[:cut], [f"S{i:03d}" for i in range(40)])
    np.testing.assert_allclose(part, full[:, :cut])
