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
