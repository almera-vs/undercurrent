"""Chart setups: where a state begins, the refractory, holes, and the study's timing."""

import itertools
from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl

from undercurrent import setups, study


def _seg(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int64)


def test_rsi_state_begins_once_then_waits_for_the_refractory() -> None:
    # 20 sessions of steady rises, then a fall of 25 sessions, a bounce, and another fall
    c = np.concatenate(
        [
            np.linspace(10, 20, 20),
            np.linspace(20, 8, 25),
            np.linspace(8, 12, 6),
            np.linspace(12, 5, 10),
        ]
    )
    hit = setups.triggers(c, c, c, np.ones(len(c)), _seg(len(c)))
    below = np.nonzero(hit["rsi_below_30"])[0]
    assert len(below) >= 2  # it entered the state twice
    kept = setups._refractory(hit["rsi_below_30"])
    assert all(b - a >= setups.REFRACTORY for a, b in itertools.pairwise(kept))
    assert kept[0] == below[0]


def test_a_new_high_needs_a_year_and_counts_every_refractory() -> None:
    c = np.linspace(10, 40, 300)  # a new high every session
    hit = setups.triggers(c, c, c, np.ones(300), _seg(300))["new_high"]
    assert not hit[:250].any()  # no 52-week high before 250 sessions of history
    kept = setups._refractory(hit)
    assert kept[0] == 250 and kept[1] == 270


def test_no_event_across_a_hole() -> None:
    c = np.concatenate([np.full(220, 10.0), np.linspace(10, 30, 60)])
    seg = np.array([0] * 220 + [1] * 60)
    hit = setups.triggers(c, c, c, np.ones(len(c)), seg)
    # after the hole the averages need 200 sessions again: no golden cross from old data
    assert not hit["golden_cross"][220:].any()
    assert not hit["above_both_averages"][220:].any()


def test_events_are_known_with_their_prices_and_trade_the_next_close() -> None:
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(450)]
    days = [d for d in days if d.weekday() < 5][:300]
    c = np.linspace(10, 40, 300)
    known = [datetime(d.year, d.month, d.day, 16, 30, tzinfo=UTC) for d in days]  # 17:30 Warsaw
    prices = pl.DataFrame(
        {
            "isin": ["PLX"] * 300,
            "trade_date": days,
            "market": ["GPW"] * 300,
            "close": c,
            "adj_close": c,
            "high": c,
            "low": c,
            "value_pln": np.full(300, 1e5),
            "known_at": known,
        }
    )
    ev = setups.setup_events(prices, {d: 0 for d in days})
    hi = ev.filter(pl.col("subtype") == "new_high").sort("event_date")
    first = hi.row(0, named=True)
    assert first["event_date"] == days[250] and first["known_at"] == known[250]
    # the study enters at the first close after the digest that carries it: the next session
    assert study.entry_offset(first["known_at"], first["event_date"], days) == 1


def test_new_setups_match_their_definitions() -> None:
    rng = np.random.default_rng(4)
    n = 400
    c = 50 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    tv = rng.uniform(5e4, 2e5, n)
    tv[-1] = 1e6  # a burst of turnover on the last session
    seg = _seg(n)
    st, defined = setups.states(c * 1.01, c * 0.99, c, tv, seg)
    from undercurrent import ta

    rsi = ta.rsi(c, 14, seg)
    hi52, _ = ta.high_low(c * 1.01, c * 0.99, seg)
    rvol = ta.relative(tv, 20, seg)
    expect = (c[-1] >= 0.97 * hi52[-1]) and rvol[-1] > 1.5 and rsi[-1] > 55
    assert bool(st["breakout"][-1]) == bool(expect)
    s200 = ta.sma(c, 200, seg)
    pull = (c > s200) & (rsi >= 30) & (rsi <= 50) & np.isfinite(s200)
    assert np.array_equal(st["pullback"], pull)
    assert not defined["trend_health"][:209].any()  # SMA200 ten sessions ago needs 209
    assert set(st) == set(setups.SETUPS)
    assert set(setups.PRESET_SETUP.values()) <= set(setups.SETUPS) | set(setups.VALUATION_SETUPS)
