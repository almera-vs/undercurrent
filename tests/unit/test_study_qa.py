"""Regression tests for the statistics and presentation fixes found in review."""

import math
from datetime import UTC, date, datetime

import numpy as np
import pytest
from scipy import stats as sps

from tests.study_fixtures import synthetic
from undercurrent import publish, study

END = datetime(2030, 1, 1, tzinfo=UTC)


def test_cluster_t_equals_plain_t_with_one_event_per_stock() -> None:
    x = np.random.default_rng(4).normal(0.01, 0.05, 40)
    t, p, g = study.cluster_t(x, [f"s{i}" for i in range(40)])
    ref = sps.ttest_1samp(x, 0)
    assert g == 40
    # CR1 with singleton clusters = sqrt(n/(n-1)) * plain SE (population form) = plain SE
    assert t == pytest.approx(ref.statistic, rel=1e-9)
    assert p == pytest.approx(ref.pvalue, rel=1e-6)


def test_cluster_t_widens_with_repeated_events() -> None:
    rng = np.random.default_rng(5)
    base = rng.normal(0.0, 0.05, 20)
    x = np.repeat(base, 8)  # each stock's move counted 8 times
    groups = [f"s{i}" for i in range(20) for _ in range(8)]
    naive = abs(sps.ttest_1samp(x, 0).statistic)
    clustered = abs(study.cluster_t(x, groups)[0])
    assert clustered < naive / 2


def test_dedupe_keeps_one_event_per_signal_stock_and_day() -> None:
    ar = np.zeros(10)
    a = study.EventAR("gpw:2", "k", "PL1", date(2026, 1, 5), ar)
    b = study.EventAR("gpw:1", "k", "PL1", date(2026, 1, 5), ar)
    c = study.EventAR("gpw:3", "k", "PL1", date(2026, 1, 6), ar)
    out = study.dedupe_events([a, b, c])
    assert [e.report_id for e in out] == ["gpw:1", "gpw:3"]


def test_walk_forward_trains_only_on_exited_trades() -> None:
    ev, rt, bench, cal = synthetic(n_events=240)
    inputs = study.cut_inputs(ev, rt, bench, END)
    ars = [e for e in study.compute_event_ars(inputs) if e.signal_key.startswith("inside")]
    year = sorted({e.event_date.year for e in ars})[2]
    cutoff = next(i for i, d in enumerate(cal) if d >= date(year, 1, 1))
    for h in study.HORIZONS:
        train = [e for e in ars if e.event_date.year < year]
        r, _ = study._trade_returns(train, h, cutoff)
        used = [e for e in train if 0 <= study.exit_index(e, h) < cutoff]
        assert len(r) == len([e for e in used if math.isfinite(study.trade_car(e, h))])
        late = [e for e in train if study.exit_index(e, h) >= cutoff]
        if h == 60:
            assert late, "a 60-session trade from December must still be open in January"


def test_verdicts_distinguish_lack_of_data() -> None:
    ev, rt, bench, _ = synthetic(n_events=30)
    df, _ = study.compute_signal_stats(study.cut_inputs(ev, rt, bench, END))
    assert set(df["verdict"]) <= {"too_few", "untested", "noise"}


def test_headline_horizon_follows_the_verdict() -> None:
    def row(h: int, q: float | None, verdict: str, rule: str | None) -> dict:
        return {"q_value": q, "verdict": verdict, "oos_last_rule": rule, "horizon": h}

    rows = {
        ("k", h): row(h, q, "holds_up", "long 5d")
        for h, q in ((1, 0.3), (5, 0.01), (20, 0.5), (60, 0.9))
    }
    assert publish.headline_horizon("k", rows) == 5
    rows = {
        ("k", h): row(h, q, "untested", None)
        for h, q in ((1, 0.04), (5, 0.2), (20, 0.5), (60, 0.9))
    }
    assert publish.headline_horizon("k", rows) == 1
    assert publish.headline_horizon("missing", rows) == 20


def test_within_slo_requires_a_delivered_digest() -> None:
    fin = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)  # 19:00 Warsaw
    ok = {"run_date": date(2026, 11, 9), "finished_at": fin, "status": "ok", "notes": None}
    assert publish.within_slo(ok)
    assert not publish.within_slo({**ok, "status": "failed"})
    assert not publish.within_slo({**ok, "notes": "no GPW session data for 2026-11-09"})
    late = {**ok, "finished_at": datetime(2026, 11, 9, 20, 0, tzinfo=UTC)}  # 21:00 Warsaw
    assert not publish.within_slo(late)


def test_q_values_below_display_precision() -> None:
    env = publish._env("pl")
    q = env.filters["qval"]
    assert q(0.0004) == "< 0,01" and q(0.034) == "0,03" and q(None) == "—"
    assert publish._env("en").filters["qval"](0.0004) == "< 0.01"
