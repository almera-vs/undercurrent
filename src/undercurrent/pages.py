"""What the site says about one report and one company, in words a reader can use.

Pure helpers for ``publish``: the opening of a report and the amounts it names, a
plain-language summary of a signal's history, the stock's own price context (price,
reaction, liquidity, a small chart), and how past digest events have done since entry.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from undercurrent import i18n
from undercurrent.study import LIQ_WINDOW, buyable

# --------------------------------------------------------------------------- report text

LEAD_MIN, LEAD_MAX = 220, 520
MAX_AMOUNTS = 4
# Report extraction turned parentheses into underscores: _"Spółka"_, _słownie: …_.
_UNDERSCORE_PARENS = re.compile(r"(?<![\w_])_([^_\n]{1,240}?)_(?![\w_])")
# Old NewConnect pages lost their typographic quotes: (?Spółka?).
_LOST_QUOTES = re.compile(r"(?<=[\s(])\?([^\W\d_][^?\n]{0,80}?)\?(?=[\s),.;:])")
_SENTENCE_END = re.compile(r"[.!?](?=\s+[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"(])")
# Abbreviations whose dot does not end a sentence.
_ABBREV = re.compile(
    r"(\bS\.A|\bs\.a|\bo\.o|\bsp|\bul|\bnr|\bart|\bust|\bpkt|\btj|\bm\.in|\bzw|\bpoz|\bDz\.U"
    r"|\bdot|\bwg|\bok|\bmln|\bmld|\btys|\bzł|\br|\bim|\bgodz|\bN\.V|\bS\.E|\bSp|\bk|\bp)$",
)
_AMOUNT = re.compile(
    r"(?:(?:PLN|EUR|USD)\s?)?"
    r"\d{1,3}(?:[ \xa0.]\d{3})+(?:,\d+)?(?:\s?(?:mln|mld|tys\.))?\s?(?:zł|PLN|EUR|USD)\b"
    r"|\d+(?:[,.]\d+)?\s?(?:mln|mld|tys\.)\s?(?:zł|PLN|EUR|USD)\b"
    r"|\d+(?:,\d+)?\s?(?:zł|PLN|EUR|USD)\b"
    r"|(?:PLN|EUR|USD)\s?\d+(?:[,.]\d+)?(?:\s?(?:mln|mld))?",
)


def clean_report_text(text: str | None) -> str:
    """Undo the extraction artefacts in a report body; whitespace collapsed."""
    if not text:
        return ""
    t = _UNDERSCORE_PARENS.sub(r"(\1)", text)
    t = _LOST_QUOTES.sub(r"„\1”", t)
    return re.sub(r"[ \t\xa0]+", " ", t).strip()


def report_lead(text: str) -> str:
    """The opening sentences of a cleaned report body: about two or three sentences."""
    if len(text) <= LEAD_MAX:
        return text
    cut = None
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        if _ABBREV.search(text[: end - 1]):
            continue
        if end >= LEAD_MIN:
            cut = end
            break
    if cut is not None and cut <= LEAD_MAX + 200:
        return text[:cut]
    space = text.rfind(" ", 0, LEAD_MAX)
    return text[: space if space > LEAD_MIN else LEAD_MAX].rstrip(" ,;:") + " …"


def money_amounts(text: str) -> list[str]:
    """Money amounts the report names, in order, at most ``MAX_AMOUNTS``, no repeats."""
    out: list[str] = []
    for m in _AMOUNT.finditer(text):
        a = re.sub(r"\s+", " ", m.group(0).replace("\xa0", " ")).strip()
        a = re.sub(r"(\d)(zł|PLN|EUR|USD)", r"\1 \2", a)
        if a not in out:
            out.append(a)
        if len(out) == MAX_AMOUNTS:
            break
    return out


_LEGAL_FORMS = (
    (re.compile(r"\bS\.a\.?(?=\s|$)"), "S.A."),
    (re.compile(r"\bSe\b"), "SE"),
    (re.compile(r"\bPlc\b"), "plc"),
    (re.compile(r"\bN\.v\.?(?=\s|$)"), "N.V."),
    (re.compile(r"\bAsi\b"), "ASI"),
    (re.compile(r"\bSp\. Z O\.o\."), "sp. z o.o."),
    (re.compile(r"\bSpółka Akcyjna\b"), "S.A."),
)


def company_name(name: str | None) -> str:
    """``MILTON ESSEX S.A.`` → ``Milton Essex S.A.``: title case, legal forms kept as written."""
    if not name:
        return ""
    t = name.title() if name.isupper() or name.islower() else name
    for rx, form in _LEGAL_FORMS:
        t = rx.sub(form, t)
    return t


# --------------------------------------------------------------------------- plain words

HORIZON_WORDS: dict[str, dict[int, str]] = {
    "pl": {
        1: "1 sesję (jeden dzień)",
        5: "5 sesji (ok. tydzień)",
        20: "20 sesji (ok. miesiąc)",
        60: "60 sesji (ok. trzy miesiące)",
    },
    "en": {
        1: "1 session (one day)",
        5: "5 sessions (about a week)",
        20: "20 sessions (about a month)",
        60: "60 sessions (about three months)",
    },
}


def horizon_words(h: int, lang: str) -> str:
    """``20`` → ``20 sesji (ok. miesiąc)``."""
    return HORIZON_WORDS[lang].get(h, f"{h} {i18n.plural(lang, h, 'session')}")


def points(x: float, lang: str) -> str:
    """Absolute size of an excess return in percentage points: ``4,7 pkt proc.`` / ``4.7 pp``."""
    v = i18n.group_digits(f"{abs(x) * 100:.1f}", lang)
    return f"{v} pkt proc." if lang == "pl" else f"{v} pp"


def plain_summary(stat: dict[str, Any] | None, h: int, lang: str) -> str:
    """What a signal type's history says, in one or two sentences without jargon."""
    if not stat or stat.get("mean_car") is None:
        n = (stat or {}).get("n") or 0
        if lang == "pl":
            return (
                f"Za mało wcześniejszych zdarzeń tego typu ({n}), by cokolwiek o nich powiedzieć."
            )
        return f"Too few past events of this type ({n}) to say anything about them."
    m = float(stat["mean_car"])
    hit = stat.get("hit_rate")
    worse = m < 0
    share = None if hit is None else round(((1 - hit) if worse else hit) * 100)
    period = horizon_words(h, lang)
    if lang == "pl":
        s = (
            f"Po takich raportach kurs przez {period} wypadał średnio o {points(m, lang)} "
            f"{'gorzej' if worse else 'lepiej'} niż WIG."
        )
        if share is not None:
            n = i18n.num(stat["n"], lang)
            s += f" {'Gorzej' if worse else 'Lepiej'} niż WIG: {share}% z {n} przypadków."
        return s
    s = (
        f"After reports like this the stock did {points(m, lang)} "
        f"{'worse' if worse else 'better'} than WIG on average over {period}."
    )
    if share is not None:
        n = i18n.num(stat["n"], lang)
        s += f" It did {'worse' if worse else 'better'} than WIG in {share}% of {n} cases."
    return s


# --------------------------------------------------------------------------- the company


@dataclass
class PriceHistory:
    """One stock's daily rows, oldest first (dates, raw close, adjusted close, returns...)."""

    dates: list[date]
    close: list[float]
    adj_close: list[float]
    ret: list[float | None]
    turnover: list[float]

    def before(self, d: date) -> int:
        """Number of rows strictly before ``d``."""
        import bisect

        return bisect.bisect_left(self.dates, d)


@dataclass
class CompanyContext:
    """The stock around one report: price, reaction, liquidity and how it has done since."""

    last_close: float | None = None
    last_date: date | None = None
    day0_ret: float | None = None
    day0_wig: float | None = None
    median_close: float | None = None
    median_turnover: float | None = None
    buyable: bool | None = None
    since_entry: float | None = None
    since_sessions: int = 0
    spark: dict[str, str] = field(default_factory=dict)  # per language
    notes: list[str] = field(default_factory=list)
    # from the market state (market_state.py) and the forecast (pick.py), when they exist
    tier: str | None = None  # thin / tradable / liquid
    surprise: float | None = None  # day 0's residual return in its forecast σ
    sigma_20: float | None = None  # forecast σ of the next 20 sessions' return
    cost: float | None = None  # round-trip cost
    forecast: dict[str, Any] | None = None  # this stock's row of pick_scores


def liquidity(hist: PriceHistory, before: date) -> tuple[float | None, float | None, bool | None]:
    """Median close, median daily turnover and whether the study would count the stock.

    Measured over the sessions before ``before``, with the study's own window and rule.
    With too few rows all three are unknown (None).
    """
    i = hist.before(before)
    lo = max(0, i - LIQ_WINDOW)
    if i - lo < 5:
        return None, None, None
    closes = np.array(hist.close[lo:i], dtype=np.float64)
    turn = np.array(hist.turnover[lo:i], dtype=np.float64)
    return float(np.median(closes)), float(np.median(turn)), buyable(closes, turn)


def realised_excess(
    hist: PriceHistory,
    wig: dict[date, float],
    sessions: Sequence[date],
    entry: date,
    horizon: int,
) -> tuple[float | None, int]:
    """Stock minus WIG, compounded, from the entry close over up to ``horizon`` sessions.

    Only sessions with data so far count; (None, 0) before the first one. None as soon as a
    day's return is missing (a gap in the stock's trading).
    """
    import bisect

    k = bisect.bisect_right(list(sessions), entry)
    window = list(sessions[k : k + horizon])
    by_date = dict(zip(hist.dates, hist.ret, strict=True))
    r_s, r_m = 1.0, 1.0
    n = 0
    for d in window:
        if d not in wig:
            break
        r = by_date.get(d)
        if r is None or not math.isfinite(r):
            return None, n
        r_s *= 1 + r
        r_m *= 1 + wig[d]
        n += 1
    return (r_s - r_m, n) if n else (None, 0)


def sparkline_svg(
    hist: PriceHistory, until: date, mark: date | None, lang: str, sessions: int = 90
) -> str:
    """The stock's adjusted close over the last ``sessions`` rows up to ``until``.

    ``mark`` (the report's day 0) gets a vertical line. Empty string with too little data.
    """
    i = hist.before(until) + (1 if until in hist.dates else 0)
    lo = max(0, i - sessions)
    pts = hist.adj_close[lo:i]
    ds = hist.dates[lo:i]
    if len(pts) < 5:
        return ""
    w, h, pad = 600, 140, 8
    vmin, vmax = min(pts), max(pts)
    span = (vmax - vmin) or vmax or 1.0

    def x(j: int) -> float:
        return pad + j / (len(pts) - 1) * (w - 2 * pad)

    def y(v: float) -> float:
        return pad + (vmax - v) / span * (h - 2 * pad)

    line = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in enumerate(pts))
    tone = "up" if pts[-1] >= pts[0] else "down"
    label = "Kurs akcji, ostatnie sesje" if lang == "pl" else "Share price, recent sessions"
    parts = [
        f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="{label}">',
        f'<polyline class="line line-{tone}" points="{line}"/>',
    ]
    if mark is not None and ds and ds[0] <= mark <= ds[-1]:
        j = next(k for k, d in enumerate(ds) if d >= mark)
        parts.append(f'<line class="entry" x1="{x(j):.1f}" x2="{x(j):.1f}" y1="0" y2="{h}"/>')
    parts.append("</svg>")
    return "".join(parts)


def lines_svg(
    series: list[tuple[str, str, list[float]]],
    dates: list[date],
    lang: str,
    label: str,
    pct_axis: bool = True,
) -> str:
    """Several lines over the same dates, e.g. a stock vs WIG, or a backtest vs baselines.

    ``series`` is a list of (css class, legend text, values); values are cumulative returns
    (0.05 = +5 %). The legend is drawn under the chart with the last value of each line.
    """
    pts = [(cls, name, [v for v in vals]) for cls, name, vals in series if vals]
    if not pts or len(dates) < 2:
        return ""
    w, h, pad_l, pad_r, pad_t, pad_b = 900, 300, 80, 20, 16, 40
    allv = [v for _, _, vals in pts for v in vals if math.isfinite(v)] + [0.0]
    lo, hi = min(allv), max(allv)
    span = (hi - lo) or 0.01
    lo, hi = lo - span * 0.06, hi + span * 0.06
    n = len(dates)

    def x(j: int) -> float:
        return pad_l + j / (n - 1) * (w - pad_l - pad_r)

    def y(v: float) -> float:
        return pad_t + (hi - v) / (hi - lo) * (h - pad_t - pad_b)

    step = 10 ** math.floor(math.log10(span / 3)) if span > 0 else 0.01
    if span / step > 8:
        step *= 2.5 if span / step < 20 else 5
    ticks = []
    t = math.floor(lo / step) * step
    while t <= hi:
        if t >= lo:
            ticks.append(t)
        t += step
    parts = [f'<svg class="chart lines" viewBox="0 0 {w} {h}" role="img" aria-label="{label}">']
    for v in ticks:
        tick = i18n.pct(v, lang, digits=0) if pct_axis else f"{v:.2f}"
        parts.append(
            f'<line class="grid" x1="{pad_l}" x2="{w - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y(v) + 5:.1f}" text-anchor="end">{tick}</text>'
        )
    parts.append(
        f'<line class="zero" x1="{pad_l}" x2="{w - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
    )
    for cls, _, vals in pts:
        line = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in enumerate(vals) if math.isfinite(v))
        parts.append(f'<polyline class="line {cls}" points="{line}"/>')
    for j in sorted({0, n // 2, n - 1}):
        anchor = "start" if j == 0 else "end" if j == n - 1 else "middle"
        parts.append(
            f'<text class="tick" x="{x(j):.1f}" y="{h - 12}" text-anchor="{anchor}">'
            f"{dates[j]:%Y-%m}</text>"
        )
    parts.append("</svg>")
    legend = "".join(
        f'<span class="lg-item"><span class="sw {cls}"></span>{name} '
        f'<b class="mono">{i18n.pct(vals[-1], lang)}</b></span>'
        for cls, name, vals in pts
        if vals and math.isfinite(vals[-1])
    )
    return "".join(parts) + f'<p class="legend small">{legend}</p>'


# How each pick feature reads on the page: (kind of value, which direction is "good").
# kinds: logret (log excess return → %), vol (daily → annualised %), pct, num, count, pln
FEATURE_KINDS: dict[str, str] = {
    "mom_12_1": "logret",
    "mom_6_1": "logret",
    "rev_1m": "logret",
    "off_high": "logret",
    "vol_60": "vol",
    "beta": "num",
    "resid_vol": "vol",
    "drawdown": "logdd",
    "turnover": "pln",
    "no_trade": "pct",
    "illiquidity": "num",
    "insider_net": "num",
    "corp_actions": "count",
    "garch_vol": "pct",
    "kalman_beta": "num",
    "bond_beta": "num",
    "resid_mom": "logret",
    "surprise_20": "num",
    "signal_effect": "logret",
}


def feature_value(feature: str, v: float | None, lang: str) -> str:
    """A raw feature value in the units a reader knows."""
    if v is None or not math.isfinite(v):
        return "—"
    kind = FEATURE_KINDS.get(feature, "count")
    if kind == "logret":
        return i18n.pct(math.expm1(v), lang)
    if kind == "logdd":  # size of the fall, shown without a sign: "a 21.4% fall"
        return i18n.pct(1 - math.exp(-v), lang, sign=False)
    if kind == "vol":
        return i18n.pct(v * math.sqrt(252), lang, sign=False)
    if kind == "pct":
        return i18n.pct(v, lang, sign=False)
    if kind == "pln":
        return i18n.short_pln(math.expm1(v), lang) + (" zł" if lang == "pl" else " PLN")
    if kind == "num":
        return i18n.group_digits(f"{v:.2f}", lang)
    return i18n.num(v, lang)


def spotlight_svg(
    dates: list[date],
    stock: list[float],
    wig: list[float],
    marks: list[tuple[date, str]],
    ticker: str,
    lang: str,
) -> str:
    """The stock's year against WIG: a filled line with dots on the days it reported news.

    ``stock`` and ``wig`` are cumulative returns (0.05 = +5 %). Each mark is (day, label);
    its dot carries the label as a tooltip.
    """
    n = len(dates)
    if n < 5:
        return ""
    w, h, pad_l, pad_r, pad_t, pad_b = 960, 360, 100, 150, 24, 36
    allv = [*stock, *wig, 0.0]
    lo, hi = min(allv), max(allv)
    span = (hi - lo) or 0.01
    lo, hi = lo - span * 0.08, hi + span * 0.1

    def x(j: float) -> float:
        return pad_l + j / (n - 1) * (w - pad_l - pad_r)

    def y(v: float) -> float:
        return pad_t + (hi - v) / (hi - lo) * (h - pad_t - pad_b)

    step = 10 ** math.floor(math.log10(span / 3)) if span > 0 else 0.01
    if span / step > 8:
        step *= 2.5 if span / step < 20 else 5
    ticks: list[float] = []
    t = math.floor(lo / step) * step
    while t <= hi:
        if t >= lo:
            ticks.append(t)
        t += step
    tone = "up" if stock[-1] >= wig[-1] else "down"
    line = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in enumerate(stock))
    area = f"{x(0):.1f},{y(lo):.1f} {line} {x(n - 1):.1f},{y(lo):.1f}"
    wig_line = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in enumerate(wig))
    label = (
        f"{ticker}: kurs wobec WIG w ostatnim roku"
        if lang == "pl"
        else f"{ticker}: share price against WIG over the last year"
    )
    parts = [
        f'<svg class="spot-chart spot-{tone}" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="{label}">',
        "<defs>"
        f'<linearGradient id="spot-fill-{tone}" x1="0" x2="0" y1="0" y2="1">'
        '<stop offset="0" class="stop-a"/><stop offset="1" class="stop-b"/></linearGradient>'
        "</defs>",
    ]
    for v in ticks:
        parts.append(
            f'<line class="grid" x1="{pad_l}" x2="{w - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{pad_l - 10}" y="{y(v) + 5:.1f}" text-anchor="end">'
            f"{i18n.pct(v, lang, digits=0)}</text>"
        )
    parts += [
        f'<line class="zero" x1="{pad_l}" x2="{w - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>',
        f'<polygon class="area" fill="url(#spot-fill-{tone})" points="{area}"/>',
        f'<polyline class="wig" points="{wig_line}"/>',
        f'<polyline class="stock draw" pathLength="1" points="{line}"/>',
    ]
    pos = {d: j for j, d in enumerate(dates)}
    for d, text in marks:
        j = pos.get(d)
        if j is None:  # a report on a day without a session: the next session
            j = next((k for k, s in enumerate(dates) if s > d), None)
        if j is None:
            continue
        parts.append(
            f'<circle class="mark" cx="{x(j):.1f}" cy="{y(stock[j]):.1f}" r="6">'
            f"<title>{d:%d.%m.%Y}: {html_escape(text)}</title></circle>"
        )
    parts += [
        f'<circle class="end" cx="{x(n - 1):.1f}" cy="{y(stock[-1]):.1f}" r="7"/>',
        f'<text class="end-lbl stock-lbl" x="{x(n - 1) + 14:.1f}" y="{y(stock[-1]) + 6:.1f}">'
        f"{i18n.pct(stock[-1], lang)}</text>",
        f'<text class="end-lbl wig-lbl" x="{x(n - 1) + 14:.1f}" y="{y(wig[-1]) + 6:.1f}">'
        f"WIG {i18n.pct(wig[-1], lang)}</text>",
    ]
    for j in sorted({0, n // 2, n - 1}):
        anchor = "start" if j == 0 else "end" if j == n - 1 else "middle"
        parts.append(
            f'<text class="tick" x="{x(j):.1f}" y="{h - 10}" text-anchor="{anchor}">'
            f"{dates[j]:%m.%Y}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def spark_svg(values: list[float]) -> str:
    """A tiny line of a stock's recent path, coloured by its direction."""
    if len(values) < 3:
        return ""
    w, h = 120, 34
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = " ".join(
        f"{2 + j / (len(values) - 1) * (w - 4):.1f},{2 + (hi - v) / span * (h - 4):.1f}"
        for j, v in enumerate(values)
    )
    tone = "up" if values[-1] >= values[0] else "down"
    return (
        f'<svg class="spark-mini {tone}" viewBox="0 0 {w} {h}" aria-hidden="true">'
        f'<polyline points="{pts}"/></svg>'
    )


def ring_svg(rank: int | None, universe: int, lang: str) -> str:
    """The pick's place in the ranking as a ring: rank 1 of N fills it."""
    if not rank or not universe:
        return ""
    share = 1 - (rank - 1) / universe
    r, c = 52, 2 * math.pi * 52
    of = "z" if lang == "pl" else "of"
    return (
        '<svg class="ring" viewBox="0 0 140 140" role="img" '
        f'aria-label="{rank} {of} {universe}">'
        f'<circle class="ring-bg" cx="70" cy="70" r="{r}"/>'
        f'<circle class="ring-fg" cx="70" cy="70" r="{r}" '
        f'stroke-dasharray="{c * share:.1f} {c:.1f}" transform="rotate(-90 70 70)"/>'
        f'<text class="ring-big" x="70" y="72" text-anchor="middle">#{rank}</text>'
        f'<text class="ring-small" x="70" y="96" text-anchor="middle">{of} {universe}</text>'
        "</svg>"
    )


def monogram(ticker: str | None) -> tuple[str, int]:
    """Two letters and a hue for a stock's badge, stable for the same ticker."""
    import hashlib

    t = (ticker or "?").upper()
    letters = "".join(ch for ch in t if ch.isalnum())[:2] or "?"
    hue = int(hashlib.sha256(t.encode()).hexdigest()[:4], 16) % 360
    return letters, hue


def html_escape(text: str) -> str:
    """Escape text for an SVG element."""
    import html

    return html.escape(text, quote=True)
