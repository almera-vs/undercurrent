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


def test_reaction_before_entry_is_not_tradable():
    """A move on day +1 happens before the first tradable close after the digest."""
    ev, rt, bench, _ = synthetic(effect_day=1)
    df, paths = study.compute_signal_stats(study.cut_inputs(ev, rt, bench, END))
    ii = df.filter((df["signal_key"] == "inside_info:large_contract") & (df["horizon"] == 1))
    row = ii.row(0, named=True)
    assert abs(row["mean_car"]) < 0.006 and row["verdict"] == "noise"
    p = paths.filter(paths["signal_key"] == "inside_info:large_contract")
    jump = p.filter(p["day"] == 1)["mean_car"][0] - p.filter(p["day"] == 0)["mean_car"][0]
    assert jump == pytest.approx(0.03, abs=0.006)  # still visible on the chart


def test_entry_session_follows_the_digest():
    from datetime import date

    cal = [date(2026, 11, d) for d in (5, 6, 9, 10, 12, 13)]  # 11th: no session
    w = study.WARSAW

    def at(d: int, hh: int, mm: int = 0) -> datetime:
        return datetime(2026, 11, d, hh, mm, tzinfo=w)

    assert study.entry_offset(at(5, 10), date(2026, 11, 5), cal) == 1  # daytime → next close
    assert study.entry_offset(at(5, 18), date(2026, 11, 6), cal) == 0  # after close, before digest
    assert study.entry_offset(at(5, 19), date(2026, 11, 6), cal) == 1  # after the cut-off
    assert study.entry_offset(at(5, 21), date(2026, 11, 6), cal) == 1  # after the digest
    assert study.entry_offset(at(7, 12), date(2026, 11, 9), cal) == 1  # Saturday → Monday digest
    assert study.entry_offset(at(11, 12), date(2026, 11, 12), cal) == 1  # holiday → Thu digest
    assert study.entry_offset(at(13, 12), date(2026, 11, 13), cal) == -1  # next session unknown


def test_digest_window_matches_digest_day():
    """Every report is in exactly one digest: the one ``digest_day`` assigns it to."""
    from datetime import date, timedelta

    cal = [date(2026, 11, d) for d in (5, 6, 9, 10, 12, 13)]  # weekend 7-8, holiday 11
    w = study.WARSAW
    t0 = datetime(2026, 11, 5, 18, 31, tzinfo=w)
    for k in range(0, 8 * 24 * 60 - 60, 7):  # every 7 minutes up to the last cut-off
        ts = t0 + timedelta(minutes=k)
        hits = []
        for d in cal[1:]:
            start, end = study.digest_window(d, cal)
            if start < ts <= end:
                hits.append(d)
        assert hits == [study.digest_day(ts, cal)], ts


def test_digest_window_edges():
    from datetime import date

    cal = [date(2026, 11, d) for d in (5, 6, 9, 10, 12, 13)]
    w = study.WARSAW
    start, end = study.digest_window(date(2026, 11, 9), cal)  # Monday
    assert start == datetime(2026, 11, 6, 18, 30, tzinfo=w)  # Friday evening is covered
    assert end == datetime(2026, 11, 9, 18, 30, tzinfo=w)
    # a day without a session shows the window so far; the next session covers it again
    tue = datetime(2026, 11, 10, 18, 30, tzinfo=w)
    assert study.digest_window(date(2026, 11, 11), cal)[0] == tue
    assert study.digest_window(date(2026, 11, 12), cal)[0] == tue
    # no earlier session known: fall back to the previous weekday
    assert study.digest_window(date(2026, 11, 9), [])[0] == start
