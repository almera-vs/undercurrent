"""Synthetic market for study tests: seeded, with a planted effect for one signal."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

WARSAW = ZoneInfo("Europe/Warsaw")


def sessions(n: int, start: date = date(2018, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def known(d: date) -> datetime:
    return datetime.combine(d, time(17, 35), tzinfo=WARSAW).astimezone(UTC)


def synthetic(
    n_sessions: int = 1600,
    n_isins: int = 40,
    n_events: int = 240,
    effect: float = 0.03,
    seed: int = 7,
    effect_day: int = 2,
):
    """Events are published at 09:00 on day 0, so the digest is that evening and the first
    tradable close is day +1. The planted effect sits on ``effect_day`` (default +2, the
    first session after entry)."""
    rng = np.random.default_rng(seed)
    cal = sessions(n_sessions)
    mkt = rng.normal(0.0003, 0.01, n_sessions)
    isins = [f"PLSYN{i:06d}" for i in range(n_isins)]
    rets = {}
    for isin in isins:
        beta = rng.uniform(0.5, 1.5)
        rets[isin] = beta * mkt + rng.normal(0, 0.02, n_sessions)
    events = []
    for k in range(n_events):
        key_type = "inside_info" if k % 2 == 0 else "holder_threshold"
        sub = "large_contract" if key_type == "inside_info" else None
        isin = isins[int(rng.integers(0, n_isins))]
        e = int(rng.integers(300, n_sessions - 80))
        if key_type == "inside_info":
            rets[isin][e + effect_day] += effect  # planted abnormal return
        pub = datetime.combine(cal[e], time(9, 0), tzinfo=WARSAW).astimezone(UTC)
        events.append(
            {
                "report_id": f"gpw:{k}",
                "signal_type": key_type,
                "subtype": sub,
                "insider_side": None,
                "isin": isin,
                "known_at": pub,
                "event_date": cal[e],
            }
        )
    ret_rows = [
        {"isin": isin, "trade_date": d, "adj_ret": float(r[i]), "known_at": known(d)}
        for isin, r in rets.items()
        for i, d in enumerate(cal)
    ]
    bench = pl.DataFrame(
        {"trade_date": cal, "wig_ret": mkt.tolist(), "known_at": [known(d) for d in cal]}
    )
    ev = pl.DataFrame(events).with_columns(pl.col("known_at").cast(pl.Datetime("us", "UTC")))
    rt = pl.DataFrame(ret_rows).with_columns(pl.col("known_at").cast(pl.Datetime("us", "UTC")))
    bench = bench.with_columns(pl.col("known_at").cast(pl.Datetime("us", "UTC")))
    return ev, rt, bench, cal
