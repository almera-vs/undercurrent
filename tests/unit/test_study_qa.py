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
        r = study._trade_returns(train, h, cutoff).ret
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


def test_holds_up_needs_a_significant_walk_forward() -> None:
    """A positive but insignificant out-of-sample mean is ``unconfirmed``, not ``holds_up``."""
    import numpy as np

    rng = np.random.default_rng(0)
    stocks = [f"S{i % 40}" for i in range(400)]
    strong = rng.normal(0.02, 0.05, 400)
    weak = rng.normal(0.001, 0.05, 400)
    assert study.oos_p_value(strong, stocks) < 0.05
    assert study.oos_p_value(weak - weak.mean() + 0.001, stocks) > 0.05
    assert study.oos_p_value(-strong, stocks) > 0.95  # one-sided: losses never confirm
    assert np.isnan(study.oos_p_value(strong, ["one"] * 400))  # a single stock proves nothing


def test_deflated_sharpe_is_deflated_with_one_signal() -> None:
    """One signal still has several horizons to pick from, so the hurdle is above zero."""
    import polars as pl

    from tests.study_fixtures import synthetic

    ev, rt, bench, cal = synthetic(seed=1, effect=0.0, n_events=600)
    ev = ev.filter(pl.col("signal_type") == "inside_info")
    inputs = study.cut_inputs(ev, rt, bench, study.as_of_for(cal[-1]))
    ars = study.dedupe_events(study.compute_event_ars(inputs))
    srs = study.trial_sharpes({"k": ars})
    assert len(srs) == len(study.HORIZONS)
    df, _ = study.compute_signal_stats(inputs)
    assert set(df["verdict"]) <= {"noise", "too_few", "untested", "unconfirmed", "eaten_by_costs"}


def test_path_band_widens_when_one_stock_repeats() -> None:
    """Resampling stocks: the same paths spread over fewer stocks give a wider band."""
    import numpy as np

    rng = np.random.default_rng(2)
    base = np.cumsum(rng.normal(0, 0.02, (8, 26)), axis=1)
    mat = np.repeat(base, 10, axis=0)  # 8 distinct paths, each filed 10 times
    by_event = [f"E{i}" for i in range(80)]
    by_stock = [f"S{i // 10}" for i in range(80)]
    lo_e, hi_e = study.path_band_clustered(mat, by_event, "k")
    lo_s, hi_s = study.path_band_clustered(mat, by_stock, "k")
    assert (hi_s - lo_s)[-1] > 2 * (hi_e - lo_e)[-1]


def test_newconnect_trades_pay_the_higher_cost() -> None:
    """The same gross returns earn less out of sample when the stocks are on NewConnect."""
    import polars as pl

    ev, rt, bench, cal = synthetic(seed=1, effect=0.10, n_events=600)
    as_of = study.as_of_for(cal[-1])
    net = {}
    for market in ("GPW", "NC"):
        df, _ = study.compute_signal_stats(
            study.cut_inputs(ev.with_columns(pl.lit(market).alias("market")), rt, bench, as_of)
        )
        row = df.filter(pl.col("signal_key") == "inside_info:large_contract").row(0, named=True)
        net[market] = row["oos_mean_net"]
    gap = study.ROUND_TRIP_COST_NC - study.ROUND_TRIP_COST
    assert net["GPW"] - net["NC"] == pytest.approx(gap, abs=1e-9)


def test_no_window_reaches_across_a_hole_in_the_calendar() -> None:
    """Sessions on either side of a data hole (missing months) are not neighbours."""
    from datetime import timedelta

    import polars as pl

    ev, rt, bench, cal = synthetic(seed=3, effect=0.0, n_events=300)
    cut = cal[1000]  # everything from here on happens ten years later

    def shift(col: str) -> pl.Expr:
        return (
            pl.when(pl.col(col).dt.date() >= cut if col == "known_at" else pl.col(col) >= cut)
            .then(pl.col(col) + timedelta(days=3650))
            .otherwise(pl.col(col))
        )

    ev2 = ev.with_columns(shift("event_date"), shift("known_at"))
    rt2 = rt.with_columns(shift("trade_date"), shift("known_at"))
    bench2 = bench.with_columns(shift("trade_date"), shift("known_at"))
    inputs = study.cut_inputs(ev2, rt2, bench2, datetime(2060, 1, 1, tzinfo=UTC))
    cal2 = inputs.benchmark["trade_date"].to_list()
    seg = study.calendar_segments(cal2)
    assert seg[999] == 0 and seg[1000] == 1 and seg[-1] == 1

    ars = study.compute_event_ars(inputs)
    assert ars
    for e in ars:
        offs = np.array(list(study._offset_range()))
        pos = e.day0_idx + offs
        inside = (pos >= 0) & (pos < len(cal2))
        other = np.zeros(len(offs), dtype=bool)
        other[inside] = seg[pos[inside]] != seg[e.day0_idx]
        assert np.all(np.isnan(e.ar[other]))  # nothing is measured on the far side
        if e.day0_idx < 1000 <= e.day0_idx + e.entry + 60:
            assert math.isnan(study.trade_car(e, 60))  # the trade would span the hole
    # events just after the hole have no estimation history on their side: skipped
    assert all(not 1000 <= e.day0_idx < 1000 + study.MIN_HISTORY for e in ars)


def test_untradable_stocks_are_left_out() -> None:
    """Grosz-priced or barely traded stocks have no events; without price data nothing changes."""
    import polars as pl

    ev, rt, bench, _ = synthetic(n_events=240)
    isins = sorted(set(rt["isin"]))
    penny, thin = isins[0], isins[1]
    rt = rt.with_columns(
        pl.lit(1.0).alias("volume"),
        pl.when(pl.col("isin") == penny).then(0.02).otherwise(12.0).alias("close"),
        pl.when(pl.col("isin") == thin).then(800.0).otherwise(250_000.0).alias("value_pln"),
    )
    with_data = study.compute_event_ars(study.cut_inputs(ev, rt, bench, END))
    plain = study.compute_event_ars(study.cut_inputs(ev, rt.drop("close", "value_pln"), bench, END))
    assert {penny, thin} <= {e.isin for e in plain}
    assert not {penny, thin} & {e.isin for e in with_data}
    assert len(with_data) == len([e for e in plain if e.isin not in (penny, thin)])


def test_buyable_thresholds() -> None:
    ok_price, ok_turn = np.full(50, 5.0), np.full(50, 50_000.0)
    assert study._buyable(ok_price, ok_turn)
    assert not study._buyable(np.full(50, study.MIN_PRICE - 0.01), ok_turn)
    assert not study._buyable(ok_price, np.full(50, study.MIN_TURNOVER - 1))
    # mostly idle: the median session has no turnover at all
    assert not study._buyable(ok_price, np.r_[np.zeros(30), np.full(20, 1e6)])
    assert not study._buyable(np.full(50, np.nan), ok_turn)
