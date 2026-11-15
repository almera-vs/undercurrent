"""Valuation on the stock pages and in the screener (DECISIONS, "Valuation").

Cheap or dear against its sector, what the price implies, and the quality of the last annual
report.

Inputs, all point in time:

* **GPW's indicators** (``indicators.py``): the number of shares, book value, the earnings
  the exchange's P/E implies, the dividend yield and the sector. Market capitalisation is
  those shares times Undercurrent's own close.
* **The last annual report** (ESEF, ``esef.statements``): revenue, operating profit, cash
  flow from operations, capital expenditure, the balance sheet, each with the prior year
  from the same filing. Used while the fiscal year ended at most ``FRESH_DAYS`` ago.

What is computed:

* **Yields**, so a loss is a low value rather than a meaningless negative multiple: earnings
  yield E/P, book-to-price B/P, free-cash-flow yield (operating cash flow minus capital
  expenditure, over market cap), EBIT yield and the dividend yield. No EV multiples: Polish
  filers tag their bank debt with their own extension concepts, so net debt is unknown.
* **Against the sector**: each yield's percentile among listed stocks of the same GPW sector
  (at least ``MIN_PEERS`` with a value), else of its sector group (GPW's broader heading,
  "Finanse" for "Finanse - banki komercyjne"), else among all listed stocks, and says which.
  The *value score* is the mean percentile of E/P, B/P and FCF yield.
* **What the price implies** (reverse DCF): the yearly growth of free cash flow over
  ``DCF_YEARS`` years, then ``TERMINAL_GROWTH`` for ever, that makes its present value equal
  the market cap, at discount rates of 8, 10 and 12 %. Equity-level (cash flow after
  interest where the company reports it so, no debt adjustment); only with positive FCF.
* **Piotroski's F-score** from the report and its comparatives, nine tests, with the
  number that could be judged: return on assets above zero, operating cash flow above zero,
  return on assets up, cash flow above profit, non-current liabilities to assets down (the
  stand-in for long-term debt), current ratio up, no more shares, gross margin up, asset
  turnover up. Year-end assets throughout (the filing reports only one prior year).
* **Altman's Z** (the public-company formula), not for financial companies:
  1.2·working capital + 1.4·retained earnings + 3.3·EBIT + 1.0·revenue, each over total
  assets, + 0.6·market cap over total liabilities. Below 1.81 distress, above 2.99 safe.

None of these are forecasts. ``history_events`` turns three of them into setups the event
study tests like the chart setups (``setups.py``): entering the cheapest fifth of its peers
by value score, reaching an F-score of 7 or more, and falling into Altman's distress zone.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

WARSAW = ZoneInfo("Europe/Warsaw")
FRESH_DAYS = 550  # as pick_features.FUND_STALE_DAYS
MIN_PEERS = 6
DISCOUNT_RATES = (0.08, 0.10, 0.12)
TERMINAL_GROWTH = 0.025
DCF_YEARS = 10
GROWTH_RANGE = (-0.30, 0.60)
VALUE_KEYS = ("ep", "bp", "fcf_yield")
YIELD_KEYS = ("ep", "bp", "fcf_yield", "ebit_yield", "dividend_yield")
CHEAP = 0.8  # value rank at or above: the cheapest fifth of its peers
QUALITY = 7  # F-score at or above, with at least QUALITY_SEEN tests judged
QUALITY_SEEN = 8
DISTRESS = 1.81
SAFE = 2.99
_CLOSE_LOCAL = time(17, 5)


def _ok(*xs: float | None) -> bool:
    return all(x is not None and math.isfinite(x) for x in xs)


def _div(a: float | None, b: float | None) -> float | None:
    if not _ok(a, b) or not b:
        return None
    assert a is not None and b is not None
    return a / b


def is_financial(sector: str | None) -> bool:
    """GPW's financial sectors (banks, insurers, investment), where Altman's Z means nothing."""
    return bool(sector) and str(sector).strip().lower().startswith("finanse")


def shares_from_report(cur: dict[str, float | None]) -> float | None:
    """Shares from the annual report: the weighted average where tagged, else profit ÷ EPS."""
    w = cur.get("shares_weighted")
    if _ok(w) and w and w > 0:
        return float(w)
    s = _div(cur.get("net_profit_parent"), cur.get("eps_basic"))
    return s if s is not None and s > 0 else None


def free_cash_flow(cur: dict[str, float | None]) -> float | None:
    """Operating cash flow minus purchases of fixed and intangible assets.

    Purchases are the fixed-asset line plus the intangible one where the report splits them,
    else the combined line many Polish filers use. None without operating cash flow and one
    of the two.
    """
    cfo, ppe = cur.get("operating_cash_flow"), cur.get("capex_ppe")
    if not _ok(cfo):
        return None
    assert cfo is not None
    if _ok(ppe):
        assert ppe is not None
        intangibles = cur.get("capex_intangibles")
        capex = abs(ppe) + (abs(intangibles) if _ok(intangibles) else 0.0)  # type: ignore[arg-type]
    elif _ok(cur.get("capex_combined")):
        capex = abs(cur["capex_combined"])  # type: ignore[arg-type]
    else:
        return None
    return float(cfo - capex)


def _present_value(fcf0: float, g: float, r: float) -> float:
    pv, f = 0.0, fcf0
    for t in range(1, DCF_YEARS + 1):
        f *= 1 + g
        pv += f / (1 + r) ** t
    return pv + f * (1 + TERMINAL_GROWTH) / (r - TERMINAL_GROWTH) / (1 + r) ** DCF_YEARS


def implied_growth(fcf0: float | None, value: float | None, r: float) -> float | None:
    """Growth of free cash flow the price implies at discount rate ``r`` (bisection).

    None without positive FCF; the range's ends when the price needs more or less than it.
    """
    if not _ok(fcf0, value) or fcf0 is None or value is None or fcf0 <= 0 or value <= 0:
        return None
    lo, hi = GROWTH_RANGE
    if _present_value(fcf0, lo, r) >= value:
        return lo
    if _present_value(fcf0, hi, r) <= value:
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if _present_value(fcf0, mid, r) < value:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def piotroski(cur: dict[str, Any], prev: dict[str, Any]) -> dict[str, Any]:
    """The nine tests, the score and how many tests the report allowed.

    Each test is True, False, or None where a line it needs is not in the report.
    """

    def ratio(d: dict[str, Any], a: str, b: str) -> float | None:
        return _div(d.get(a), d.get(b))

    def up(a: float | None, b: float | None) -> bool | None:
        return None if not _ok(a, b) else bool(a > b)  # type: ignore[operator]

    roa, roa_prev = ratio(cur, "net_profit", "assets"), ratio(prev, "net_profit", "assets")
    cfo, net = cur.get("operating_cash_flow"), cur.get("net_profit")
    lev, lev_prev = (
        ratio(cur, "noncurrent_liabilities", "assets"),
        ratio(prev, "noncurrent_liabilities", "assets"),
    )
    cr, cr_prev = (
        ratio(cur, "current_assets", "current_liabilities"),
        ratio(prev, "current_assets", "current_liabilities"),
    )
    sh, sh_prev = shares_from_report(cur), shares_from_report(prev)
    gm, gm_prev = ratio(cur, "gross_profit", "revenue"), ratio(prev, "gross_profit", "revenue")
    at, at_prev = ratio(cur, "revenue", "assets"), ratio(prev, "revenue", "assets")
    tests: dict[str, bool | None] = {
        "roa_positive": None if roa is None else roa > 0,
        "cfo_positive": None if not _ok(cfo) else cfo > 0,  # type: ignore[operator]
        "roa_up": up(roa, roa_prev),
        "cash_above_profit": None if not _ok(cfo, net) else cfo > net,  # type: ignore[operator]
        "leverage_down": up(lev_prev, lev),
        "liquidity_up": up(cr, cr_prev),
        "no_new_shares": None if not _ok(sh, sh_prev) else sh <= sh_prev * 1.005,  # type: ignore[operator]
        "margin_up": up(gm, gm_prev),
        "turnover_up": up(at, at_prev),
    }
    seen = [v for v in tests.values() if v is not None]
    return {"score": sum(seen), "seen": len(seen), "tests": tests}


def altman(cur: dict[str, Any], market_cap: float | None) -> dict[str, Any] | None:
    """Altman's Z for a listed company (None if any input is missing)."""
    ta, tl = cur.get("assets"), cur.get("liabilities")
    parts = (
        cur.get("current_assets"),
        cur.get("current_liabilities"),
        cur.get("retained_earnings"),
        cur.get("operating_profit"),
        cur.get("revenue"),
    )
    if not _ok(ta, tl, market_cap, *parts) or not ta or not tl:
        return None
    ca, cl, re_, ebit, sales = (float(x) for x in parts)  # type: ignore[arg-type]
    z = (
        1.2 * (ca - cl) / ta
        + 1.4 * re_ / ta
        + 3.3 * ebit / ta
        + 0.6 * float(market_cap) / tl  # type: ignore[arg-type]
        + 1.0 * sales / ta
    )
    zone = "distress" if z < DISTRESS else "safe" if z > SAFE else "grey"
    return {"z": float(z), "zone": zone}


def yields(
    close: float | None,
    gpw: dict[str, Any] | None,
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Market cap and the yields of one stock (GPW first, the annual report as fallback)."""
    cur = report["cur"] if report else {}
    shares = gpw.get("shares") if gpw else None
    shares_src = "gpw" if _ok(shares) and shares else None
    if not shares_src:
        shares = shares_from_report(cur) if report else None
        shares_src = "report" if shares else None
    mcap = float(shares) * close if _ok(shares, close) and shares and close else None
    earnings = None
    if gpw and _ok(gpw.get("pe"), gpw.get("market_value_pln")) and gpw["pe"] > 0:
        earnings = gpw["market_value_pln"] / gpw["pe"]
    elif cur.get("net_profit_parent") is not None:
        earnings = cur["net_profit_parent"]
    book = gpw.get("book_value_pln") if gpw else None
    if not _ok(book):
        book = cur.get("equity_parent")
    fcf = free_cash_flow(cur) if report else None
    return {
        "market_cap": mcap,
        "shares": shares,
        "shares_source": shares_src,
        "ep": _div(earnings, mcap),
        "bp": _div(book, mcap),
        "fcf": fcf,
        "fcf_yield": _div(fcf, mcap),
        "ebit_yield": _div(cur.get("operating_profit"), mcap),
        "dividend_yield": gpw.get("dividend_yield") if gpw else None,
    }


def sector_group(sector: str | None) -> str | None:
    """GPW's broader group of a sector: "Finanse - banki komercyjne" → "Finanse"."""
    return str(sector).split(" - ")[0].strip() if sector else None


def _rank(rows: list[dict[str, Any]], k: str) -> None:
    """``{k}_pct``: each row's percentile of ``k`` among its peers; sets ``peer_group``."""
    allv = np.array([r[k] for r in rows if _ok(r.get(k))], dtype=float)
    pools: dict[tuple[str, Any], list[float]] = {}
    for r in rows:
        if _ok(r.get(k)):
            pools.setdefault(("sector", r.get("sector_code")), []).append(float(r[k]))
            pools.setdefault(("group", sector_group(r.get("sector"))), []).append(float(r[k]))
    for r in rows:
        x = r.get(k)
        if not _ok(x):
            r[f"{k}_pct"] = None
            continue
        peers, group = allv, "market"
        for level, key in (
            ("sector", r.get("sector_code")),
            ("group", sector_group(r.get("sector"))),
        ):
            pool = pools.get((level, key))
            if key is not None and pool is not None and len(pool) >= MIN_PEERS:
                peers, group = np.array(pool, dtype=float), level
                break
        r[f"{k}_pct"] = float((np.sum(peers < x) + 0.5 * np.sum(peers == x)) / len(peers))
        r.setdefault("peer_group", group)


def percentiles(
    rows: list[dict[str, Any]], keys: Iterable[str] = YIELD_KEYS
) -> list[dict[str, Any]]:
    """Each row's percentile of each yield against its peers, and its value rank.

    Peers are the rows of the same GPW sector (``sector_code``) when it has ``MIN_PEERS``
    values, else of the same sector group (``sector_group``), else all rows; ``peer_group``
    says which (``sector``, ``group`` or ``market``, from the first yield ranked).
    ``value_score`` is the mean percentile of ``VALUE_KEYS`` (at least two of them), and
    ``value_score_pct`` its own percentile among the same peers: a mean of percentiles
    bunches towards the middle, so "the cheapest fifth" is read from this rank.
    """
    for k in keys:
        _rank(rows, k)
    for r in rows:
        ps = [float(r[f"{k}_pct"]) for k in VALUE_KEYS if r.get(f"{k}_pct") is not None]
        r["value_score"] = float(np.mean(ps)) if len(ps) >= 2 else None
    _rank(rows, "value_score")
    return rows


def fresh(report: dict[str, Any] | None, day: date) -> bool:
    """Whether an annual report is recent enough to use on ``day``."""
    return bool(report) and (day - report["fiscal_year_end"]).days <= FRESH_DAYS  # type: ignore[index]


def last_report(reports: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """The latest report known before ``day``'s close, however old (None without any)."""
    close = datetime.combine(day, _CLOSE_LOCAL, tzinfo=WARSAW)
    known = [r for r in reports if r["known_at"] < close]
    return known[-1] if known else None


def report_on(reports: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """The report in force on ``day``, None when none is fresh.

    The latest that reached the repository before that session's close; ``reports`` sorted
    by ``known_at``.
    """
    close = datetime.combine(day, _CLOSE_LOCAL, tzinfo=WARSAW)
    best = None
    for r in reports:
        if r["known_at"] < close:
            best = r
        else:
            break
    return best if fresh(best, day) else None


# --------------------------------------------------------------------------- history


def history_events(
    prices: pl.DataFrame,
    statements: list[dict[str, Any]],
    sectors: dict[str, tuple[int | None, str | None]],
    calendar: list[date],
) -> pl.DataFrame:
    """Valuation setups as study events, judged on the first session of every week.

    ``prices``: main-market isin, trade_date, close, known_at, market. Shares come from the
    annual report here (GPW's table has no history), and the sector from GPW's latest table:
    a company rarely changes sector, but that is today's classification applied to the past.
    """
    weeks = [
        d
        for i, d in enumerate(calendar)
        if i == 0 or d.isocalendar()[:2] != calendar[i - 1].isocalendar()[:2]
    ]
    by_isin: dict[str, list[dict[str, Any]]] = {}
    for s in sorted(statements, key=lambda s: s["known_at"]):
        by_isin.setdefault(s["isin"], []).append(s)
    px = prices.filter(pl.col("isin").is_in(list(by_isin)))
    close: dict[tuple[str, date], tuple[float, datetime, str]] = {
        (i, d): (c, k, m)
        for i, d, c, k, m in px.select(
            "isin", "trade_date", "close", "known_at", "market"
        ).iter_rows()
    }
    state_prev: dict[tuple[str, str], bool] = {}
    last_event: dict[tuple[str, str], int] = {}
    rows: list[dict[str, Any]] = []
    for w, day in enumerate(weeks):
        cross = []
        for isin, reps in by_isin.items():
            px_row = close.get((isin, day))
            rep = report_on(reps, day)
            if px_row is None or rep is None or rep["currency"] != "PLN":
                continue
            code, sector = sectors.get(isin, (None, None))
            y = yields(px_row[0], None, rep)
            if y["market_cap"] is None:
                continue
            cross.append(
                {
                    "isin": isin,
                    "sector_code": code,
                    "sector": sector,
                    **y,
                    "_rep": rep,
                    "_px": px_row,
                }
            )
        percentiles(cross, VALUE_KEYS)
        for r in cross:
            f = piotroski(r["_rep"]["cur"], r["_rep"]["prev"])
            z = None if is_financial(r["sector"]) else altman(r["_rep"]["cur"], r["market_cap"])
            states = {
                "cheap_vs_sector": (r["value_score_pct"] or 0.0) >= CHEAP,
                "quality": f["seen"] >= QUALITY_SEEN and f["score"] >= QUALITY,
                "distress": z is not None and z["zone"] == "distress",
            }
            for k, on in states.items():
                was = state_prev.get((r["isin"], k))
                state_prev[(r["isin"], k)] = on
                if not on or was is None or was:
                    continue  # an event when the state begins, judged at least once before
                if w - last_event.get((r["isin"], k), -99) < 4:  # about 20 sessions
                    continue
                last_event[(r["isin"], k)] = w
                rows.append(
                    {
                        "report_id": f"setup:{k}:{r['isin']}:{day:%Y%m%d}",
                        "signal_type": "setup",
                        "subtype": k,
                        "insider_side": None,
                        "isin": r["isin"],
                        "known_at": r["_px"][1],
                        "event_date": day,
                        "market": r["_px"][2],
                    }
                )
    schema: dict[str, Any] = {
        "report_id": pl.String,
        "signal_type": pl.String,
        "subtype": pl.String,
        "insider_side": pl.String,
        "isin": pl.String,
        "known_at": pl.Datetime("us", "UTC"),
        "event_date": pl.Date,
        "market": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def first_session_after(known_at: datetime, calendar: list[date]) -> int:
    """Position of the first session whose close (17:05 Warsaw) is after ``known_at``."""
    local = known_at.astimezone(WARSAW)
    day = local.date() if local.time() < _CLOSE_LOCAL else local.date() + timedelta(days=1)
    return bisect.bisect_left(calendar, day)
