"""Data for the site's analysis pages: the market, the screener, and one file per stock.

``build`` reads the work dir (adjusted prices, the market state, signal events, the forecast,
ESEF snapshots) up to the run's session and writes JSON the browser loads:

* ``market.json``: the session calendar since the lake begins (positions every other file
  refers to), its segments, WIG and the other indices, the regime's stress probability, and
  the main market's breadth each session;
* ``screener.json``: one row per listed stock at the last session;
* ``stocks/<ISIN>.json``: one stock's full history, loaded only by its own page.

Every number is computed here; the browser draws, sorts and filters, and recomputes an
indicator only when a reader changes its parameter (``site/static/js/indicators.js`` has the
same formulas as ``ta.py``). Nothing after the run's session is read.

A stock file is columnar. ``pos`` gives the stock's sessions as runs of calendar positions
``[first, count]``; ``o h l c`` are the exchange's own prices and ``adj`` the adjustment
factor's change points ``[row, factor]`` (adjusted price = price × factor, the factor that
``adjusted_prices`` applies for dividends, splits and rights issues); ``tv`` turnover in
thousands of złoty (the volume bars: unlike a share count it survives a split); ``s20 beta
z`` the market state's 20-session σ, Kalman beta to WIG and surprise; ``ev`` the stock's
signal events ``[row, kind, title, page]``, the kind an index into ``kinds``, the title kept
for the latest ``TITLED_EVENTS`` only (the page lists those; older markers keep their link).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt
import polars as pl

from undercurrent import pages as page_text
from undercurrent import risk, sectors, setups, ta, valuation

log = logging.getLogger(__name__)
FloatArray = npt.NDArray[np.float64]

FORMAT = 1
LISTED_WITHIN = 5  # a stock without a session among the last 5 is no longer listed
WINDOWS = (5, 20, 60, 250)  # sessions of the screener's returns against WIG
INSIDER_WINDOW = 20  # sessions of insider buying net of selling
TOP = 8  # movers and surprises on the market page
INDICES = ("WIG", "WIG20", "MWIG40", "SWIG80", "TBSP.INDEX")
FUND_STALE_DAYS = 550  # as pick_features: an annual report older than this is not shown
TITLE_CHARS = 90
TITLED_EVENTS = 60  # the latest events keep their titles; older ones only a kind and link
STAGING = "site_data"


@dataclass
class ToolData:
    """What the templates need about the analysis pages (the files are on disk)."""

    day: date
    staging: Path
    stocks: list[dict[str, Any]] = field(default_factory=list)  # every stock with a page
    screener: list[dict[str, Any]] = field(default_factory=list)
    market: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, dict[str, Any]] = field(default_factory=dict)  # risk.profile per ISIN
    valuation: dict[str, dict[str, Any]] = field(default_factory=dict)  # per ISIN, listed GPW

    @property
    def slug_by_isin(self) -> dict[str, str]:
        """Each stock's page name."""
        return {s["isin"]: s["slug"] for s in self.stocks}


# --------------------------------------------------------------------------- helpers


def _r(x: Any, digits: int = 4) -> float | None:
    """``x`` to ``digits`` significant digits; None when missing (JSON null)."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return float(f"{x:.{digits}g}")


def _price(x: Any) -> float | None:
    """A price as the exchange prints it: at most 4 decimals."""
    if x is None or not math.isfinite(float(x)):
        return None
    return round(float(x), 4)


def _add_valuation(
    tool: ToolData,
    closes: dict[str, float],
    facts: pl.DataFrame | None,
    gpw: pl.DataFrame | None,
    day: date,
) -> None:
    """Valuation of every listed main-market stock (``valuation.py``).

    Into the screener rows and ``tool.valuation``; peers are ranked among these stocks.
    """
    from undercurrent import esef

    reports: dict[str, list[dict[str, Any]]] = {}
    if facts is not None and facts.height:
        for st in esef.statements(facts):
            reports.setdefault(st["isin"], []).append(st)
    table = {r["isin"]: r for r in gpw.iter_rows(named=True)} if gpw is not None else {}
    rows: list[dict[str, Any]] = []
    for isin, close in closes.items():
        rep = valuation.report_on(reports.get(isin, []), day)
        if rep is not None and rep["currency"] != "PLN":
            rep = None  # a report in euros against a price in złoty: not without FX rates
        last = valuation.last_report(reports.get(isin, []), day)
        g = table.get(isin)
        y = valuation.yields(close, g, rep)
        rows.append(
            {
                "isin": isin,
                "sector_code": g["sector_code"] if g else None,
                "sector": g["sector"] if g else None,
                "gpw": g,
                "report": rep,
                "last": last,
                **y,
            }
        )
    valuation.percentiles(rows)
    screener = {r["isin"]: r for r in tool.screener}
    for r in rows:
        rep, g = r["report"], r["gpw"]
        f = valuation.piotroski(rep["cur"], rep["prev"]) if rep else None
        z = (
            None
            if rep is None or valuation.is_financial(r["sector"])
            else valuation.altman(rep["cur"], r["market_cap"])
        )
        dcf = {
            f"{rate:.2f}": valuation.implied_growth(r["fcf"], r["market_cap"], rate)
            for rate in valuation.DISCOUNT_RATES
        }
        detail = {
            k: r.get(k)
            for k in (
                "market_cap", "shares", "shares_source", "sector", "sector_code", "peer_group",
                "ep", "bp", "fcf", "fcf_yield", "ebit_yield", "dividend_yield", "value_score",
                "value_score_pct",
                *(f"{k}_pct" for k in valuation.YIELD_KEYS),
            )
        }  # fmt: skip
        detail |= {
            "pe": 1 / r["ep"] if r.get("ep") and r["ep"] > 0 else None,
            "pbv": 1 / r["bp"] if r.get("bp") and r["bp"] > 0 else None,
            "fscore": f,
            "altman": z,
            "dcf": dcf if any(x is not None for x in dcf.values()) else None,
            "fiscal_year_end": rep["fiscal_year_end"] if rep else None,
            "report_known_at": rep["known_at"] if rep else None,
            "gpw_report_date": g["report_date"] if g else None,
            # the last report however old, for its own scores only (not against today's price)
            "stale": None
            if rep is not None or r["last"] is None
            else {
                "fiscal_year_end": r["last"]["fiscal_year_end"],
                "fscore": valuation.piotroski(r["last"]["cur"], r["last"]["prev"]),
            },
            "gpw_day": g["trade_date"] if g else None,
        }
        tool.valuation[r["isin"]] = detail
        row = screener.get(r["isin"])
        if row is None:
            continue
        row |= {
            "mcap": _r(detail["market_cap"]),
            "pe": _r(detail["pe"], 3),
            "pbv": _r(detail["pbv"], 3),
            "dy": _r(detail["dividend_yield"], 3),
            "fcfy": _r(detail["fcf_yield"], 3),
            "value": _r(detail["value_score_pct"], 3),
            "fscore": f["score"] if f and f["seen"] >= valuation.QUALITY_SEEN else None,
            "altman": _r(z["z"], 3) if z else None,
        }
        if (detail["value_score_pct"] or 0.0) >= valuation.CHEAP:
            row["presets"].append("cheap")
        if f and f["seen"] >= valuation.QUALITY_SEEN and f["score"] >= valuation.QUALITY:
            row["presets"].append("quality")
        if z and z["zone"] == "distress":
            row["presets"].append("distress")


def _risk_json(rk: dict[str, Any] | None) -> dict[str, Any] | None:
    """``risk.profile`` rounded for the page."""
    if rk is None:
        return None
    out = {k: (v if k == "nu" else _r(v)) for k, v in rk.items() if k != "backtest"}
    out["backtest"] = {
        k: (_r(v) if isinstance(v, float) and k.startswith("p") else v)
        for k, v in rk["backtest"].items()
    }
    return out


def _thousands(x: Any) -> float | None:
    """Turnover in thousands of złoty: whole thousands, two digits below ten thousand."""
    if x is None or not math.isfinite(float(x)):
        return None
    k = float(x) / 1000
    return float(round(k)) if k >= 10 else _r(k, 2)


def _runs(pos: npt.NDArray[np.int64]) -> list[list[int]]:
    """Calendar positions as runs ``[first, count]``."""
    out: list[list[int]] = []
    for p in pos.tolist():
        if out and out[-1][0] + out[-1][1] == p:
            out[-1][1] += 1
        else:
            out.append([p, 1])
    return out


def _change_points(f: FloatArray) -> list[list[float]]:
    """``[row, value]`` wherever the (rounded) factor changes, starting at row 0."""
    out: list[list[float]] = []
    for i, x in enumerate(f.tolist()):
        v = _r(x, 7)
        v = 1.0 if v is None else v
        if not out or out[-1][1] != v:
            out.append([i, v])
    return out


def slugify(ticker: str | None, isin: str, taken: set[str]) -> str:
    """A stock page's file name: its ticker in lower case, the ISIN when that is taken."""
    base = re.sub(r"[^a-z0-9]+", "-", (ticker or "").lower()).strip("-") or isin.lower()
    slug = base if base not in taken else f"{base}-{isin.lower()}"
    taken.add(slug)
    return slug


def _excess(
    adj: FloatArray, pos: npt.NDArray[np.int64], wig: FloatArray, seg: npt.NDArray[np.int64], k: int
) -> float | None:
    """Stock minus WIG, compounded, over the last ``k`` calendar sessions (same segment)."""
    p = int(pos[-1])
    q = p - k
    if q < 0 or seg[q] != seg[p] or not (math.isfinite(wig[p]) and math.isfinite(wig[q])):
        return None
    j = int(np.searchsorted(pos, q, side="right")) - 1  # the stock's last close at or before q
    if j < 0 or seg[pos[j]] != seg[p]:
        return None
    return float(adj[-1] / adj[j] - wig[p] / wig[q])


# --------------------------------------------------------------------------- reading


def _read(work: Path, day: date) -> dict[str, Any]:
    from undercurrent.transform import duckdb_path

    con = duckdb.connect(str(duckdb_path(work)))  # as every other reader: one config per process
    try:
        cal = con.sql(
            "select trade_date, after_hole from sessions where trade_date <= ? order by 1",
            params=[day],
        ).pl()
        prices = con.sql(
            "select a.isin, a.trade_date, a.market, a.ticker, a.short_name, a.close, a.adj_close, "
            "a.adj_ret, a.volume, a.value_pln, p.open, p.high, p.low "
            "from adjusted_prices a left join prices_daily p using (isin, trade_date) "
            "where a.trade_date <= ? order by a.isin, a.trade_date",
            params=[day],
        ).pl()
        indices = con.sql(
            "select name, trade_date, close from index_returns where trade_date <= ?",
            params=[day],
        ).pl()
        sector_idx = con.sql(
            "select name, trade_date, close from bronze_indices_gpw "
            "where upper(name) like 'WIG-%' and trade_date <= ? "
            "qualify row_number() over "
            "(partition by upper(name), trade_date order by ingested_at desc) = 1",
            params=[day],
        ).pl()
        events = con.sql(
            "select report_id, isin, event_date, signal_type, subtype, insider_side, "
            "insider_value, title from events "
            "where isin is not null and event_date is not null and event_date <= ? "
            "order by isin, event_date, report_id",
            params=[day],
        ).pl()
        names = con.sql(
            "select isin, arg_max(issuer_name, known_at) as name from reports "
            "where isin is not null and issuer_name is not null group by isin"
        ).pl()
        tables = {
            r[0] for r in con.sql("select table_name from information_schema.tables").fetchall()
        }
        facts = con.sql("select * from fundamentals").pl() if "fundamentals" in tables else None
    finally:
        con.close()
    state_path = work / "gold" / "market_state.parquet"
    state = None
    if state_path.exists():
        cols = ["isin", "trade_date", "sigma", "sigma_20", "beta_wig", "surprise", "regime_stress"]
        cols += ["liquidity_tier", "median_turnover", "cost", "signal_effect"]
        state = (
            pl.scan_parquet(state_path).select(cols).filter(pl.col("trade_date") <= day).collect()
        )
    return {
        "cal": cal,
        "prices": prices,
        "indices": indices,
        "sectors": sector_idx,
        "events": events,
        "names": names,
        "facts": facts,
        "state": state,
    }


def _fundamentals(facts: pl.DataFrame | None, day: date) -> dict[str, dict[str, Any]]:
    """The latest ESEF snapshot known by the run, per ISIN, while it is fresh."""
    if facts is None or not facts.height:
        return {}
    from undercurrent import esef
    from undercurrent.study import as_of_for

    snaps = esef.snapshots(facts).filter(pl.col("known_at") <= as_of_for(day))
    out: dict[str, dict[str, Any]] = {}
    for r in snaps.sort("known_at").iter_rows(named=True):
        out[r["isin"]] = r
    return {k: v for k, v in out.items() if (day - v["fiscal_year_end"]).days <= FUND_STALE_DAYS}


def _event_kind(r: dict[str, Any]) -> str:
    from undercurrent.study import signal_key

    return signal_key(r["signal_type"], r["subtype"], r["insider_side"])


# --------------------------------------------------------------------------- build


class _Breadth:
    """Main-market breadth per calendar session: counts of stocks, summed stock by stock."""

    KEYS = (
        "adv",
        "dec",
        "with_ret",
        "above50",
        "n50",
        "above200",
        "n200",
        "new_hi",
        "new_lo",
        "n_year",
    )

    def __init__(self, n: int) -> None:
        self.counts = {k: np.zeros(n, dtype=np.int64) for k in self.KEYS}

    def add(
        self,
        pos: npt.NDArray[np.int64],
        ret: FloatArray,
        c: FloatArray,
        ind: dict[str, FloatArray],
    ) -> None:
        k = self.counts
        ok = np.isfinite(ret)
        np.add.at(k["with_ret"], pos[ok], 1)
        np.add.at(k["adv"], pos[ok & (ret > 0)], 1)
        np.add.at(k["dec"], pos[ok & (ret < 0)], 1)
        for sma, above, cnt in (("sma50", "above50", "n50"), ("sma200", "above200", "n200")):
            m = np.isfinite(ind[sma])
            np.add.at(k[cnt], pos[m], 1)
            np.add.at(k[above], pos[m & (c > ind[sma])], 1)
        m = np.isfinite(ind["hi52"])
        np.add.at(k["n_year"], pos[m], 1)
        np.add.at(k["new_hi"], pos[m & (ind["h"] >= ind["hi52"])], 1)
        np.add.at(k["new_lo"], pos[m & (ind["l"] <= ind["lo52"])], 1)


def _indicators(
    o: FloatArray, h: FloatArray, lo: FloatArray, c: FloatArray, seg: npt.NDArray[np.int64]
) -> dict[str, FloatArray]:
    hh, ll = ta.fill_range(h, lo, c)
    hi52, lo52 = ta.high_low(hh, ll, seg)
    return {
        "h": hh,
        "l": ll,
        "sma50": ta.sma(c, 50, seg),
        "sma200": ta.sma(c, 200, seg),
        "rsi": ta.rsi(c, ta.RSI_N, seg),
        "atr": ta.atr(hh, ll, c, ta.ATR_N, seg),
        "hi52": hi52,
        "lo52": lo52,
    }


def build(
    work: Path,
    day: date,
    *,
    pages: set[str] | None = None,
    forecast: dict[str, dict[str, Any]] | None = None,
    indicators: pl.DataFrame | None = None,
    logos: dict[str, str] | None = None,
) -> ToolData:
    """Write the analysis pages' JSON into ``work/site_data`` and return what pages need.

    ``pages``: report IDs that have an event page (their markers link to it); ``forecast``:
    the forecast per ISIN (``publish._forecast_rows``); ``indicators``: GPW's latest
    indicators per ISIN (``indicators.latest``) for the valuation; ``logos``: each ISIN's
    icon file under the site's ``logos/`` (``logos.read_logos``).
    """
    pages = pages or set()
    forecast = forecast or {}
    logos = logos or {}
    src = _read(work, day)
    staging = work / STAGING
    stocks_dir = staging / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)
    for old in stocks_dir.glob("*.json"):
        old.unlink()

    cal_df: pl.DataFrame = src["cal"]
    cal: list[date] = cal_df["trade_date"].to_list()
    idx = {d: i for i, d in enumerate(cal)}
    seg = np.cumsum(cal_df["after_hole"].fill_null(False).cast(pl.Int64).to_numpy())
    n_cal = len(cal)
    tool = ToolData(day=day, staging=staging)
    if not n_cal:
        return tool

    idx_close: dict[str, FloatArray] = {}
    for (name,), g in src["indices"].group_by(["name"]):
        a = np.full(n_cal, np.nan)
        for d, v in zip(g["trade_date"].to_list(), g["close"].to_list(), strict=True):
            if d in idx and v is not None:
                a[idx[d]] = v
        idx_close[str(name)] = a
    wig = idx_close.get("WIG", np.full(n_cal, np.nan))

    state_by: dict[str, pl.DataFrame] = {}
    regime = np.full(n_cal, np.nan)
    if src["state"] is not None and src["state"].height:
        st: pl.DataFrame = src["state"]
        reg = st.group_by("trade_date").agg(pl.col("regime_stress").drop_nulls().first())
        for d, v in zip(reg["trade_date"].to_list(), reg["regime_stress"].to_list(), strict=True):
            if d in idx and v is not None:
                regime[idx[d]] = v
        state_by = {str(k[0]): v for k, v in st.partition_by("isin", as_dict=True).items()}

    ev_by: dict[str, pl.DataFrame] = {
        str(k[0]): v for k, v in src["events"].partition_by("isin", as_dict=True).items()
    }
    names = dict(zip(src["names"]["isin"].to_list(), src["names"]["name"].to_list(), strict=True))
    fund = _fundamentals(src["facts"], day)
    val_in: dict[str, float] = {}  # listed main-market stocks: last close
    breadth = _Breadth(n_cal)
    taken: set[str] = set()
    last_pos = n_cal - 1

    for (isin,), g in sorted(src["prices"].partition_by("isin", as_dict=True).items()):
        isin = str(isin)
        pos = np.array([idx.get(d, -1) for d in g["trade_date"].to_list()], dtype=np.int64)
        keep = pos >= 0
        if not keep.any():
            continue
        g, pos = g.filter(pl.Series(keep)), pos[keep]
        dates = g["trade_date"].to_list()
        c = g["close"].to_numpy().astype(np.float64)
        f = g["adj_close"].to_numpy().astype(np.float64) / c
        f = np.where(np.isfinite(f) & (f > 0), f, 1.0)
        o = g["open"].fill_null(np.nan).to_numpy().astype(np.float64)
        h = g["high"].fill_null(np.nan).to_numpy().astype(np.float64)
        lo = g["low"].fill_null(np.nan).to_numpy().astype(np.float64)
        adj = c * f
        sseg = seg[pos]
        ind = _indicators(o * f, h * f, lo * f, adj, sseg)
        ret = g["adj_ret"].fill_null(np.nan).to_numpy().astype(np.float64)
        market = g["market"][-1]
        if market == "GPW":
            breadth.add(pos, ret, adj, ind)

        ticker = g["ticker"][-1] or g["short_name"][-1]
        slug = slugify(ticker, isin, taken)
        listed = int(pos[-1]) >= n_cal - LISTED_WITHIN
        name = names.get(isin) or g["short_name"][-1] or ticker

        stt = state_by.get(isin)
        cols = {"s20": np.full(len(pos), np.nan), "beta": np.full(len(pos), np.nan)}
        cols["z"] = np.full(len(pos), np.nan)
        cols["sigma"] = np.full(len(pos), np.nan)
        last_state: dict[str, Any] = {}
        if stt is not None and stt.height:
            row_of = {d: i for i, d in enumerate(dates)}
            for d, s1, s20, b, z in zip(
                stt["trade_date"].to_list(),
                stt["sigma"].to_list(),
                stt["sigma_20"].to_list(),
                stt["beta_wig"].to_list(),
                stt["surprise"].to_list(),
                strict=True,
            ):
                i = row_of.get(d)
                if i is not None:
                    cols["sigma"][i] = np.nan if s1 is None else s1
                    cols["s20"][i] = np.nan if s20 is None else s20
                    cols["beta"][i] = np.nan if b is None else b
                    cols["z"][i] = np.nan if z is None else z
            last_state = stt.sort("trade_date").row(-1, named=True)

        evs = ev_by.get(isin)
        ev_rows: list[list[Any]] = []
        last_event: dict[str, Any] | None = None
        insider_net = 0.0
        if evs is not None:
            for r in evs.iter_rows(named=True):
                p = idx.get(r["event_date"])
                if p is None:
                    continue
                row = int(np.searchsorted(pos, p, side="right")) - 1
                if row < 0:
                    continue
                kind = _event_kind(r)
                title = (r["title"] or "")[:TITLE_CHARS]
                page = r["report_id"].replace(":", "-") if r["report_id"] in pages else None
                ev_rows.append([row, kind, title, page])
                last_event = {"date": r["event_date"], "kind": kind}
                if (
                    r["signal_type"] == "insider_tx"
                    and p > last_pos - INSIDER_WINDOW
                    and r["insider_value"] is not None
                ):
                    sign = {"buy": 1.0, "sell": -1.0}.get(r["insider_side"] or "", 0.0)
                    insider_net += sign * float(r["insider_value"])

        kinds = sorted({e[1] for e in ev_rows})
        tv = g["value_pln"].fill_null(0.0).to_numpy().astype(np.float64)
        rk = risk.profile(ret, cols["sigma"], adj, sseg) if listed else None
        if rk is not None:
            tool.risk[isin] = rk
        fc = forecast.get(isin)
        fu = fund.get(isin)
        tier = last_state.get("liquidity_tier")
        meta = {
            "isin": isin,
            "ticker": ticker,
            "name": name,
            "market": market,
            "slug": slug,
            "first": dates[0],
            "last": dates[-1],
            "listed": listed,
            "tier": tier,
            "logo": logos.get(isin),
        }
        if not meta["logo"]:  # a coloured monogram instead
            meta["hue"] = page_text.monogram(ticker)[1]
        tool.stocks.append(meta)

        payload = {
            "format": FORMAT,
            "isin": isin,
            "ticker": ticker,
            "name": name,
            "market": market,
            "as_of": day.isoformat(),
            "listed": listed,
            "pos": _runs(pos),
            "o": [_price(x) for x in o.tolist()],
            "h": [_price(x) for x in h.tolist()],
            "l": [_price(x) for x in lo.tolist()],
            "c": [_price(x) for x in c.tolist()],
            "adj": _change_points(f),
            "tv": [_thousands(x) for x in g["value_pln"].to_list()],
            "s20": [_r(x, 2) for x in cols["s20"].tolist()],
            "beta": [_r(x, 2) for x in cols["beta"].tolist()],
            "z": [_r(x, 2) for x in cols["z"].tolist()],
            "kinds": kinds,
            "ev": [
                [e[0], kinds.index(e[1]), e[2] if k >= len(ev_rows) - TITLED_EVENTS else "", e[3]]
                for k, e in enumerate(ev_rows)
            ],
            "last": {
                "tier": tier,
                "cost": _r(last_state.get("cost")),
                "median_turnover": _r(last_state.get("median_turnover")),
                "signal_effect": _r(last_state.get("signal_effect")),
                "forecast": None
                if fc is None
                else {k: _r(fc.get(k)) for k in ("expected", "band_lo", "band_hi")}
                | {"rank": fc.get("rank"), "universe": fc.get("universe")},
                "risk": _risk_json(rk),
                "fund": None
                if fu is None
                else {
                    "fiscal_year_end": fu["fiscal_year_end"].isoformat(),
                    "earnings_yield": _r(fu["eps_pln"] / c[-1]) if fu["eps_pln"] else None,
                    "profitability": _r(fu["profitability"]),
                    "asset_growth": _r(fu["asset_growth"]),
                    "accruals": _r(fu["accruals"]),
                },
            },
        }
        (stocks_dir / f"{isin}.json").write_text(
            json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8"
        )

        if not listed:
            continue
        p = int(pos[-1])
        day_ret = ret[-1] if p == last_pos and math.isfinite(ret[-1]) else None
        in_state, _ = setups.states(h * f, lo * f, adj, tv, sseg)
        presets = [k for k, v in setups.PRESET_SETUP.items() if v in in_state and in_state[v][-1]]
        if market == "GPW":
            val_in[isin] = float(c[-1])
        rvol = ta.relative(tv, ta.RVOL_N, sseg)[-1]
        adx = ta.adx(ind["h"], ind["l"], adj, ta.ADX_N, sseg)[0][-1]
        tool.screener.append(
            {
                **meta,
                "close": _price(c[-1]),
                "ret": _r(day_ret),
                **{f"x{k}": _r(_excess(adj, pos, wig, seg, k)) for k in WINDOWS},
                "rsi": _r(ind["rsi"][-1], 3),
                "d50": _r(adj[-1] / ind["sma50"][-1] - 1),
                "d200": _r(adj[-1] / ind["sma200"][-1] - 1),
                "dhi": _r(adj[-1] / ind["hi52"][-1] - 1),
                "dlo": _r(adj[-1] / ind["lo52"][-1] - 1),
                "new_hi": bool(np.isfinite(ind["hi52"][-1]) and ind["h"][-1] >= ind["hi52"][-1]),
                "new_lo": bool(np.isfinite(ind["lo52"][-1]) and ind["l"][-1] <= ind["lo52"][-1]),
                "atr": _r(ind["atr"][-1] / adj[-1]),
                "s20": _r(cols["s20"][-1], 3),
                "beta": _r(cols["beta"][-1], 3),
                "z": _r(cols["z"][-1], 3) if p == last_pos else None,
                "turnover": _r(last_state.get("median_turnover")),
                "cost": _r(last_state.get("cost")),
                "event": last_event,
                "insider_net": _r(insider_net) if insider_net else None,
                "fc": _r(fc.get("expected")) if fc else None,
                "fc_rank": fc.get("rank") if fc else None,
                "ey": _r(fu["eps_pln"] / c[-1]) if fu and fu["eps_pln"] else None,
                "prof": _r(fu["profitability"]) if fu else None,
                "ag": _r(fu["asset_growth"]) if fu else None,
                "acc": _r(fu["accruals"]) if fu else None,
                "presets": presets,
                "rvol": _r(rvol, 3),
                "adx": _r(adx, 3),
                "var95": _r(rk["var95"]) if rk else None,
                "es975": _r(rk["es975"]) if rk else None,
                "dd": _r(rk["drawdown"]) if rk else None,
                "vol_pct": _r(rk["vol_percentile"], 3) if rk else None,
            }
        )

    _add_valuation(tool, val_in, src["facts"], indicators, day)
    tool.market = _market_summary(tool, cal, idx_close, regime)
    tool.market["sectors"] = sectors.rotation(
        sectors.sector_closes(src["sectors"], idx, n_cal), wig, seg
    )
    series = {
        "format": FORMAT,
        "as_of": day.isoformat(),
        "cal": [d.isoformat() for d in cal],
        "seg": _change_points(seg.astype(np.float64)),
        "index": {k: [_r(x, 7) for x in v.tolist()] for k, v in idx_close.items()},
        "regime": [_r(x, 3) for x in regime.tolist()],
        "breadth": {k: v.tolist() for k, v in breadth.counts.items()},
    }
    (staging / "market.json").write_text(
        json.dumps(series, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    (staging / "screener.json").write_text(
        json.dumps(
            {"format": FORMAT, "as_of": day.isoformat(), "rows": tool.screener},
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ),
        encoding="utf-8",
    )
    log.info(
        "site data: %d stocks (%d listed), %.1f MB",
        len(tool.stocks),
        len(tool.screener),
        sum(p.stat().st_size for p in staging.rglob("*.json")) / 1e6,
    )
    return tool


def _market_summary(
    tool: ToolData, cal: list[date], idx_close: dict[str, FloatArray], regime: FloatArray
) -> dict[str, Any]:
    """The market page's figures for the last session."""
    p = len(cal) - 1

    def back(a: FloatArray, k: int) -> float | None:
        q = p - k
        if q < 0 or not (math.isfinite(a[p]) and math.isfinite(a[q])):
            return None
        return float(a[p] / a[q] - 1)

    indices = [
        {
            "name": name,
            "close": _r(idx_close[name][p], 7),
            "r1": back(idx_close[name], 1),
            "r20": back(idx_close[name], 20),
            "r250": back(idx_close[name], 250),
        }
        for name in INDICES
        if name in idx_close
    ]
    rows = [r for r in tool.screener if r["tier"] in ("tradable", "liquid")]
    today = [r for r in rows if r["ret"] is not None]
    up = sorted(today, key=lambda r: -r["ret"])[:TOP]
    down = sorted(today, key=lambda r: r["ret"])[:TOP]
    surprised = sorted([r for r in rows if r["z"] is not None], key=lambda r: -abs(r["z"]))[:TOP]
    gpw = [r for r in tool.screener if r["market"] == "GPW"]
    return {
        "day": cal[p],
        "indices": indices,
        "regime": _r(regime[p], 3) if math.isfinite(regime[p]) else None,
        "regime_20": [_r(x, 3) for x in regime[max(0, p - 59) : p + 1].tolist()],
        "up": up,
        "down": down,
        "surprised": surprised,
        "breadth": {
            "adv": sum(1 for r in gpw if (r["ret"] or 0) > 0),
            "dec": sum(1 for r in gpw if (r["ret"] or 0) < 0),
            "above200": sum(1 for r in gpw if (r["d200"] or -1) > 0),
            "n200": sum(1 for r in gpw if r["d200"] is not None),
            "new_hi": sum(1 for r in gpw if r["new_hi"]),
            "new_lo": sum(1 for r in gpw if r["new_lo"]),
        },
        "listed": len(tool.screener),
    }
