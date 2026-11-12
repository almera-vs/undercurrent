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
* **Reads the market state** (``market_state.py``) when the work dir has it. Costs are then
  each stock's own round trip, in the backtest and in the live record alike, and three
  nested variants are walked forward: the original features (``base``), plus the state's
  features (``state_features``), plus a score → expected-return map in units of each
  stock's forecast σ (``state``). The variant with the best out-of-sample rank IC becomes
  the model (ties go to the simpler one); every variant × penalty counts as a trial in the
  deflated Sharpe ratio. Without the state all three are the same model.
* **Band** (conformal, per regime): every training year after the first is scored by a
  model fitted only on the weeks before it (purged), and the errors, divided by each stock's
  forecast σ, are kept with the regime of their week (stress when the market state's
  stress probability is at least ½). The 5th and 95th percentiles of each regime's errors
  give that regime's band; a regime with fewer than ``MIN_REGIME_WEEKS`` weeks of errors
  uses the pooled one. A stock's band is its expected excess plus the percentiles times its
  σ. The backtest records how often the outcome fell inside (90 % if the band is honest)
  and which band was used; the page's bad case is the band's lower edge.
* **Coverage that corrects itself** (adaptive conformal inference, Gibbs and Candès 2021):
  the band's miss rate α starts at the target 10 % and, after every week whose outcome is
  known (20 sessions later), moves by ``ACI_GAMMA`` × (10 % − that week's miss rate). Weeks
  that missed too often widen later bands; weeks that missed too rarely narrow them. The
  history is the walk-forward's own weeks, and from the first live week on, the forecast
  ledger's.
* **Basket** (``basket.py``, the decision layer): the same weekly forecasts also feed a
  basket of up to ten names, weighted by hierarchical risk parity and charged each stock's
  own cost. **The basket is the decision**: the stock of the week is its largest position
  (the top score, flagged, in a week when nothing beats its cost), the headline verdict is
  the basket's, and its weekly record, ``state/basket.parquet``, is the live record. The
  single pick is still walked forward, as a comparison.
* **Forecast ledger:** on the first session of each week every eligible stock's expected
  excess and band are appended to ``state/forecasts.parquet`` and never rewritten. Its
  realized coverage is the live check of the band, and its misses set α for later weeks.

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

from undercurrent import basket, lake, study
from undercurrent.pick_features import (
    ALL_FEATURES,
    FEATURES,
    HORIZON,
    NEWS_GROUPS,
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
ALPHA = 0.10  # the band's target miss rate: a 90 % band, 5 % below and 5 % above
ACI_GAMMA = 0.05  # how far one week's miss moves α
ALPHA_BOUNDS = (0.01, 0.5)
LEDGER_STATE = "forecasts"
BASKET_STATE = "basket"
MIN_BAND_ERRORS = 200  # stock-weeks a band needs to be calibrated on
MIN_REGIME_WEEKS = 8  # weeks of errors a regime needs for a band of its own
STRESS_AT = 0.5  # stress probability from which a week counts as stress


@dataclass(frozen=True)
class Variant:
    """Which features a model reads and how its score becomes an expected return."""

    name: str
    features: tuple[str, ...]
    scaled: bool  # expected excess = (a + b·score) × the stock's forecast 20-session σ


VARIANTS: tuple[Variant, ...] = (
    Variant("base", FEATURES, False),
    Variant("state_features", ALL_FEATURES, False),
    Variant("state", ALL_FEATURES, True),
)
BASE = VARIANTS[0]
NEWS_FEATURES = frozenset((*NEWS_GROUPS, "insider_net"))  # the features read from reports


# --------------------------------------------------------------------------- dataset


@dataclass
class Week:
    """One rebalance session's cross-section."""

    t: int
    day: date
    stocks: npt.NDArray[np.int64]  # panel rows of the eligible stocks
    z: FloatArray  # stocks × ALL_FEATURES, rank-normal
    raw: FloatArray  # stocks × ALL_FEATURES, raw values
    y: FloatArray  # winsorized 20-session excess return (NaN if not known yet)
    y_rank: FloatArray  # its rank-normal score (0 where unknown)
    y_raw: FloatArray  # the same before winsorizing: what bands are calibrated and scored on
    scale: FloatArray  # forecast 20-session σ (the date's median where missing; 1 without)
    cost: FloatArray  # round-trip cost of buying each stock (COST without a market state)
    regime: str | None = None  # "calm" / "stress" from the market state; None without


def regime_of(panel: Panel, t: int) -> str | None:
    """The market regime on session ``t``: ``calm``, ``stress`` or ``None`` (unknown)."""
    p = panel.regime[t]
    if not math.isfinite(p):
        return None
    return "stress" if p >= STRESS_AT else "calm"


def _scale_and_cost(
    panel: Panel, t: int, rows: npt.NDArray[np.int64]
) -> tuple[FloatArray, FloatArray]:
    sig = panel.sigma_20[rows, t]
    ok = np.isfinite(sig) & (sig > 0)
    scale = np.where(ok, sig, float(np.median(sig[ok])) if ok.any() else 1.0)
    c = panel.cost[rows, t]
    return scale, np.where(np.isfinite(c), c, COST)


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
        y_raw = y.copy()
        known = np.isfinite(y)
        if known.sum() >= MIN_UNIVERSE:
            lo, hi = np.quantile(y[known], WINSOR)
            y = np.where(known, np.clip(y, lo, hi), np.nan)
        y_rank = np.zeros(len(rows))
        if known.sum() >= 2:
            y_rank[known] = rank_normal(y[known][:, None])[:, 0]
        scale, cost = _scale_and_cost(panel, t, rows)
        out.append(
            Week(
                t,
                panel.cal[t],
                rows,
                rank_normal(raw),
                raw,
                y,
                y_rank,
                y_raw,
                scale,
                cost,
                regime_of(panel, t),
            )
        )
    return out


# --------------------------------------------------------------------------- model


@dataclass
class Model:
    """Ridge weights on rank-normal features, and the score → expected excess map."""

    weights: FloatArray  # one per feature of ``variant``
    penalty: float
    intercept: float  # expected excess = (intercept + slope * score) × (σ if scaled else 1)
    slope: float
    train_weeks: int
    train_start: date | None = None
    train_end: date | None = None
    variant: Variant = BASE
    # calibration errors (outcome − expected) / σ: "all" pooled, and per regime with enough
    errors: dict[str, FloatArray] = field(default_factory=dict)

    def band_for(
        self, regime: str | None, alpha: float = ALPHA
    ) -> tuple[str, tuple[float, float]] | None:
        """The band to use in ``regime`` at miss rate ``alpha``, and its name.

        The pooled band is the fallback for a regime without errors of its own.
        """
        name = regime if regime is not None and regime in self.errors else "all"
        if name not in self.errors:
            return None
        lo, hi = np.quantile(self.errors[name], (alpha / 2, 1 - alpha / 2))
        return name, (float(lo), float(hi))

    def interval(
        self, z: FloatArray, scale: FloatArray, regime: str | None = None, alpha: float = ALPHA
    ) -> tuple[FloatArray, FloatArray] | None:
        """Lower and upper edge of each stock's band (``None`` without a calibration)."""
        b = self.band_for(regime, alpha)
        if b is None:
            return None
        e = self.expected(z, scale)
        return e + b[1][0] * scale, e + b[1][1] * scale

    @property
    def features(self) -> tuple[str, ...]:
        """The features this model reads, in weight order."""
        return self.variant.features

    @property
    def cols(self) -> npt.NDArray[np.int64]:
        """Their columns in ``ALL_FEATURES``."""
        return np.array([ALL_FEATURES.index(f) for f in self.features], dtype=np.int64)

    def score(self, z: FloatArray) -> FloatArray:
        """Score of each row of rank-normal features (``ALL_FEATURES`` columns)."""
        return np.asarray(z[:, self.cols] @ self.weights, dtype=np.float64)

    def expected(self, z: FloatArray, scale: FloatArray | None = None) -> FloatArray:
        """Expected 20-session excess return over WIG, from the score."""
        e = self.intercept + self.slope * self.score(z)
        if self.variant.scaled and scale is not None:
            e = e * scale
        return np.asarray(e, dtype=np.float64)


def _stack(
    weeks: list[Week],
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    zs, yr, yw, sc = [], [], [], []
    for w in weeks:
        k = np.isfinite(w.y)
        zs.append(w.z[k])
        yr.append(w.y_rank[k])
        yw.append(w.y[k])
        sc.append(w.scale[k])
    if not zs:
        empty = np.zeros((0, len(ALL_FEATURES)))
        return empty, np.zeros(0), np.zeros(0), np.zeros(0)
    return np.vstack(zs), np.concatenate(yr), np.concatenate(yw), np.concatenate(sc)


def fit_ridge(weeks: list[Week], penalty: float, variant: Variant = BASE) -> Model:
    """Ridge on the pooled cross-sections; then a least-squares map from score to excess.

    For a scaled variant the map is fitted on excess ÷ σ, so a score means the same
    risk-adjusted result for a calm stock and a wild one.
    """
    x_all, y_rank, y, scale = _stack(weeks)
    cols = [ALL_FEATURES.index(f) for f in variant.features]
    x = x_all[:, cols]
    if variant.scaled:
        y = y / scale
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
        variant=variant,
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


def choose_model(weeks: list[Week], panel: Panel, variant: Variant = BASE) -> Model | None:
    """Pick the penalty on the last year of ``weeks`` (purged), then refit on all of them."""
    if len(weeks) < MIN_TRAIN_WEEKS:
        return None
    val = weeks[-VALIDATION_WEEKS:]
    fit_on = _known_before(weeks[:-VALIDATION_WEEKS], panel, val[0].t)
    best = PENALTIES[len(PENALTIES) // 2]
    if len(fit_on) >= MIN_TRAIN_WEEKS // 2:
        scores = []
        for p in PENALTIES:
            m = fit_ridge(fit_on, p, variant)
            ics = [rank_ic(m.score(w.z), w.y) for w in val]
            scores.append(np.nanmean(ics) if np.isfinite(ics).any() else -np.inf)
        best = PENALTIES[int(np.argmax(scores))]
    model = fit_ridge(weeks, best, variant)
    model.errors = calibrate_bands(weeks, panel, variant, best)
    return model


def band_errors(
    weeks: list[Week], panel: Panel, variant: Variant, penalty: float
) -> list[tuple[str | None, FloatArray]]:
    """Normalised errors of the training years after the first, with their week's regime.

    Each year is scored by a model fitted only on the (purged) weeks before it.
    """
    out: list[tuple[str | None, FloatArray]] = []
    years = sorted({w.day.year for w in weeks})
    for y in years[1:]:
        test = [w for w in weeks if w.day.year == y]
        fit_on = _known_before([w for w in weeks if w.day.year < y], panel, test[0].t)
        if len(fit_on) < MIN_TRAIN_WEEKS // 2:
            continue
        m = fit_ridge(fit_on, penalty, variant)
        for w in test:
            k = np.isfinite(w.y_raw)
            if k.any():
                out.append((w.regime, ((w.y_raw - m.expected(w.z, w.scale)) / w.scale)[k]))
    return out


def calibrate_bands(
    weeks: list[Week], panel: Panel, variant: Variant, penalty: float
) -> dict[str, FloatArray]:
    """The calibration errors, pooled (``all``) and per regime with enough weeks."""
    errs = band_errors(weeks, panel, variant, penalty)
    out: dict[str, FloatArray] = {}
    groups: dict[str, list[FloatArray]] = {"all": [e for _, e in errs]}
    for regime, e in errs:
        if regime is not None:
            groups.setdefault(regime, []).append(e)
    for name, parts in groups.items():
        e = np.concatenate(parts) if parts else np.zeros(0)
        if len(e) < MIN_BAND_ERRORS or (name != "all" and len(parts) < MIN_REGIME_WEEKS):
            continue
        out[name] = np.sort(e)
    return out


def coverage(model: Model, w: Week, alpha: float = ALPHA) -> float:
    """Share of the week's stocks whose outcome fell inside their band."""
    iv = model.interval(w.z, w.scale, w.regime, alpha)
    k = np.isfinite(w.y_raw)
    if iv is None or not k.any():
        return math.nan
    lo, hi = iv
    return float(np.mean((w.y_raw[k] >= lo[k]) & (w.y_raw[k] <= hi[k])))


def aci_alpha(misses: list[tuple[int, float]], t_now: int) -> float:
    """The band's miss rate for a week starting at ``t_now``, from past weeks' misses.

    ``misses`` are (rebalance position, share of that week's outcomes outside its band).
    Only weeks whose 20-session outcome is known before ``t_now`` count, in time order.
    """
    alpha = ALPHA
    for t, miss in sorted(misses):
        if t + HORIZON >= t_now:
            break
        if math.isfinite(miss):
            alpha = min(max(alpha + ACI_GAMMA * (ALPHA - miss), ALPHA_BOUNDS[0]), ALPHA_BOUNDS[1])
    return alpha


# --------------------------------------------------------------------------- picking


@dataclass
class Holding:
    """The pick being held, with its expected excess when chosen or last re-scored."""

    stock: int  # panel row
    expected: float


def pick_with_hysteresis(
    held: Holding | None,
    stocks: npt.NDArray[np.int64],
    expected: FloatArray,
    costs: FloatArray | None = None,
) -> tuple[Holding, bool]:
    """Keep the current pick unless the leader beats it by more than its round trip.

    ``costs`` are the stocks' own round-trip costs; without them every stock costs ``COST``.
    """
    lead = int(np.argmax(expected))
    leader = Holding(int(stocks[lead]), float(expected[lead]))
    lead_cost = float(costs[lead]) if costs is not None else COST
    if held is not None:
        where = np.nonzero(stocks == held.stock)[0]
        if len(where):
            current = float(expected[where[0]])
            if leader.expected - current <= lead_cost:
                return Holding(held.stock, current), False
    return leader, held is None or leader.stock != held.stock


@dataclass
class Backtest:
    """Weekly out-of-sample results of the walk-forward."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    feature_ic: dict[str, list[float]] = field(default_factory=dict)
    misses: list[tuple[int, float]] = field(default_factory=list)  # (week t, miss rate)
    forecasts: list[basket.Forecast] = field(default_factory=list)  # what the basket reads


def _cost_of(w: Week, stock: int) -> float:
    where = np.nonzero(w.stocks == stock)[0]
    return float(w.cost[where[0]]) if len(where) else COST


def walk_forward(
    weeks: list[Week], panel: Panel, penalty: float | None = None, variant: Variant = BASE
) -> Backtest:
    """Yearly re-fits on purged history; weekly picks with hysteresis, net of costs.

    ``penalty`` fixes the ridge penalty (used to count the trials for the deflated Sharpe);
    by default it is chosen on each training history. Every purchase pays the round-trip
    cost of the stock bought.
    """
    bt = Backtest(feature_ic={f: [] for f in ALL_FEATURES})
    years = sorted({w.day.year for w in weeks})
    held: Holding | None = None
    held_mom: Holding | None = None
    mom_col = FEATURES.index("mom_12_1")
    for y in years:
        test = [w for w in weeks if w.day.year == y]
        start = test[0].t
        train = _known_before([w for w in weeks if w.day.year < y], panel, start)
        model = (
            fit_ridge(train, penalty, variant)
            if penalty is not None and len(train) >= MIN_TRAIN_WEEKS
            else choose_model(train, panel, variant)
            if penalty is None
            else None
        )
        if model is None:
            continue
        mom = fit_ridge(train, 1.0)
        mom.weights = np.zeros(len(FEATURES))
        mom.weights[mom_col] = 1.0
        xs, _, ys, _ = _stack(train)
        s = xs[:, mom_col]
        if len(s) > 2 and np.var(s) > 0:
            mom.slope = float(np.cov(s, ys, ddof=0)[0, 1] / np.var(s))
            mom.intercept = float(np.mean(ys) - mom.slope * np.mean(s))
        for k, w in enumerate(test):
            nxt = test[k + 1].t if k + 1 < len(test) else _next_rebalance(weeks, w.t, panel)
            exp_m = model.expected(w.z, w.scale)
            held, switched = pick_with_hysteresis(held, w.stocks, exp_m, w.cost)
            held_mom, switched_mom = pick_with_hysteresis(
                held_mom, w.stocks, mom.expected(w.z), w.cost
            )
            gross = hold_excess(panel, held.stock, w.t, nxt)
            gross_mom = hold_excess(panel, held_mom.stock, w.t, nxt)
            cost = _cost_of(w, held.stock)
            cost_mom = _cost_of(w, held_mom.stock)
            universe_rets = [hold_excess(panel, int(i), w.t, nxt) for i in w.stocks]
            bt.forecasts.append(basket.Forecast(w.t, w.day, nxt, w.stocks, exp_m, w.cost, w.scale))
            alpha = aci_alpha(bt.misses, w.t)
            cov = coverage(model, w, alpha)
            if math.isfinite(cov):
                bt.misses.append((w.t, 1 - cov))
            bt.rows.append(
                {
                    "day": w.day,
                    "isin": panel.isins[held.stock],
                    "ticker": panel.tickers[held.stock],
                    "expected": held.expected,
                    "switched": switched,
                    "cost": cost,
                    "gross": gross,
                    "net": gross - (cost if switched else 0.0),
                    "momentum_net": gross_mom - (cost_mom if switched_mom else 0.0),
                    "basket": float(np.nanmean(universe_rets)) if universe_rets else math.nan,
                    # diagnostic: the model's ten best scores, equal weights, before costs
                    "top10": float(
                        np.nanmean(
                            [universe_rets[i] for i in np.argsort(-exp_m, kind="stable")[:TOP_N]]
                        )
                    ),
                    # of the expected excess, the number the pick is chosen on: for a scaled
                    # variant it ranks differently from the bare score
                    "ic": rank_ic(exp_m, w.y),
                    "coverage": cov,
                    "alpha": alpha,
                    "regime": w.regime,
                    "band": (model.band_for(w.regime) or (None,))[0],
                    "universe": len(w.stocks),
                    "model_end": model.train_end,
                    "penalty": model.penalty,
                }
            )
            for j, f in enumerate(ALL_FEATURES):
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


def _nanmean_or_none(df: pl.DataFrame, col: str) -> float | None:
    if col not in df.columns:
        return None
    v = df[col].to_numpy().astype(np.float64)
    return float(np.nanmean(v)) if np.isfinite(v).any() else None


def net_verdict(net: FloatArray, days: list[date]) -> dict[str, Any]:
    """Mean weekly net excess, its one-sided p (clustered by month) and the verdict."""
    ok = np.isfinite(net)
    weeks = int(ok.sum())
    months = [f"{d:%Y-%m}" for d, k in zip(days, ok, strict=True) if k]
    t_stat, _, g = study.cluster_t(net[ok], months)
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
    return {
        "verdict": verdict,
        "weeks": weeks,
        "mean_net": mean,
        "p_value": None if not math.isfinite(p) else p,
        "total_net": float(np.prod(1 + net[ok]) - 1) if weeks else None,
        "max_drawdown": max_drawdown(net),
    }


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
        "band_coverage": _nanmean_or_none(df, "coverage"),
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
    "cost": pl.Float64,  # round trip of the stock, paid when switched (missing: COST)
    "variant": pl.String,
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
    scale, costs = _scale_and_cost(panel, t, rows)
    exp = model.expected(rank_normal(raw_features(panel, t)[rows]), scale)
    held = None
    if old is not None and old.height:
        last = old.sort("week").row(-1, named=True)
        if last["isin"] in panel.isins:
            held = Holding(panel.isins.index(last["isin"]), float(last["expected"]))
    choice, switched = pick_with_hysteresis(held, rows, exp, costs)
    new = pl.DataFrame(
        [
            {
                "week": day,
                "isin": panel.isins[choice.stock],
                "ticker": panel.tickers[choice.stock],
                "expected": choice.expected,
                "switched": switched,
                "as_of": as_of,
                "cost": float(costs[int(np.nonzero(rows == choice.stock)[0][0])]),
                "variant": model.variant.name,
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
        paid = r.get("cost")
        paid = COST if paid is None or not math.isfinite(paid) else paid
        rows.append({**r, "gross": g, "net": g - (paid if r["switched"] else 0.0)})
    return pl.DataFrame(rows, schema={**LIVE_SCHEMA, "gross": pl.Float64, "net": pl.Float64})


# --------------------------------------------------------------------------- run


def compute(
    inputs: PickInputs,
    ledger: pl.DataFrame | None = None,
    basket_live: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Everything the page needs, from the inputs and the forecast ledger (no lake writes)."""
    # Empty slices (a stock with no trades in a window, the last week's open holding) are
    # expected and come out as NaN; numpy's warnings about them are noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _compute(inputs, ledger, basket_live)


def _band_summary(model: Model | None, regime: str | None, alpha: float) -> dict[str, Any]:
    out: dict[str, Any] = {"regime": regime, "alpha": alpha}
    used = model.band_for(regime, alpha) if model is not None else None
    out["band_used"] = used[0] if used else None
    out["band_lo_q"] = used[1][0] if used else None
    out["band_hi_q"] = used[1][1] if used else None
    for name in ("all", "calm", "stress"):  # each at the target 90 %, for the record
        b = model.band_for(name) if model is not None and name in model.errors else None
        out[f"band_{name}_lo_q"] = b[1][0] if b else None
        out[f"band_{name}_hi_q"] = b[1][1] if b else None
    return out


def ledger_coverage(panel: Panel, ledger: pl.DataFrame | None) -> pl.DataFrame:
    """Per ledger week: how many outcomes are known and the share inside their band."""
    schema = {
        "week": pl.Date,
        "t": pl.Int64,
        "alpha": pl.Float64,
        "known": pl.Int64,
        "coverage": pl.Float64,
    }
    if ledger is None or not ledger.height:
        return pl.DataFrame(schema=schema)
    pos = {s: i for i, s in enumerate(panel.isins)}
    rows = []
    for (week,), g in ledger.sort("week").group_by("week", maintain_order=True):
        if week not in panel.cal:
            continue
        t = panel.cal.index(week)
        fwd = forward_excess(panel, t)
        y = np.array([fwd[pos[s]] if s in pos else np.nan for s in g["isin"].to_list()])
        lo, hi = g["band_lo"].to_numpy(), g["band_hi"].to_numpy()
        k = np.isfinite(y) & np.isfinite(lo) & np.isfinite(hi)
        rows.append(
            {
                "week": week,
                "t": t,
                "alpha": float(g["alpha"][0]),
                "known": int(k.sum()),
                "coverage": float(np.mean((y[k] >= lo[k]) & (y[k] <= hi[k])))
                if k.any()
                else math.nan,
            }
        )
    return pl.DataFrame(rows, schema=schema)


def _history(bt: Backtest, ledger_cov: pl.DataFrame) -> list[tuple[int, float]]:
    """Misses that set α: the walk-forward's, then from the first live week the ledger's."""
    if not ledger_cov.height:
        return list(bt.misses)
    first = int(ledger_cov["t"].min())  # type: ignore[arg-type]
    live = [
        (int(t), 1 - c)
        for t, c in zip(ledger_cov["t"], ledger_cov["coverage"], strict=True)
        if c is not None and math.isfinite(c)
    ]
    return [m for m in bt.misses if m[0] < first] + live


def _mean_ic(bt: Backtest) -> float:
    ic = np.array([r["ic"] for r in bt.rows], dtype=np.float64)
    return float(np.nanmean(ic)) if np.isfinite(ic).any() else -math.inf


def _band_columns(
    model: Model,
    z: FloatArray,
    scale: FloatArray,
    order: npt.NDArray[np.int64],
    regime: str | None,
    alpha: float,
) -> dict[str, FloatArray]:
    iv = model.interval(z, scale, regime, alpha)
    if iv is None:
        nan = np.full(len(order), np.nan)
        return {"band_lo": nan, "band_hi": nan.copy()}
    return {"band_lo": iv[0][order], "band_hi": iv[1][order]}


def _held_from(panel: Panel, live: pl.DataFrame | None, day: date) -> dict[int, float]:
    """The basket's weights from the last live week before ``day``."""
    if live is None or not live.height:
        return {}
    before = live.filter(pl.col("week") < day)
    if not before.height:
        return {}
    last = before.filter(pl.col("week") == before["week"].max())
    pos = {s: i for i, s in enumerate(panel.isins)}
    return {
        pos[s]: float(w)
        for s, w in zip(last["isin"], last["weight"], strict=True)
        if s in pos and w is not None
    }


def _basket_now(
    panel: Panel,
    t_now: int,
    rows: npt.NDArray[np.int64],
    exp: FloatArray,
    costs: FloatArray,
    scale: FloatArray,
    held: dict[int, float],
) -> tuple[pl.DataFrame, float]:
    f = basket.Forecast(t_now, panel.cal[t_now], t_now, rows, exp, costs, scale)
    new, cost = basket.step(panel, basket.daily_residuals(panel), f, held)
    pos = {int(s): k for k, s in enumerate(rows)}
    df = pl.DataFrame(
        [
            {
                "isin": panel.isins[i],
                "ticker": panel.tickers[i],
                "weight": w,
                "expected": float(exp[pos[i]]),
                "cost": float(costs[pos[i]]),
                "held_before": held.get(i, 0.0),
            }
            for i, w in sorted(new.items(), key=lambda kv: -kv[1])
        ],
        schema={
            "isin": pl.String,
            "ticker": pl.String,
            "weight": pl.Float64,
            "expected": pl.Float64,
            "cost": pl.Float64,
            "held_before": pl.Float64,
        },
    )
    return df, cost


def _headline(basket_now: pl.DataFrame, scores: pl.DataFrame) -> dict[str, Any]:
    """The stock of the week: the basket's largest position, else the top score."""
    if basket_now.height:
        r = basket_now.row(0, named=True)  # sorted by weight
        return {
            "headline_isin": r["isin"],
            "headline_ticker": r["ticker"],
            "headline_weight": r["weight"],
            "headline_source": "basket",
        }
    if scores.height:
        r = scores.row(0, named=True)
        return {
            "headline_isin": r["isin"],
            "headline_ticker": r["ticker"],
            "headline_weight": 0.0,
            "headline_source": "top_score",
        }
    return {
        "headline_isin": None,
        "headline_ticker": None,
        "headline_weight": None,
        "headline_source": None,
    }


def headline_record(live: pl.DataFrame | None, isin: str | None) -> dict[str, Any]:
    """Whether the stock of the week is in the latest live basket, and since which week."""
    if live is None or not live.height or isin is None:
        return {"headline_live": False, "headline_since": None}
    weeks = sorted(set(live["week"].to_list()), reverse=True)
    held = {wk: set(g["isin"].to_list()) for (wk,), g in live.group_by("week", maintain_order=True)}
    if isin not in held[weeks[0]]:
        return {"headline_live": False, "headline_since": None}
    since = weeks[0]
    for wk in weeks[1:]:
        if isin not in held[wk]:
            break
        since = wk
    return {"headline_live": True, "headline_since": since}


def basket_live_results(panel: Panel, live: pl.DataFrame | None) -> pl.DataFrame:
    """Each live basket week's result against WIG while it was held, net of its trades."""
    schema = {
        "week": pl.Date,
        "names": pl.Int64,
        "top_ticker": pl.String,
        "gross": pl.Float64,
        "cost": pl.Float64,
        "net": pl.Float64,
    }
    if live is None or not live.height:
        return pl.DataFrame(schema=schema)
    weeks = sorted(set(live["week"].to_list()))
    pos = {s: i for i, s in enumerate(panel.isins)}
    out = []
    for k, wk in enumerate(weeks):
        if wk not in panel.cal:
            continue
        t = panel.cal.index(wk)
        t2 = (
            panel.cal.index(weeks[k + 1])
            if k + 1 < len(weeks) and weeks[k + 1] in panel.cal
            else len(panel.cal) - 1
        )
        g = live.filter(pl.col("week") == wk)
        parts = [
            (float(w), hold_excess(panel, pos[s], t, t2))
            for s, w in zip(g["isin"], g["weight"], strict=True)
            if s in pos
        ]
        known = [(w, x) for w, x in parts if math.isfinite(x)]
        gross = float(sum(w * x for w, x in known)) if known else math.nan
        paid = float(g["trade_cost"][0]) if "trade_cost" in g.columns else 0.0
        top = g.sort("weight", descending=True)["ticker"][0]
        out.append(
            {
                "week": wk,
                "names": g.height,
                "top_ticker": top,
                "gross": gross,
                "cost": paid,
                "net": gross - paid,
            }
        )
    return pl.DataFrame(out, schema=schema)


def record_basket(
    data_dir: Path, panel: Panel, now: pl.DataFrame, cost: float, day: date, as_of: datetime
) -> pl.DataFrame | None:
    """Append this week's basket on its first session; never rewrite."""
    old = lake.read_state(data_dir, BASKET_STATE)
    if old is not None and old.filter(pl.col("week") == day).height:
        return old
    if not now.height or not is_rebalance_day(panel, day) or panel.cal[-1] != day:
        return old
    new = now.select("isin", "ticker", "weight").with_columns(
        pl.lit(day).alias("week"),
        pl.lit(cost, dtype=pl.Float64).alias("trade_cost"),
        pl.lit(as_of).cast(pl.Datetime("us", "UTC")).alias("as_of"),
    )
    df = new if old is None else pl.concat([old, new], how="diagonal_relaxed")
    df = df.sort(["week", "isin"])
    lake.write_state(data_dir, BASKET_STATE, df)
    log.info("basket %s: %d names", day, new.height)
    return df


def _compute(
    inputs: PickInputs, ledger: pl.DataFrame | None, basket_live: pl.DataFrame | None = None
) -> dict[str, Any]:
    panel = build_panel(inputs)
    weeks = build_weeks(panel)
    runs = {v.name: walk_forward(weeks, panel, variant=v) for v in VARIANTS}
    # The best out-of-sample rank IC wins; a richer variant must beat the simpler ones.
    variant = BASE
    for v in VARIANTS[1:]:
        if _mean_ic(runs[v.name]) > _mean_ic(runs[variant.name]):
            variant = v
    bt = runs[variant.name]
    trials = [
        np.array([r["net"] for r in walk_forward(weeks, panel, p, v).rows], dtype=np.float64)
        for v in VARIANTS
        for p in PENALTIES
    ]
    trials.append(np.array([r["momentum_net"] for r in bt.rows], dtype=np.float64))
    summary = summarise(bt, trials)
    ablation = pl.DataFrame(
        [
            {
                "variant": v.name,
                "features": len(v.features),
                "scaled": v.scaled,
                "adopted": v == variant,
                **{
                    k: summarise(runs[v.name], []).get(k)
                    for k in (
                        "oos_weeks",
                        "ic_mean",
                        "ic_t",
                        "mean_net",
                        "total_net",
                        "switches",
                        "band_coverage",
                    )
                },
            }
            for v in VARIANTS
        ]
    )
    t_now = len(panel.cal) - 1
    final = choose_model(_known_before(weeks, panel, t_now + 1), panel, variant)
    ledger_cov = ledger_coverage(panel, ledger)
    alpha_now = aci_alpha(_history(bt, ledger_cov), t_now + 1)
    regime_now = regime_of(panel, t_now) if t_now >= 0 else None
    scores = pl.DataFrame()
    basket_now = pl.DataFrame()
    basket_cost = 0.0
    if final is not None and t_now >= 0:
        ok = eligible(panel, t_now)
        rows = np.nonzero(ok)[0]
        if len(rows) >= MIN_UNIVERSE:
            raw = raw_features(panel, t_now)[rows]
            z = rank_normal(raw)
            cols = final.cols
            contrib = z[:, cols] * final.weights
            scale, costs = _scale_and_cost(panel, t_now, rows)
            exp = final.expected(z, scale)
            order = np.argsort(-exp, kind="stable")
            # what the report-based features add to the expected excess, in its own units
            news = [j for j, f in enumerate(final.features) if f in NEWS_FEATURES]
            unit = final.slope * (scale if final.variant.scaled else 1.0)
            news_effect = contrib[:, news].sum(axis=1) * unit if news else np.zeros(len(rows))
            scores = pl.DataFrame(
                {
                    "isin": [panel.isins[rows[i]] for i in order],
                    "ticker": [panel.tickers[rows[i]] for i in order],
                    "rank": list(range(1, len(order) + 1)),
                    "score": final.score(z)[order],
                    "expected": exp[order],
                    "cost": costs[order],
                    "expected_net": exp[order] - costs[order],
                    "news_effect": news_effect[order],
                    **_band_columns(final, z, scale, order, regime_now, alpha_now),
                    **{f"f_{f}": raw[order, cols[j]] for j, f in enumerate(final.features)},
                    **{f"c_{f}": contrib[order, j] for j, f in enumerate(final.features)},
                }
            )
            held = _held_from(panel, basket_live, panel.cal[t_now])
            basket_now, basket_cost = _basket_now(panel, t_now, rows, exp, costs, scale, held)
    feats = list(variant.features)
    model_df = pl.DataFrame(
        {
            "feature": feats,
            "weight": (final.weights if final is not None else np.full(len(feats), np.nan)),
            "oos_ic": [
                float(np.nanmean(v)) if np.isfinite(v).any() else math.nan
                for v in (np.array(bt.feature_ic[f], dtype=np.float64) for f in feats)
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
        **_band_summary(final, regime_now, alpha_now),
        **_ledger_summary(ledger_cov),
        "variant": variant.name,
        "uses_state": inputs.state is not None,
    }
    b_rows, b_weights = basket.walk(panel, bt.forecasts)
    basket_bt = pl.DataFrame(b_rows) if b_rows else pl.DataFrame()
    if basket_bt.height:
        bv = net_verdict(basket_bt["net"].to_numpy().astype(np.float64), basket_bt["day"].to_list())
        summary |= {f"basket_{k}": v for k, v in bv.items()} | {
            "basket_turnover": float(basket_bt["turnover"].mean()),  # type: ignore[arg-type]
            "basket_names": float(basket_bt["names"].mean()),  # type: ignore[arg-type]
            "basket_cost_total": float(basket_bt["cost"].sum()),
        }
    # The basket is the decision (DECISIONS, 2026-11-12): its verdict is the headline one,
    # and the stock of the week is its largest position.
    summary["single_verdict"] = summary["verdict"]
    summary["verdict"] = summary.get("basket_verdict", "too_short")
    summary |= _headline(basket_now, scores)
    summary["basket_now_names"] = basket_now.height
    summary["basket_now_cost"] = basket_cost
    backtest = pl.DataFrame(bt.rows) if bt.rows else pl.DataFrame()
    return {
        "panel": panel,
        "model": final,
        "summary": summary,
        "scores": scores,
        "model_df": model_df,
        "backtest": backtest,
        "ablation": ablation,
        "ledger_coverage": ledger_cov,
        "basket_now": basket_now,
        "basket_cost": basket_cost,
        "basket_backtest": basket_bt,
        "basket_weights": pl.DataFrame(b_weights) if b_weights else pl.DataFrame(),
    }


def _ledger_summary(cov: pl.DataFrame) -> dict[str, Any]:
    known = cov.filter(pl.col("coverage").is_not_nan()) if cov.height else cov
    return {
        "ledger_weeks": cov.height,
        "ledger_scored_weeks": known.height,
        "live_coverage": float(known["coverage"].mean()) if known.height else None,  # type: ignore[arg-type]
    }


def record_forecasts(
    data_dir: Path, panel: Panel, scores: pl.DataFrame, summary: dict[str, Any], day: date
) -> pl.DataFrame | None:
    """Append this week's forecasts for every scored stock on its first session; never rewrite."""
    old = lake.read_state(data_dir, LEDGER_STATE)
    if old is not None and old.filter(pl.col("week") == day).height:
        return old
    if not scores.height or not is_rebalance_day(panel, day) or panel.cal[-1] != day:
        return old
    new = scores.select("isin", "ticker", "expected", "band_lo", "band_hi", "cost").with_columns(
        pl.lit(day).alias("week"),
        pl.lit(summary["alpha"], dtype=pl.Float64).alias("alpha"),
        pl.lit(summary["regime"], dtype=pl.String).alias("regime"),
        pl.lit(summary["band_used"], dtype=pl.String).alias("band"),
        pl.lit(summary["variant"], dtype=pl.String).alias("variant"),
        pl.lit(summary["as_of"]).cast(pl.Datetime("us", "UTC")).alias("as_of"),
    )
    df = new if old is None else pl.concat([old, new], how="diagonal_relaxed")
    df = df.sort(["week", "isin"])
    lake.write_state(data_dir, LEDGER_STATE, df)
    log.info("forecast ledger %s: %d stocks", day, new.height)
    return df


def run_pick(duckdb_file: Path, work: Path, data_dir: Path, day: date) -> Path:
    """Compute the pick, write ``gold/pick_*.parquet`` and append the live records.

    The basket's weekly record is the live record; the forecast ledger records every scored
    stock. The single pick's own record (``state/picks``) is no longer appended to: the
    basket replaced it as the decision before its first live week.
    """
    from undercurrent.pick_features import load_inputs

    as_of = study.as_of_for(day)
    ledger = lake.read_state(data_dir, LEDGER_STATE)
    if ledger is not None:  # a rerun of an earlier day must not see later weeks
        ledger = ledger.filter(pl.col("week") <= day)
    basket_live = lake.read_state(data_dir, BASKET_STATE)
    if basket_live is not None:
        basket_live = basket_live.filter(pl.col("week") <= day)
    out = compute(load_inputs(duckdb_file, as_of), ledger, basket_live)
    gold = work / "gold"
    if out["model"] is not None:
        record_forecasts(data_dir, out["panel"], out["scores"], out["summary"], day)
        basket_live = (
            record_basket(data_dir, out["panel"], out["basket_now"], out["basket_cost"], day, as_of)
            or basket_live
        )
    out["summary"] |= headline_record(basket_live, out["summary"].get("headline_isin"))
    summary = pl.DataFrame([out["summary"]])
    lake.write_frame(summary, gold / "pick_summary.parquet")
    lake.write_frame(out["scores"], gold / "pick_scores.parquet")
    lake.write_frame(out["model_df"], gold / "pick_model.parquet")
    lake.write_frame(out["backtest"], gold / "pick_backtest.parquet")
    lake.write_frame(out["ablation"], gold / "pick_ablation.parquet")
    lake.write_frame(out["ledger_coverage"], gold / "pick_ledger.parquet")
    lake.write_frame(out["basket_now"], gold / "pick_basket.parquet")
    lake.write_frame(out["basket_backtest"], gold / "pick_basket_backtest.parquet")
    lake.write_frame(out["basket_weights"], gold / "pick_basket_weights.parquet")
    lake.write_frame(
        basket_live_results(out["panel"], basket_live), gold / "pick_basket_live.parquet"
    )
    log.info(
        "pick: verdict=%s weeks=%s universe=%s variant=%s",
        out["summary"]["verdict"],
        out["summary"].get("oos_weeks"),
        out["summary"]["universe"],
        out["summary"]["variant"],
    )
    return gold / "pick_summary.parquet"
