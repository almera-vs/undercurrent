"""Sector rotation on the market page: the GPW sector indices against WIG.

The exchange's index file carries the WIG sector sub-indices (``WIG-BANKI``, ``WIG-ENERG``,
…), so no stock needs a sector of its own. For each sector, at the last session:

* its return against WIG over 5, 20, 60 and 250 sessions (compounded, both ends in one
  segment of the calendar, as everywhere else);
* a **relative rotation** point. ``rs`` is the sector over WIG; *trend* is
  ``100 · rs / SMA50(rs)`` (above 100: the sector has been beating WIG lately) and
  *momentum* is ``100 · trend / trend ten sessions earlier`` (above 100: that is improving).
  The four quadrants read leading (both above), weakening, lagging and improving, and a
  sector usually turns through them clockwise. The tail shows the last four weeks.

This describes; it does not forecast. Nothing here has been tested as a signal.
"""

from __future__ import annotations

import html
import math
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

from undercurrent import ta

FloatArray = npt.NDArray[np.float64]

SECTORS = (
    "BANKI", "BUDOW", "CHEMIA", "DEWEL", "ENERG", "GORNIC", "GRY", "INFO", "LEKI",
    "MEDIA", "MOTO", "NRCHOM", "ODZIEZ", "PALIWA", "SPOZYW", "SUROWC", "TELKOM",
)  # fmt: skip
LABELS: dict[str, dict[str, str]] = {
    "en": {
        "BANKI": "Banks", "BUDOW": "Construction", "CHEMIA": "Chemicals", "DEWEL": "Developers",
        "ENERG": "Energy", "GORNIC": "Mining", "GRY": "Games", "INFO": "IT", "LEKI": "Pharma",
        "MEDIA": "Media", "MOTO": "Automotive", "NRCHOM": "Real estate", "ODZIEZ": "Clothing",
        "PALIWA": "Fuels", "SPOZYW": "Food", "SUROWC": "Commodities", "TELKOM": "Telecoms",
    },
    "pl": {
        "BANKI": "Banki", "BUDOW": "Budownictwo", "CHEMIA": "Chemia", "DEWEL": "Deweloperzy",
        "ENERG": "Energetyka", "GORNIC": "Górnictwo", "GRY": "Gry", "INFO": "Informatyka",
        "LEKI": "Leki", "MEDIA": "Media", "MOTO": "Motoryzacja", "NRCHOM": "Nieruchomości",
        "ODZIEZ": "Odzież", "PALIWA": "Paliwa", "SPOZYW": "Spożywczy", "SUROWC": "Surowce",
        "TELKOM": "Telekomunikacja",
    },
}  # fmt: skip
WINDOWS = (5, 20, 60, 250)
TREND_SESSIONS = 50
MOMENTUM_SESSIONS = 10
TAIL_WEEKS = 4
QUADRANTS = ("leading", "weakening", "lagging", "improving")


def sector_closes(indices: pl.DataFrame, idx: dict[Any, int], n: int) -> dict[str, FloatArray]:
    """Each sector's close on the calendar positions (``indices``: name, trade_date, close)."""
    out: dict[str, FloatArray] = {}
    df = indices.with_columns(pl.col("name").str.to_uppercase())
    for key in SECTORS:
        g = df.filter(pl.col("name") == f"WIG-{key}")
        if not g.height:
            continue
        a = np.full(n, np.nan)
        for d, v in zip(g["trade_date"].to_list(), g["close"].to_list(), strict=True):
            if d in idx and v is not None and v > 0:
                a[idx[d]] = v
        out[key] = a
    return out


def _back(a: FloatArray, p: int, k: int, seg: npt.NDArray[np.int64]) -> float | None:
    q = p - k
    if q < 0 or seg[q] != seg[p] or not (math.isfinite(a[p]) and math.isfinite(a[q])):
        return None
    return float(a[p] / a[q] - 1)


def rotation(
    closes: dict[str, FloatArray], wig: FloatArray, seg: npt.NDArray[np.int64]
) -> list[dict[str, Any]]:
    """Every sector with a close on the last session: returns against WIG and its rotation."""
    p = len(wig) - 1
    out: list[dict[str, Any]] = []
    for key, a in closes.items():
        if p < 0 or not math.isfinite(a[p]):
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            rs = a / wig
        ok = np.isfinite(rs)
        # the trend needs a full 50-session window of rs; fill gaps cannot be invented
        rs_f = np.where(ok, rs, np.nan)
        sma = ta.sma(np.nan_to_num(rs_f, nan=0.0), TREND_SESSIONS, seg)
        sma = np.where(_window_ok(ok, TREND_SESSIONS, seg), sma, np.nan)
        trend = 100 * rs_f / sma
        prev = np.full(len(trend), np.nan)
        k = MOMENTUM_SESSIONS
        prev[k:] = np.where(seg[k:] == seg[:-k], trend[:-k], np.nan)
        momentum = 100 * trend / prev
        tail = []
        for w in range(TAIL_WEEKS, -1, -1):
            t = p - 5 * w
            if (
                t >= 0
                and seg[t] == seg[p]
                and math.isfinite(trend[t])
                and math.isfinite(momentum[t])
            ):
                tail.append([float(trend[t]), float(momentum[t])])
        row: dict[str, Any] = {
            "key": key,
            "close": float(a[p]),
            "r1": _back(a, p, 1, seg),
            **{
                f"x{k}": (
                    None
                    if (s := _back(a, p, k, seg)) is None or (m := _back(wig, p, k, seg)) is None
                    else s - m
                )
                for k in WINDOWS
            },
            "trend": float(trend[p]) if math.isfinite(trend[p]) else None,
            "momentum": float(momentum[p]) if math.isfinite(momentum[p]) else None,
            "tail": tail,
        }
        row["quadrant"] = quadrant(row["trend"], row["momentum"])
        out.append(row)
    return sorted(out, key=lambda r: -(r["x20"] if r["x20"] is not None else -9))


def _window_ok(ok: npt.NDArray[np.bool_], n: int, seg: npt.NDArray[np.int64]) -> FloatArray:
    """Whether the last ``n`` sessions all have a value, inside one segment."""
    cnt = ta.sma(ok.astype(np.float64), n, seg)
    return np.asarray(cnt == 1.0, dtype=np.bool_)


def quadrant(trend: float | None, momentum: float | None) -> str | None:
    """Leading, weakening, lagging or improving (None without both)."""
    if trend is None or momentum is None:
        return None
    if trend >= 100:
        return "leading" if momentum >= 100 else "weakening"
    return "lagging" if momentum < 100 else "improving"


def rrg_svg(rows: list[dict[str, Any]], lang: str, titles: dict[str, str]) -> str:
    """The rotation chart: trend across, momentum up, 100/100 in the middle, with tails.

    ``titles``: the four quadrants' names and the two axes' (``leading`` … ``trend``,
    ``momentum``), already in ``lang``.
    """
    pts = [(float(x), float(y)) for r in rows for x, y in r["tail"]]
    if not pts:
        return ""
    w, h, pad = 640, 440, 36
    span_x = float(max(max(abs(x - 100) for x, _ in pts), 1.0) * 1.15)
    span_y = float(max(max(abs(y - 100) for _, y in pts), 0.5) * 1.15)

    def sx(x: float) -> float:
        return pad + (x - (100 - span_x)) / (2 * span_x) * (w - 2 * pad)

    def sy(y: float) -> float:
        return h - pad - (y - (100 - span_y)) / (2 * span_y) * (h - 2 * pad)

    cx, cy = sx(100), sy(100)
    e = html.escape
    parts = [
        f'<svg class="rrg" viewBox="0 0 {w} {h}" role="img" aria-label="{e(titles["chart"])}">',
        f'<rect class="q-leading" x="{cx:.1f}" y="{pad}" width="{w - pad - cx:.1f}" '
        f'height="{cy - pad:.1f}"/>',
        f'<rect class="q-weakening" x="{cx:.1f}" y="{cy:.1f}" width="{w - pad - cx:.1f}" '
        f'height="{h - pad - cy:.1f}"/>',
        f'<rect class="q-lagging" x="{pad}" y="{cy:.1f}" width="{cx - pad:.1f}" '
        f'height="{h - pad - cy:.1f}"/>',
        f'<rect class="q-improving" x="{pad}" y="{pad}" width="{cx - pad:.1f}" '
        f'height="{cy - pad:.1f}"/>',
        f'<line class="axis" x1="{pad}" y1="{cy:.1f}" x2="{w - pad}" y2="{cy:.1f}"/>',
        f'<line class="axis" x1="{cx:.1f}" y1="{pad}" x2="{cx:.1f}" y2="{h - pad}"/>',
        f'<text class="qt" x="{w - pad - 6}" y="{pad + 16}" text-anchor="end">'
        f"{e(titles['leading'])}</text>",
        f'<text class="qt" x="{w - pad - 6}" y="{h - pad - 8}" text-anchor="end">'
        f"{e(titles['weakening'])}</text>",
        f'<text class="qt" x="{pad + 6}" y="{h - pad - 8}">{e(titles["lagging"])}</text>',
        f'<text class="qt" x="{pad + 6}" y="{pad + 16}">{e(titles["improving"])}</text>',
        f'<text class="ax" x="{w - pad}" y="{h - 10}" text-anchor="end">'
        f"{e(titles['trend'])} →</text>",
        f'<text class="ax" x="12" y="{pad - 12}">↑ {e(titles["momentum"])}</text>',
    ]
    tails, dots, labels = [], [], []
    placed: list[tuple[float, float, float, float]] = []  # label boxes already drawn
    drawn = [r for r in rows if r["tail"]]
    for r in sorted(drawn, key=lambda r: -r["tail"][-1][1]):  # top of the chart first
        line = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in r["tail"])
        q = r["quadrant"] or "none"
        x, y = r["tail"][-1]
        label = LABELS[lang].get(r["key"], r["key"])
        tails.append(f'<polyline class="tail tail-{q}" points="{line}"/>')
        dots.append(
            f'<circle class="dot-{q}" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="5">'
            f"<title>{e(label)}: {x:.1f} / {y:.1f}</title></circle>"
        )
        # a label that would overlap one already placed moves down a line at a time
        lx, ly, lw = sx(x) + 8, sy(y) + 4, 7.0 * len(label)
        if lx + lw > w - 4:  # no room on the right: put it on the left of its dot
            lx = sx(x) - 8 - lw
        for _ in range(6):
            if not any(
                lx < bx + bw and bx < lx + lw and abs(ly - by) < 15 for bx, by, bw, _h in placed
            ):
                break
            ly += 15
        placed.append((lx, ly, lw, 15))
        labels.append(f'<text class="lbl" x="{lx:.1f}" y="{ly:.1f}">{e(label)}</text>')
    parts += tails + dots + labels  # labels last, so no tail is drawn over them
    parts.append("</svg>")
    return "".join(parts)
