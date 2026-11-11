"""Stock of the week: rank liquid WIG stocks by expected 20-session excess return over WIG.

Method (details in ``docs/METHODOLOGY.md`` § Stock of the week):

* **Universe:** main-market shares with a year of continuous history and a median daily
  turnover of at least 100,000 zł (``pick_features.eligible``).
* **Model:** ridge regression of the cross-sectional rank of the next 20 sessions' excess
  return on the ranks of the features (``pick_features.FEATURES``). A stock's score is the
  sum of feature × weight, so every score can be explained term by term. A second, pooled
  least-squares fit turns the score into an expected excess return in percent.
* **Walk-forward:** a model for year Y is fitted only on weeks whose 20-session target had
  ended before Y began (the purge), with its penalty chosen on the last year of that
  history. Each week of Y it picks the top score; the pick is kept unless the new leader's
  expected excess beats it by more than a round-trip cost (``study.ROUND_TRIP_COST``).
  Every switch, the first purchase included, pays that cost.
* **Baselines:** the same rule on 12-1 momentum alone, and an equal-weighted basket of the
  whole universe. The verdict is about the model, but the page shows all three.
* **Live record:** on the first session of each week the nightly run appends that week's
  pick to ``state/picks.parquet``. Rows are never rewritten; this is the record that counts.

Deterministic: no wall-clock values, seeded bootstrap.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy import stats as sps

from undercurrent import lake, study
from undercurrent.pick_features import (
    FEATURES,
    HORIZON,
    Panel,
    PickInputs,
    build_panel,
    eligible,
    forward_excess,
    hold_excess,
    rank_normal,
    raw_features,
    rebalance_positions,
)

log = logging.getLogger(__name__)
FloatArray = npt.NDArray[np.float64]

PENALTIES: tuple[float, ...] = (1.0, 10.0, 100.0, 1000.0)
MIN_UNIVERSE = 15  # stocks needed on a date for a cross-section
MIN_TRAIN_WEEKS = 40
VALIDATION_WEEKS = 52
MIN_OOS_WEEKS = 26
SIGNIFICANCE = 0.05
COST = study.ROUND_TRIP_COST
WINSOR = (0.01, 0.99)
LIVE_STATE = "picks"
TOP_N = 10  # size of the diagnostic top-scores basket


# --------------------------------------------------------------------------- dataset


@dataclass
class Week:
    """One rebalance session's cross-section."""

    t: int
    day: date
    stocks: npt.NDArray[np.int64]  # panel rows of the eligible stocks
    z: FloatArray  # stocks × FEATURES, rank-normal
    raw: FloatArray  # stocks × FEATURES, raw values
    y: FloatArray  # winsorized 20-session excess return (NaN if not known yet)
    y_rank: FloatArray  # its rank-normal score (0 where unknown)


def build_weeks(panel: Panel) -> list[Week]:
    """Cross-sections on every rebalance session with a large enough universe."""
    out = []
    for t in rebalance_positions(panel):
        ok = eligible(panel, t)
        if ok.sum() < MIN_UNIVERSE:
            continue
        rows = np.nonzero(ok)[0]
        raw = raw_features(panel, t)[rows]
        y = forward_excess(panel, t)[rows]
        known = np.isfinite(y)
        if known.sum() >= MIN_UNIVERSE:
            lo, hi = np.quantile(y[known], WINSOR)
            y = np.where(known, np.clip(y, lo, hi), np.nan)
        y_rank = np.zeros(len(rows))
        if known.sum() >= 2:
            y_rank[known] = rank_normal(y[known][:, None])[:, 0]
        out.append(Week(t, panel.cal[t], rows, rank_normal(raw), raw, y, y_rank))
    return out


# --------------------------------------------------------------------------- model


@dataclass
class Model:
    """Ridge weights on rank-normal features, and the score → expected excess map."""

    weights: FloatArray
    penalty: float
    intercept: float  # expected excess = intercept + slope * score
    slope: float
    train_weeks: int
    train_start: date | None = None
    train_end: date | None = None

    def score(self, z: FloatArray) -> FloatArray:
        """Score of each row of rank-normal features."""
        return np.asarray(z @ self.weights, dtype=np.float64)

    def expected(self, z: FloatArray) -> FloatArray:
        """Expected 20-session excess return over WIG, from the score."""
        return np.asarray(self.intercept + self.slope * self.score(z), dtype=np.float64)


def _stack(weeks: list[Week]) -> tuple[FloatArray, FloatArray, FloatArray]:
    zs, yr, yw = [], [], []
    for w in weeks:
        k = np.isfinite(w.y)
        zs.append(w.z[k])
        yr.append(w.y_rank[k])
        yw.append(w.y[k])
    if not zs:
        empty = np.zeros((0, len(FEATURES)))
        return empty, np.zeros(0), np.zeros(0)
    return np.vstack(zs), np.concatenate(yr), np.concatenate(yw)


def fit_ridge(weeks: list[Week], penalty: float) -> Model:
    """Ridge on the pooled cross-sections; then a least-squares map from score to excess."""
    x, y_rank, y = _stack(weeks)
    k = x.shape[1]
    n = max(len(x), 1)
    w = np.linalg.solve(x.T @ x + penalty * n / 100 * np.eye(k), x.T @ y_rank)
    s = x @ w
    if len(s) > 2 and np.var(s) > 0:
        slope = float(np.cov(s, y, ddof=0)[0, 1] / np.var(s))
        intercept = float(np.mean(y) - slope * np.mean(s))
    else:
        slope, intercept = 0.0, 0.0
    days = [wk.day for wk in weeks]
    return Model(
        weights=w,
        penalty=penalty,
        intercept=intercept,
        slope=slope,
        train_weeks=len(weeks),
        train_start=min(days) if days else None,
        train_end=max(days) if days else None,
    )


def rank_ic(score: FloatArray, y: FloatArray) -> float:
    """Spearman correlation of scores and realized returns (NaN with too few)."""
    k = np.isfinite(y) & np.isfinite(score)
    if k.sum() < 5 or np.ptp(score[k]) == 0:
        return math.nan
    return float(sps.spearmanr(score[k], y[k]).statistic)


def _known_before(weeks: list[Week], panel: Panel, t_cut: int) -> list[Week]:
    """Weeks whose 20-session target ended before calendar position ``t_cut`` (the purge)."""
    return [w for w in weeks if w.t + HORIZON < t_cut and panel.seg[w.t] == panel.seg[w.t]]


def choose_model(weeks: list[Week], panel: Panel) -> Model | None:
    """Pick the penalty on the last year of ``weeks`` (purged), then refit on all of them."""
    if len(weeks) < MIN_TRAIN_WEEKS:
        return None
    val = weeks[-VALIDATION_WEEKS:]
    fit_on = _known_before(weeks[:-VALIDATION_WEEKS], panel, val[0].t)
    best = PENALTIES[len(PENALTIES) // 2]
    if len(fit_on) >= MIN_TRAIN_WEEKS // 2:
        scores = []
        for p in PENALTIES:
            m = fit_ridge(fit_on, p)
            ics = [rank_ic(m.score(w.z), w.y) for w in val]
            scores.append(np.nanmean(ics) if np.isfinite(ics).any() else -np.inf)
        best = PENALTIES[int(np.argmax(scores))]
    return fit_ridge(weeks, best)


# --------------------------------------------------------------------------- picking


@dataclass
class Holding:
    """The pick being held, with its expected excess when chosen or last re-scored."""

    stock: int  # panel row
    expected: float


def pick_with_hysteresis(
    held: Holding | None, stocks: npt.NDArray[np.int64], expected: FloatArray
) -> tuple[Holding, bool]:
    """Keep the current pick unless the leader beats it by more than a round trip."""
    lead = int(np.argmax(expected))
    leader = Holding(int(stocks[lead]), float(expected[lead]))
    if held is not None:
        where = np.nonzero(stocks == held.stock)[0]
        if len(where):
            current = float(expected[where[0]])
            if leader.expected - current <= COST:
                return Holding(held.stock, current), False
    return leader, held is None or leader.stock != held.stock


@dataclass
class Backtest:
    """Weekly out-of-sample results of the walk-forward."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    feature_ic: dict[str, list[float]] = field(default_factory=dict)


def walk_forward(weeks: list[Week], panel: Panel, penalty: float | None = None) -> Backtest:
    """Yearly re-fits on purged history; weekly picks with hysteresis, net of costs.

    ``penalty`` fixes the ridge penalty (used to count the trials for the deflated Sharpe);
    by default it is chosen on each training history.
    """
    bt = Backtest(feature_ic={f: [] for f in FEATURES})
    years = sorted({w.day.year for w in weeks})
    held: Holding | None = None
    held_mom: Holding | None = None
    mom_col = FEATURES.index("mom_12_1")
    for y in years:
        test = [w for w in weeks if w.day.year == y]
        start = test[0].t
        train = _known_before([w for w in weeks if w.day.year < y], panel, start)
        model = (
            fit_ridge(train, penalty)
            if penalty is not None and len(train) >= MIN_TRAIN_WEEKS
            else choose_model(train, panel)
            if penalty is None
            else None
        )
        if model is None:
            continue
        mom = fit_ridge(train, 1.0)
        mom.weights = np.zeros(len(FEATURES))
        mom.weights[mom_col] = 1.0
        xs, _, ys = _stack(train)
        s = xs[:, mom_col]
        if len(s) > 2 and np.var(s) > 0:
            mom.slope = float(np.cov(s, ys, ddof=0)[0, 1] / np.var(s))
            mom.intercept = float(np.mean(ys) - mom.slope * np.mean(s))
        for k, w in enumerate(test):
            nxt = test[k + 1].t if k + 1 < len(test) else _next_rebalance(weeks, w.t, panel)
            exp_m = model.expected(w.z)
            held, switched = pick_with_hysteresis(held, w.stocks, exp_m)
            held_mom, switched_mom = pick_with_hysteresis(held_mom, w.stocks, mom.expected(w.z))
            gross = hold_excess(panel, held.stock, w.t, nxt)
            gross_mom = hold_excess(panel, held_mom.stock, w.t, nxt)
            basket = [hold_excess(panel, int(i), w.t, nxt) for i in w.stocks]
            bt.rows.append(
                {
                    "day": w.day,
                    "isin": panel.isins[held.stock],
                    "ticker": panel.tickers[held.stock],
                    "expected": held.expected,
                    "switched": switched,
                    "gross": gross,
                    "net": gross - (COST if switched else 0.0),
                    "momentum_net": gross_mom - (COST if switched_mom else 0.0),
                    "basket": float(np.nanmean(basket)) if basket else math.nan,
                    # diagnostic: the model's ten best scores, equal weights, before costs
                    "top10": float(
                        np.nanmean([basket[i] for i in np.argsort(-exp_m, kind="stable")[:TOP_N]])
                    ),
                    "ic": rank_ic(model.score(w.z), w.y),
                    "universe": len(w.stocks),
                    "model_end": model.train_end,
                    "penalty": model.penalty,
                }
            )
            for j, f in enumerate(FEATURES):
                bt.feature_ic[f].append(rank_ic(w.z[:, j], w.y))
    return bt


def _next_rebalance(weeks: list[Week], t: int, panel: Panel) -> int:
    later = [w.t for w in weeks if w.t > t]
    if later:
        return later[0]
    return len(panel.cal) - 1


# --------------------------------------------------------------------------- verdict


def _block_bootstrap_t(x: FloatArray, block: int = 4, draws: int = 2000) -> float:
    """t-statistic of the mean of a weekly series with overlapping targets (moving blocks)."""
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2 * block:
        return math.nan
    rng = np.random.default_rng(study._seed("pick", "ic"))
    starts = rng.integers(0, n - block + 1, size=(draws, math.ceil(n / block)))
    idx = (starts[:, :, None] + np.arange(block)).reshape(draws, -1)[:, :n]
    sd = float(x[idx].mean(axis=1).std(ddof=1))
    return float(x.mean() / sd) if sd > 0 else math.nan


def max_drawdown(r: FloatArray) -> float:
    """Largest fall of the compounded curve, as a positive fraction."""
    r = r[np.isfinite(r)]
    if not len(r):
        return math.nan
    curve = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], curve]))[1:]
    return float((1 - curve / peak).max())


def summarise(bt: Backtest, trial_nets: list[FloatArray]) -> dict[str, Any]:
    """Headline numbers and the verdict for the page."""
    if not bt.rows:
        return {"verdict": "too_short", "oos_weeks": 0}
    df = pl.DataFrame(bt.rows)
    net = df["net"].to_numpy().astype(np.float64)
    ok = np.isfinite(net)
    weeks = int(ok.sum())
    months = [f"{d:%Y-%m}" for d in df["day"].to_list()]
    # One-sided p that the mean weekly net excess is above zero, clustered by calendar month
    # (a pick is often held for months, so its weeks are not independent; by stock there
    # would be only a handful of clusters).
    t_stat, _, g = study.cluster_t(net[ok], [m for m, k in zip(months, ok, strict=True) if k])
    p = float(sps.t.sf(t_stat, df=g - 1)) if math.isfinite(t_stat) else math.nan
    mean = float(np.mean(net[ok])) if weeks else math.nan
    if weeks < MIN_OOS_WEEKS:
        verdict = "too_short"
    elif mean > 0 and math.isfinite(p) and p < SIGNIFICANCE:
        verdict = "beats_wig"
    elif mean > 0:
        verdict = "unconfirmed"
    else:
        verdict = "no_edge"
    srs = [
        float(np.mean(r) / np.std(r, ddof=1)) for r in trial_nets if len(r) > 2 and np.std(r) > 0
    ]
    dsr = (
        study.deflated_sharpe(net[ok], len(srs), float(np.var(srs, ddof=1)))
        if len(srs) > 1 and weeks > 3
        else math.nan
    )
    ic = df["ic"].to_numpy().astype(np.float64)
    mom = df["momentum_net"].to_numpy().astype(np.float64)
    basket = df["basket"].to_numpy().astype(np.float64)
    top10 = df["top10"].to_numpy().astype(np.float64)
    return {
        "verdict": verdict,
        "oos_weeks": weeks,
        "oos_start": df["day"].min(),
        "oos_end": df["day"].max(),
        "mean_net": mean,
        "p_value": None if not math.isfinite(p) else p,
        "hit_rate": float(np.mean(net[ok] > 0)) if weeks else None,
        "max_drawdown": max_drawdown(net),
        "total_net": float(np.prod(1 + net[ok]) - 1) if weeks else None,
        "switches": int(df["switched"].sum()),
        "ic_mean": float(np.nanmean(ic)) if np.isfinite(ic).any() else None,
        "ic_t": _block_bootstrap_t(ic),
        "deflated_sharpe": None if not math.isfinite(dsr) else dsr,
        "momentum_mean_net": float(np.nanmean(mom)) if np.isfinite(mom).any() else None,
        "momentum_total_net": float(np.prod(1 + mom[np.isfinite(mom)]) - 1),
        "basket_mean": float(np.nanmean(basket)) if np.isfinite(basket).any() else None,
        "basket_total": float(np.prod(1 + basket[np.isfinite(basket)]) - 1),
        "top10_mean": float(np.nanmean(top10)) if np.isfinite(top10).any() else None,
        "top10_total": float(np.prod(1 + top10[np.isfinite(top10)]) - 1),
        "trials": len(trial_nets),
    }


# --------------------------------------------------------------------------- live record

LIVE_SCHEMA: dict[str, Any] = {
    "week": pl.Date,
    "isin": pl.String,
    "ticker": pl.String,
    "expected": pl.Float64,
    "switched": pl.Boolean,
    "as_of": pl.Datetime("us", "UTC"),
}


def is_rebalance_day(panel: Panel, day: date) -> bool:
    """Whether ``day`` is the first session of its ISO week in the panel's calendar."""
    if day not in panel.cal:
        return False
    t = panel.cal.index(day)
    return t in rebalance_positions(panel)


def record_live(
    data_dir: Path, panel: Panel, model: Model, day: date, as_of: datetime
) -> pl.DataFrame | None:
    """Append this week's pick to ``state/picks`` on its first session; never rewrite."""
    old = lake.read_state(data_dir, LIVE_STATE)
    if old is not None and old.filter(pl.col("week") == day).height:
        return old
    if not is_rebalance_day(panel, day):
        return old
    t = panel.cal.index(day)
    ok = eligible(panel, t)
    if ok.sum() < MIN_UNIVERSE:
        return old
    rows = np.nonzero(ok)[0]
    exp = model.expected(rank_normal(raw_features(panel, t)[rows]))
    held = None
    if old is not None and old.height:
        last = old.sort("week").row(-1, named=True)
        if last["isin"] in panel.isins:
            held = Holding(panel.isins.index(last["isin"]), float(last["expected"]))
    choice, switched = pick_with_hysteresis(held, rows, exp)
    new = pl.DataFrame(
        [
            {
                "week": day,
                "isin": panel.isins[choice.stock],
                "ticker": panel.tickers[choice.stock],
                "expected": choice.expected,
                "switched": switched,
                "as_of": as_of,
            }
        ],
        schema=LIVE_SCHEMA,
    )
    df = new if old is None else pl.concat([old, new], how="diagonal_relaxed").sort("week")
    lake.write_state(data_dir, LIVE_STATE, df)
    log.info("pick of the week %s: %s", day, panel.tickers[choice.stock])
    return df


def live_results(panel: Panel, live: pl.DataFrame | None) -> pl.DataFrame:
    """Each live pick's result against WIG while it was held, net of its switch cost."""
    if live is None or not live.height:
        return pl.DataFrame(schema={**LIVE_SCHEMA, "gross": pl.Float64, "net": pl.Float64})
    rows = []
    recs = live.sort("week").to_dicts()
    last_t = len(panel.cal) - 1
    for k, r in enumerate(recs):
        t = panel.cal.index(r["week"]) if r["week"] in panel.cal else None
        t2 = (
            panel.cal.index(recs[k + 1]["week"])
            if k + 1 < len(recs) and recs[k + 1]["week"] in panel.cal
            else last_t
        )
        g = math.nan
        if t is not None and r["isin"] in panel.isins:
            g = hold_excess(panel, panel.isins.index(r["isin"]), t, t2)
        rows.append({**r, "gross": g, "net": g - (COST if r["switched"] else 0.0)})
    return pl.DataFrame(rows, schema={**LIVE_SCHEMA, "gross": pl.Float64, "net": pl.Float64})


# --------------------------------------------------------------------------- run


def compute(inputs: PickInputs) -> dict[str, Any]:
    """Everything the page needs, from the inputs alone (no lake writes)."""
    # Empty slices (a stock with no trades in a window, the last week's open holding) are
    # expected and come out as NaN; numpy's warnings about them are noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _compute(inputs)


def _compute(inputs: PickInputs) -> dict[str, Any]:
    panel = build_panel(inputs)
    weeks = build_weeks(panel)
    bt = walk_forward(weeks, panel)
    trials = [
        np.array([r["net"] for r in walk_forward(weeks, panel, p).rows], dtype=np.float64)
        for p in PENALTIES
    ]
    trials.append(np.array([r["momentum_net"] for r in bt.rows], dtype=np.float64))
    summary = summarise(bt, trials)
    t_now = len(panel.cal) - 1
    final = choose_model(_known_before(weeks, panel, t_now + 1), panel)
    scores = pl.DataFrame()
    if final is not None and t_now >= 0:
        ok = eligible(panel, t_now)
        rows = np.nonzero(ok)[0]
        if len(rows) >= MIN_UNIVERSE:
            raw = raw_features(panel, t_now)[rows]
            z = rank_normal(raw)
            contrib = z * final.weights
            exp = final.expected(z)
            order = np.argsort(-exp, kind="stable")
            scores = pl.DataFrame(
                {
                    "isin": [panel.isins[rows[i]] for i in order],
                    "ticker": [panel.tickers[rows[i]] for i in order],
                    "rank": list(range(1, len(order) + 1)),
                    "score": final.score(z)[order],
                    "expected": exp[order],
                    **{f"f_{f}": raw[order, j] for j, f in enumerate(FEATURES)},
                    **{f"c_{f}": contrib[order, j] for j, f in enumerate(FEATURES)},
                }
            )
    model_df = pl.DataFrame(
        {
            "feature": list(FEATURES),
            "weight": (final.weights if final is not None else np.full(len(FEATURES), np.nan)),
            "oos_ic": [
                float(np.nanmean(v)) if np.isfinite(v).any() else math.nan
                for v in (np.array(bt.feature_ic[f], dtype=np.float64) for f in FEATURES)
            ],
        }
    )
    summary |= {
        "as_of": inputs.as_of,
        "scored_on": panel.cal[t_now] if t_now >= 0 else None,
        "universe": scores.height,
        "penalty": final.penalty if final else None,
        "train_start": final.train_start if final else None,
        "train_end": final.train_end if final else None,
        "train_weeks": final.train_weeks if final else 0,
        "intercept": final.intercept if final else None,
        "slope": final.slope if final else None,
    }
    backtest = pl.DataFrame(bt.rows) if bt.rows else pl.DataFrame()
    return {
        "panel": panel,
        "model": final,
        "summary": summary,
        "scores": scores,
        "model_df": model_df,
        "backtest": backtest,
    }


def run_pick(duckdb_file: Path, work: Path, data_dir: Path, day: date) -> Path:
    """Compute the pick, write ``gold/pick_*.parquet`` and append the live record."""
    from undercurrent.pick_features import load_inputs

    as_of = study.as_of_for(day)
    out = compute(load_inputs(duckdb_file, as_of))
    gold = work / "gold"
    live = None
    if out["model"] is not None:
        live = record_live(data_dir, out["panel"], out["model"], day, as_of)
    else:
        live = lake.read_state(data_dir, LIVE_STATE)
    summary = pl.DataFrame([out["summary"]])
    lake.write_frame(summary, gold / "pick_summary.parquet")
    lake.write_frame(out["scores"], gold / "pick_scores.parquet")
    lake.write_frame(out["model_df"], gold / "pick_model.parquet")
    lake.write_frame(out["backtest"], gold / "pick_backtest.parquet")
    lake.write_frame(live_results(out["panel"], live), gold / "pick_live.parquet")
    log.info(
        "pick: verdict=%s weeks=%s universe=%s",
        out["summary"]["verdict"],
        out["summary"].get("oos_weeks"),
        out["summary"]["universe"],
    )
    return gold / "pick_summary.parquet"
