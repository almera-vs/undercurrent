"""Corporate calendar: dividends, general meetings and periodic-report dates, from the reports.

Everything comes from current reports already in the lake, read by plain rules:

* **Dividends.** The exchange's structured dividend form ("Decyzja o wypłacie dywidendy"),
  board recommendations and the general meeting's resolution all state the amount per
  share, the record date (*dzień dywidendy*) and the payment date in the text. Each is read
  with its stage: ``proposed`` (board or supervisory-board recommendation, a draft
  resolution), ``resolved`` (the meeting's resolution, or the exchange form) or
  ``interim`` (an advance on the year's dividend). A subsidiary paying its parent is not
  the issuer's dividend and is skipped. The ex-date is the session before the record date
  (T+2 settlement), from the exchange calendar.
* **General meetings.** Notices convening an ordinary (ZWZ) or extraordinary (NWZ) meeting,
  and notices cancelling one.
* **Periodic-report dates.** Every January issuers publish when their quarterly,
  half-year and annual reports will come out; later reports move single dates.

A field the text does not state stays ``None``; nothing is guessed. Per issuer and event
the latest report wins, so a resolution replaces the recommendation before it.

The extracted dividends are checked against the exchange's own numbers: on the ex-date
GPW and NewConnect lower the reference price by the dividend, so ``prev_close −
reference`` recovers the amount the market adjusted for (:func:`check_against_exchange`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from undercurrent import gpw_calendar
from undercurrent.classify import _COMPANY_DOTS, normalize
from undercurrent.parse import PL_MONTHS, parse_pl_number

# --------------------------------------------------------------------------- text helpers

# "(słownie: dwadzieścia dwa grosze)" and "_słownie: …_" sit between an amount and its unit.
_SPELLED = re.compile(r"[(_\[]\s*slownie\s*:?[^)_\]]{0,200}[)_\]]")
# No word boundary after the year: "28 czerwca 2017r." is common.
_DATE_ANY = re.compile(
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)|\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)"
    r"|\b(\d{1,2})\s+([a-z]+)\s+(\d{4})(?!\d)"
)


def clean(text: str | None) -> str:
    """Normalised text with spelled-out amounts removed."""
    t = _SPELLED.sub(" ", normalize(text))
    t = re.sub(r"\b\d{1,2}/100\b", " ", t)  # "0,55 zł 55/100 złotych"
    # "Kino Polska TV S.A. podjęło": a company form's dots must not end the sentence
    t = _COMPANY_DOTS.sub(lambda m: m.group(0).replace(".", ""), t)
    t = re.sub(r"(\d), (\d{2})\b", r"\1,\2", t)  # "33, 00 zł"
    # one form of "dzień" for the rules: "dniem dywidendy", "dnia dywidendy", "w dniu …"
    t = re.sub(r"\b(dnia|dniu|dniem)\b", "dzien", t)
    # underscores stand in for brackets in the exchange's text ("_dzień dywidendy_"); they
    # become quotes, so a label in brackets still reads as one
    return re.sub(r"\s+", " ", t.replace("_", ' " '))


def dates_in(text: str) -> list[tuple[int, int, date]]:
    """Every date in normalised ``text`` as ``(start, end, date)``, in order."""
    out: list[tuple[int, int, date]] = []
    for m in _DATE_ANY.finditer(text):
        g = m.groups()
        try:
            if g[0]:
                d = date(int(g[0]), int(g[1]), int(g[2]))
            elif g[3]:
                d = date(int(g[5]), int(g[4]), int(g[3]))
            else:
                month = PL_MONTHS.get(g[7])
                if month is None:
                    continue
                d = date(int(g[8]), month, int(g[6]))
        except ValueError:
            continue
        out.append((m.start(), m.end(), d))
    return out


# A date labelled by what follows it: "8 lipca 2026 r. jako dzień dywidendy",
# "w dniu 31 lipca 2015 roku ("dzień dywidendy")". The label needs a bracket, a quote or
# "jako": "… 28.08.2018 r. Termin wypłaty …" starts a new sentence, not a label.
_LABEL_AFTER_DATE = re.compile(
    r"\s*(r\.?|roku)?\s*,?\s*(?:[(\"„”]+[\s(\"„”]*"
    r"|((ustalono|ustala sie|wyznaczono|okreslono) )?(jako|tj\.?)\s+[(\"„”]*\s*)"
)
# A record or payment date is near the report: from two months before it (a report on a
# dividend already paid) to well over a year after (a recommendation in spring for autumn).
_DATE_RANGE = (timedelta(days=-60), timedelta(days=400))


def _date_near(
    text: str,
    anchor: re.Pattern[str],
    after: int = 70,
    before: int = 45,
    published: date | None = None,
) -> date | None:
    """The date ``anchor`` labels.

    In order of confidence, over every place the anchor occurs: a date written right before
    it as its label ("8 lipca jako dzień dywidendy"), else the next date after it, else a
    date shortly before it. A weak early mention ("… i terminu jej wypłaty") then cannot
    win over a clear later one. With ``published``, dates far from it are not candidates:
    "zysk za okres od 1 stycznia 2015" is not a record date.
    """
    found = dates_in(text)
    if published is not None:
        lo, hi = published + _DATE_RANGE[0], published + _DATE_RANGE[1]
        found = [x for x in found if lo <= x[2] <= hi]
    hits = list(anchor.finditer(text))
    for m in hits:
        labelled = [
            d
            for _, e, d in found
            if e <= m.start() and _LABEL_AFTER_DATE.fullmatch(text[e : m.start()])
        ]
        if labelled:
            return labelled[-1]
    for m in hits:
        nxt = [d for s, _, d in found if m.end() <= s <= m.end() + after]
        if nxt:
            return nxt[0]
    for m in hits:
        prev = [d for _, e, d in found if m.start() - before <= e <= m.start()]
        if prev:
            return prev[-1]
    return None


# --------------------------------------------------------------------------- dividends

DIVIDEND_STAGES = ("proposed", "resolved", "interim", "none")

_DIV_TITLE = re.compile(r"dywidend|podzial\w* zysku|przeznaczeni\w* zysku")
_DIV_BODY = re.compile(r"dzien dywidendy|praw\w* do dywidendy|wyplat\w* dywidendy")
_FORM_TITLE = re.compile(r"decyzja o wyplacie dywidendy")
_SUBSIDIARY = re.compile(r"spolk\w* zalezn|spolk\w* corki|od spolki zaleznej|zalezn\w+ od emitenta")
_INTERIM = re.compile(r"zaliczk\w* (na poczet|na dywidend)|zaliczkow\w+ dywidend")
_NO_DIV = re.compile(
    r"(nie|bez) (wyplac\w*|wyplaty|przeznacz\w*)[^.]{0,40}dywidend|niewyplaceni\w* dywidendy"
    r"|brak\w* (wyplaty )?dywidendy|zawieszeni\w* (wyplaty|polityki) dywidend"
    r"|nieprzeznaczeni\w* zysku na dywidend|pokryci\w* straty"
)
_RESOLVED = re.compile(
    r"walne\w* zgromadzeni\w* [^.]{0,80}(podjel\w*|uchwalil\w*|postanowil\w*|zdecydowal\w*)"
    r"|uchwal\w* (nr \S+ )?(zwyczajnego |nadzwyczajnego )?walnego"
    r"|(podjet\w*|podjal\w*) przez (zwyczajne |nadzwyczajne )?walne"
    r"|zgromadzenie [^.]{0,40}uchwala"
    r"|uchwal\w* (podjet\w*|powzi\w*)[^.]{0,40}(zwz|wza|walne)"
)
_PROPOSED = re.compile(
    r"rekomend\w*|propozycj\w*|proponowan\w*|wniosk\w*|intencj\w*|zamierz\w*|projekt\w* uchwal"
    r"|zaproponuj\w*|przedlozy\w*|przedlozeni\w*|zaopiniowa\w*"
)
_PER_SHARE = re.compile(r"na (jedna|1|kazda|jednej) akcj\w*|na akcj[ea]\b|przypadajac\w* na akcj")
_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ \xa0.]\d{3})*(?:,\d{1,4})|\d+(?:[.,]\d{1,4})?)\s*"
    r"(zl\b|zlot\w*|pln\b|gr\b|grosz\w*|usd\b|eur\b|euro\b)(?:\s+(\d{1,2})\s+grosz\w*)?"
)
_RECORD = re.compile(
    r"dzien\w* dywidendy|dniem dywidendy"
    r"|(dzien|dni)\w* (ustalenia )?prawa (akcjonariuszy )?do (dywidendy|zaliczki)"
    r"|termin\w* (nabycia|ustalenia) praw\w* do dywidendy|dzien\w* nabycia praw\w* do dywidendy"
    r"|list\w* akcjonariuszy uprawnionych do dywidendy[^.]{0,40}ustala sie na"
    r"|dzien\w* ustalenia list\w* akcjonariuszy uprawnionych do dywidendy"
    r"|uprawnionymi do dywidendy (beda|sa) [^.]{0,60}w dzien"
    r"|dzien,? (wedlug|w) ktor\w+ (ustala sie liste akcjonariuszy|wlasciciele akcji nabywaja)"
    # an interim dividend: "dzień ustalenia prawa do zaliczki"
    r"|dzien\w* (ustalenia )?prawa do (otrzymania )?zaliczki|uprawnionych do (otrzymania )?zaliczki"
)
_PAY = re.compile(
    r"termin\w* wyplaty( dywidendy)?|dzien\w* wyplaty( dywidendy)?"
    r"|wyplat\w* (dywidendy )?nastapi|wyplacon\w* w dzien|wyplata dywidendy w dzien"
)
_FORM_DPS = re.compile(r"wartosc dywidendy przypadajaca na akcje((?:\s+\d+(?:[.,]\d+)?)+)")
_FORM_TOTAL = re.compile(r"wysokosc dywidendy\s+(\d[\d \xa0]*(?:,\d+)?)")
_FORM_RECORD = re.compile(r"dzien dywidendy\s+(\d{4}-\d{2}-\d{2})")
_FORM_PAY = re.compile(r"dzien wyplaty (dywidendy|transzy)\s+(\d{4}-\d{2}-\d{2})")
_FORM_CCY = re.compile(r"waluta dywidendy\s+([a-z]{3})")
_FISCAL = re.compile(
    r"(?:zysk\w*|rok\w*|obrotow\w*|za)\s+(?:netto\s+)?(?:za\s+)?(?:rok\s+)?(?:obrotowy\s+)?(20\d\d)"
)


@dataclass
class Dividend:
    """One dividend as a report states it; ``None`` = not stated."""

    stage: str
    dps: float | None = None
    currency: str = "PLN"
    record_date: date | None = None
    pay_date: date | None = None
    fiscal_year: int | None = None
    notes: list[str] = field(default_factory=list)


def _dps(text: str) -> tuple[float, str] | None:
    """The amount per share and its currency: the amount nearest an "on one share" phrase."""
    amounts = [
        (m.start(), m.end(), m.group(1), m.group(2), m.group(3)) for m in _AMOUNT.finditer(text)
    ]
    best: tuple[int, float, str] | None = None
    for m in _PER_SHARE.finditer(text):
        for s, e, num, unit, grosze in amounts:
            gap = m.start() - e if e <= m.start() else s - m.end()
            if gap < 0 or gap > 45:
                continue
            v = parse_pl_number(num)
            if v is None or v <= 0:
                continue
            if unit.startswith("gr"):
                v /= 100
            elif grosze:  # "7 złotych 43 grosze"
                v += int(grosze) / 100
            if v > 2000:  # a total, not an amount per share
                continue
            ccy = "USD" if unit == "usd" else "EUR" if unit.startswith("eur") else "PLN"
            if best is None or gap < best[0]:
                best = (gap, v, ccy)
    return (best[1], best[2]) if best else None


def _fiscal_year(text: str, published: date | None) -> int | None:
    for m in _FISCAL.finditer(text):
        y = int(m.group(1))
        if published is None or published.year - 2 <= y <= published.year:
            return y
    return None


def extract_dividend(
    title: str | None, body: str | None, published: date | None = None
) -> Dividend | None:
    """The issuer's own dividend in one report, or ``None`` when it is not about one."""
    t, b = clean(title), clean(body)
    if not (_DIV_TITLE.search(t) or _DIV_BODY.search(b)):
        return None
    if _SUBSIDIARY.search(t) or _SUBSIDIARY.search(b[:400]):
        return None
    if _FORM_TITLE.search(t) or _FORM_DPS.search(b):
        d = Dividend("resolved")
        if m := _FORM_DPS.search(b):
            # one value per instalment; the exchange adjusts for all of them on the ex-date
            parts = [parse_pl_number(x) for x in m.group(1).split()]
            d.dps = sum(x for x in parts if x is not None) or None
        if m := _FORM_CCY.search(b):
            d.currency = m.group(1).upper()
        if m := _FORM_RECORD.search(b):
            d.record_date = date.fromisoformat(m.group(1))
        if m := _FORM_PAY.search(b):
            d.pay_date = date.fromisoformat(m.group(2))
        if _PROPOSED.search(b) and not _RESOLVED.search(b):
            d.stage = "proposed"
        d.fiscal_year = _fiscal_year(b, published)
        return d
    # Only the stretch of text about the dividend: an AGM report carries every resolution.
    i = b.find("dywidend")
    window = b[max(0, i - 900) : i + 1500] if i >= 0 else b[:1500]
    text = t + " . " + window
    if _NO_DIV.search(text) and not _PER_SHARE.search(window):
        return Dividend("none", fiscal_year=_fiscal_year(text, published))
    if _INTERIM.search(text):
        stage = "interim"
    elif _RESOLVED.search(text) and not re.search(r"rekomend\w*|projekt\w* uchwal", t):
        stage = "resolved"
    elif _PROPOSED.search(text):
        stage = "proposed"
    else:
        stage = "resolved" if _RECORD.search(window) else "proposed"
    d = Dividend(stage)
    if (amount := _dps(window)) is not None:
        d.dps, d.currency = amount
    d.record_date = _date_near(window, _RECORD, published=published)
    d.pay_date = _date_near(window, _PAY, published=published)
    d.fiscal_year = _fiscal_year(text, published)
    if d.dps is None:
        d.notes.append("amount per share not stated")
    if d.record_date is None:
        d.notes.append("record date not stated")
    if d.pay_date is not None and d.record_date is not None and d.pay_date < d.record_date:
        d.pay_date = None
        d.notes.append("payment date before record date dropped")
    if d.dps is None and d.record_date is None and d.pay_date is None:
        # a policy, a KNF letter, a mention in passing: nothing to put in a calendar
        return None
    return d


def ex_date(record: date) -> date:
    """The ex-dividend session: one session before the record date (T+2 since 2014-10-06)."""
    # a record date on a closed day counts from the last session before it
    d = record
    while not gpw_calendar.is_session(d):
        d -= timedelta(days=1)
    back = 1 if record >= date(2014, 10, 6) else 2
    while back:
        d -= timedelta(days=1)
        if gpw_calendar.is_session(d):
            back -= 1
    return d


# --------------------------------------------------------------------------- meetings

_MEETING_TITLE = re.compile(r"walne\w* zgromadzeni\w*|\b(zwz|nwz|wza|zwza|nwza)\b")
_CONVENE = re.compile(r"zwolani\w*|zwoluje|ogloszeni\w* (o zwolaniu|zarzadu o zwolaniu)")
_CANCEL = re.compile(r"odwolani\w*|odwoluje")
_MEETING_DATE = re.compile(r"na dzien|w dzien|zwolan\w* na|\bna\b(?= \d)|odbedzie sie")


@dataclass(frozen=True)
class Meeting:
    """A general meeting a report convenes or cancels."""

    kind: str  # zwz | nwz | wz
    status: str  # convened | cancelled
    meeting_date: date | None


def extract_meeting(
    title: str | None, body: str | None, published: date | None = None
) -> Meeting | None:
    """A convened or cancelled general meeting, or ``None``."""
    t, b = clean(title), clean(body)
    if not _MEETING_TITLE.search(t):
        return None
    if re.search(r"przerw\w* w obradach|wznowieni|projekt\w* uchwal\w* zgloszon|zgloszeni\w*", t):
        return None
    status = "cancelled" if _CANCEL.search(t) else "convened" if _CONVENE.search(t) else None
    if status is None:
        return None
    if re.search(r"nadzwyczajn|\bnwz", t):
        kind = "nwz"
    elif re.search(r"zwyczajn|\bzwz", t):
        kind = "zwz"
    else:
        kind = "wz"
    when = _date_near(t, _MEETING_DATE, after=25, before=0) or _date_near(
        b[:900], _MEETING_DATE, after=25, before=0
    )
    if (
        when is not None
        and published is not None
        and not (published - timedelta(days=5) <= when <= published + timedelta(days=200))
    ):
        when = None
    return Meeting(kind, status, when)


# --------------------------------------------------------------------------- report dates

PERIODS = ("q1", "q2", "q3", "q4", "h1", "annual")
_SCHEDULE_TITLE = re.compile(
    r"(termin\w*|harmonogram\w*|kalendarz\w*|dat\w*) [^.]{0,40}"
    r"(przekazywani|publikacj|publikowani|przekazani)\w* [^.]{0,30}raport\w* okresow"
    r"|raport\w* okresow\w* [^.]{0,20}(termin|harmonogram)"
)
_CHANGE = re.compile(r"zmian\w* (termin|daty|harmonogram)|przesunieci\w* termin|korekt\w* termin")
_PERIOD_RX: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("h1", re.compile(r"polroczn|polrocz|\bh1\b|\bi pol")),
    ("annual", re.compile(r"roczn\w*|\bza (rok )?(obrotowy )?20\d\d( r\.?| rok)?\b(?! ?kw)")),
    ("q4", re.compile(r"\b(iv|4)\s*\.?\s*(kw|kwartal)|czwart\w* kwartal")),
    ("q3", re.compile(r"\b(iii|3)\s*\.?\s*(kw|kwartal)|trzeci\w* kwartal")),
    ("q2", re.compile(r"\b(ii|2)\s*\.?\s*(kw|kwartal)|drugi\w* kwartal")),
    ("q1", re.compile(r"\b(i|1)\s*\.?\s*(kw|kwartal)|pierwsz\w* kwartal")),
)
# a date that ends a period, not a publication date: "rok zakończony 31 grudnia 2025"
_PERIOD_END = re.compile(
    r"(zakonczon\w*|konczac\w*|\bod( dzien)?|\bdo( dzien)?|stan na|okres\w*|(?<!w )\bna dzien)\s*$"
)


def _period_of(label: str) -> str | None:
    for name, rx in _PERIOD_RX:
        if rx.search(label):
            return name
    return None


def _year_of(label: str, period: str, schedule_year: int) -> int:
    ys = [int(y) for y in re.findall(r"\b(20\d\d)\b", label)]
    if ys:
        return ys[-1]
    return schedule_year - 1 if period in ("annual", "q4") else schedule_year


def extract_report_dates(
    title: str | None, body: str | None, published: date
) -> list[tuple[str, int, date]]:
    """``(period, fiscal_year, publication_date)`` for each date a schedule announces."""
    t, b = clean(title), clean(body)
    if not (_SCHEDULE_TITLE.search(t) or (_CHANGE.search(t) and "raport" in t)):
        return []
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]
    schedule_year = years[-1] if years else (published.year + (published.month == 12))
    out: dict[tuple[str, int], date] = {}
    last_end = 0
    text = b
    for s, e, d in dates_in(text):
        if _PERIOD_END.search(text[max(0, s - 22) : s]):
            continue  # part of the label ("roczny za rok zakończony 31 grudnia 2025 – …")
        label = text[last_end:s]
        last_end = e
        if not (published <= d <= published + timedelta(days=500)):
            continue
        # the label is the text since the previous date; only its last stretch names it
        tail = label[-140:]
        period = _period_of(tail)
        if period is None:
            continue
        key = (period, _year_of(tail, period, schedule_year))
        # a change report says "from <old> to <new>": the later mention is the new date
        if key not in out or _CHANGE.search(t):
            out[key] = d
    return [(p, y, d) for (p, y), d in out.items()]


# --------------------------------------------------------------------------- build

DIVIDEND_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "isin": pl.String,
    "published": pl.Date,
    "stage": pl.String,
    "dps": pl.Float64,
    "currency": pl.String,
    "record_date": pl.Date,
    "ex_date": pl.Date,
    "pay_date": pl.Date,
    "fiscal_year": pl.Int64,
    "notes": pl.String,
}
MEETING_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "isin": pl.String,
    "published": pl.Date,
    "kind": pl.String,
    "status": pl.String,
    "meeting_date": pl.Date,
}
REPORT_DATE_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "isin": pl.String,
    "published": pl.Date,
    "period": pl.String,
    "fiscal_year": pl.Int64,
    "report_date": pl.Date,
}


@dataclass
class CalendarTables:
    """Everything the calendar reads, one row per report and event."""

    dividends: pl.DataFrame
    meetings: pl.DataFrame
    report_dates: pl.DataFrame


def extract_all(reports: pl.DataFrame) -> CalendarTables:
    """Run the three extractors over ``reports`` (report_id, isin, title, body_text, published)."""
    divs: list[dict[str, Any]] = []
    meets: list[dict[str, Any]] = []
    sched: list[dict[str, Any]] = []
    for r in reports.iter_rows(named=True):
        if not r["isin"]:
            continue
        pub = r["published"]
        title, body = r["title"], r["body_text"]
        base = {"report_id": r["report_id"], "isin": r["isin"], "published": pub}
        # cheap screens first: normalising every body of the lake is most of the cost
        low_t, low_b = (title or "").lower(), (body or "").lower()
        maybe_div = "dywidend" in low_b or "dywidend" in low_t or "zysk" in low_t
        if maybe_div and (d := extract_dividend(title, body, pub)) is not None:
            divs.append(
                base
                | {
                    "stage": d.stage,
                    "dps": d.dps,
                    "currency": d.currency,
                    "record_date": d.record_date,
                    "ex_date": ex_date(d.record_date) if d.record_date else None,
                    "pay_date": d.pay_date,
                    "fiscal_year": d.fiscal_year,
                    "notes": "; ".join(d.notes) or None,
                }
            )
        maybe_meeting = "zgromadz" in low_t or re.search(r"\b(n?zwz|n?wza|nwz)", low_t)
        if maybe_meeting and (m := extract_meeting(title, body, pub)) is not None:
            meets.append(
                base | {"kind": m.kind, "status": m.status, "meeting_date": m.meeting_date}
            )
        if "raport" not in low_t:
            continue
        for period, fy, when in extract_report_dates(title, body, pub):
            sched.append(base | {"period": period, "fiscal_year": fy, "report_date": when})
    return CalendarTables(
        pl.DataFrame(divs, schema=DIVIDEND_SCHEMA),
        pl.DataFrame(meets, schema=MEETING_SCHEMA),
        pl.DataFrame(sched, schema=REPORT_DATE_SCHEMA),
    )


def latest_dividends(divs: pl.DataFrame) -> pl.DataFrame:
    """One row per dividend: the latest report about it wins.

    A dividend is one issuer's payout for one record date; a report that states no record
    date belongs with the issuer's other reports of the same fiscal year (or, without one,
    of the same season: published within 200 days).
    """
    if divs.is_empty():
        return divs
    rows = divs.sort("isin", "published", "report_id").to_dicts()
    groups: list[list[dict[str, Any]]] = []
    for r in rows:
        g = next(
            (
                g
                for g in reversed(groups)
                if g[-1]["isin"] == r["isin"]
                and r["published"] - g[-1]["published"] <= timedelta(days=200)
                and _same_dividend(g, r)
            ),
            None,
        )
        if g is None:
            groups.append([r])
        else:
            g.append(r)
    out = []
    for g in groups:
        last = dict(g[-1])
        # a later report that leaves a field out does not erase what an earlier one said
        for k in ("dps", "record_date", "ex_date", "pay_date", "fiscal_year"):
            if last[k] is None:
                last[k] = next((x[k] for x in reversed(g) if x[k] is not None), None)
        # once the meeting has resolved, a later note about the dividend does not undo that
        if last["stage"] == "proposed" and any(x["stage"] == "resolved" for x in g):
            last["stage"] = "resolved"
        last["reports"] = len(g)
        last["first_report_id"] = g[0]["report_id"]
        out.append(last)
    return pl.DataFrame(out).sort("isin", "published")


def _same_dividend(group: list[dict[str, Any]], r: dict[str, Any]) -> bool:
    rec = r["record_date"]
    seen = {x["record_date"] for x in group if x["record_date"]}
    if rec and seen:
        return rec in seen
    if r["stage"] == "interim" or any(x["stage"] == "interim" for x in group):
        return (
            r["stage"] == "interim"
            and all(x["stage"] == "interim" for x in group)
            and (rec is None or not seen)
        )
    fys = {x["fiscal_year"] for x in group if x["fiscal_year"]}
    return not (r["fiscal_year"] and fys and r["fiscal_year"] not in fys)


def latest_meetings(meets: pl.DataFrame) -> pl.DataFrame:
    """One row per (issuer, meeting date), with cancellations applied."""
    if meets.is_empty():
        return meets
    m = meets.filter(pl.col("meeting_date").is_not_null()).sort("published", "report_id")
    return m.unique(["isin", "meeting_date", "kind"], keep="last", maintain_order=True)


def latest_report_dates(sched: pl.DataFrame) -> pl.DataFrame:
    """One row per (issuer, period, fiscal year): the latest announcement wins."""
    if sched.is_empty():
        return sched
    return sched.sort("published", "report_id").unique(
        ["isin", "period", "fiscal_year"], keep="last", maintain_order=True
    )


# --------------------------------------------------------------------------- exchange check

CHECK_TOL = 0.03
# The exchange rounds the adjusted reference price to its tick, up to about 0.5 % of the price
# for a thinly traded stock: 2.60 zł on a 120 zł share can show as an adjustment of 2.50 zł.
TICK_TOL = 0.005


def check_against_exchange(divs: pl.DataFrame, prices: pl.DataFrame) -> pl.DataFrame:
    """Each past dividend with the amount the exchange adjusted for on its ex-date.

    ``prices``: isin, trade_date, close, prev_close, exch_reference (``adjusted_prices``).
    ``exchange_dps`` is ``prev_close − reference``; ``agrees`` when it is within
    ``CHECK_TOL`` of the extracted amount, or within a tick (``TICK_TOL`` of the price).
    """
    cols = ["isin", "trade_date", "close", "prev_close", "exch_reference"]
    d = divs.filter(
        pl.col("ex_date").is_not_null()
        & pl.col("dps").is_not_null()
        & (pl.col("stage") != "none")
        & (pl.col("currency") == "PLN")
    )
    j = d.join(
        prices.select(cols),
        left_on=["isin", "ex_date"],
        right_on=["isin", "trade_date"],
        how="inner",
    )
    j = j.with_columns(
        exchange_dps=(pl.col("prev_close") - pl.col("exch_reference")),
    ).with_columns(
        agrees=(
            (pl.col("exchange_dps") - pl.col("dps")).abs()
            <= pl.max_horizontal(
                pl.col("dps") * CHECK_TOL, pl.col("prev_close") * TICK_TOL, pl.lit(0.011)
            )
        ),
        drop_ratio=(pl.col("prev_close") - pl.col("close")) / pl.col("dps"),
        yield_=(pl.col("dps") / pl.col("prev_close")),
    )
    return j


def drop_off(
    checked: pl.DataFrame, bench: dict[date, float], seed: int = 20261120
) -> dict[str, Any]:
    """How much of the dividend the price lost on the ex-date, market move taken out.

    The drop-off ratio is ``(prev_close − close − prev_close·r_WIG) / dps``: 1 means the price
    fell by the whole dividend, 0.81 by the dividend after Poland's 19 % tax. Only dividends
    the exchange confirmed (``agrees``) and of at least 1 % of the price, where a tick of
    rounding cannot swamp the ratio. A 95 % bootstrap interval of the median.
    """
    ok = checked.filter(pl.col("agrees") & (pl.col("yield_") >= 0.01))
    ratios = []
    for r in ok.iter_rows(named=True):
        wig = bench.get(r["ex_date"])
        if wig is None or r["prev_close"] is None or r["close"] is None:
            continue
        ratios.append((r["prev_close"] - r["close"] - r["prev_close"] * wig) / r["dps"])
    if len(ratios) < 10:
        return {"n": len(ratios)}
    a = np.asarray(ratios)
    a = a[np.isfinite(a)]
    rng = np.random.default_rng(seed)
    meds = np.median(rng.choice(a, size=(2000, a.size), replace=True), axis=1)
    lo, hi = np.percentile(meds, [2.5, 97.5])
    return {
        "n": int(a.size),
        "median": float(np.median(a)),
        "mean": float(np.mean(a)),
        "lo": float(lo),
        "hi": float(hi),
        "share_full": float(np.mean(a >= 0.9)),
    }


PERIODIC_KIND = {
    "q1": "Kwartalny",
    "q2": "Kwartalny",
    "q3": "Kwartalny",
    "q4": "Kwartalny",
    "h1": "Półroczny",
    "annual": "Roczny",
}


def dates_kept(sched: pl.DataFrame, periodic: pl.DataFrame, day: date) -> dict[str, Any]:
    """How often an announced report date was kept: a periodic report out within a day.

    ``periodic``: isin, published (the lake's quarterly, half-year and annual reports).
    Issuers may publish early, and the lake has holes, so this is a floor, not an accuracy.
    """
    past = sched.filter(pl.col("report_date") <= day)
    if past.is_empty() or periodic.is_empty():
        return {"n": past.height}
    j = past.join(periodic.select("isin", pl.col("published").alias("out")), on="isin").filter(
        (pl.col("out") - pl.col("report_date")).dt.total_days().abs() <= 1
    )
    kept = j.select("report_id", "isin", "period", "fiscal_year").unique().height
    early = (
        past.join(periodic.select("isin", pl.col("published").alias("out")), on="isin")
        .filter((pl.col("report_date") - pl.col("out")).dt.total_days().is_between(2, 20))
        .select("report_id", "isin", "period", "fiscal_year")
        .unique()
        .height
    )
    return {"n": past.height, "kept": kept, "early": early}


GOLD = {
    "dividends": "calendar_dividends.parquet",
    "meetings": "calendar_meetings.parquet",
    "report_dates": "calendar_report_dates.parquet",
    "checked": "calendar_checked.parquet",
}
STATS_FILE = "calendar_stats.json"


def run_calendar(duckdb_file: Path, work: Path, day: date) -> Path:
    """Extract the calendar from every report known by ``day`` and write it to gold."""
    import json

    import duckdb

    from undercurrent import lake, study

    as_of = study.as_of_for(day)
    con = duckdb.connect(str(duckdb_file))
    try:
        reps = con.sql(
            "select report_id, isin, title, body_text, signal_type, "
            "cast(substr(published_at_local, 1, 10) as date) as published "
            "from reports where isin is not null and known_at <= ?",
            params=[as_of],
        ).pl()
        prices = con.sql(
            "select isin, trade_date, close, prev_close, exch_reference from adjusted_prices "
            "where trade_date <= ?",
            params=[day],
        ).pl()
        bench = dict(
            con.sql(
                "select trade_date, wig_ret from benchmark_returns where wig_ret is not null"
            ).fetchall()
        )
    finally:
        con.close()
    tabs = extract_all(reps.filter(pl.col("signal_type") != "periodic"))
    divs = latest_dividends(tabs.dividends)
    meets = latest_meetings(tabs.meetings)
    sched = latest_report_dates(tabs.report_dates)
    checked = (
        check_against_exchange(divs.filter(pl.col("ex_date") <= day), prices)
        if not divs.is_empty()
        else pl.DataFrame()
    )
    periodic = reps.filter(pl.col("signal_type") == "periodic").select("isin", "published")
    if not sched.is_empty():
        # a report that came out early is no longer coming up
        done = (
            sched.join(periodic.rename({"published": "out"}), on="isin")
            .filter(
                (pl.col("out") > pl.col("published"))
                & (pl.col("report_date") - pl.col("out")).dt.total_days().is_between(0, 25)
            )
            .select("report_id", "period", "fiscal_year", pl.lit(True).alias("done"))
            .unique()
        )
        sched = sched.join(
            done, on=["report_id", "period", "fiscal_year"], how="left"
        ).with_columns(pl.col("done").fill_null(False))
    gold = work / "gold"
    for key, frame in (
        ("dividends", divs),
        ("meetings", meets),
        ("report_dates", sched),
        ("checked", checked),
    ):
        lake.write_frame(frame, gold / GOLD[key])
    stats = {
        "day": day.isoformat(),
        "checked": checked.height,
        "agree": int(checked["agrees"].sum()) if checked.height else 0,
        "drop": drop_off(checked, bench) if checked.height else {"n": 0},
        "dates": dates_kept(sched, periodic, day),
        "dividends": divs.height,
        "meetings": meets.height,
        "report_dates": sched.height,
    }
    out = gold / STATS_FILE
    out.write_text(json.dumps(stats, indent=1, sort_keys=True), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- site context

HORIZON_DAYS = 45


def context(
    work: Path, day: date, stocks: dict[str, dict[str, Any]], closes: dict[str, float]
) -> dict[str, Any] | None:
    """What the calendar page and the stock pages show, or ``None`` before a first run.

    ``stocks``: ISIN → the stock's page metadata (ticker, slug, name); only stocks with a
    page are listed. ``closes``: ISIN → last close, for the yield.
    """
    import json

    gold = work / "gold"
    if not (gold / STATS_FILE).exists():
        return None
    divs = pl.read_parquet(gold / GOLD["dividends"])
    meets = pl.read_parquet(gold / GOLD["meetings"])
    sched = pl.read_parquet(gold / GOLD["report_dates"])
    stats = json.loads((gold / STATS_FILE).read_text(encoding="utf-8"))
    until = day + timedelta(days=HORIZON_DAYS)
    entries: list[dict[str, Any]] = []

    def add(isin: str, when: date | None, kind: str, **kw: Any) -> None:
        st = stocks.get(isin)
        if st is None or when is None or not (day < when <= until):
            return
        entries.append({"date": when, "kind": kind, "isin": isin, "stock": st, **kw})

    dividends = []
    if not divs.is_empty():
        for r in divs.filter(pl.col("stage") != "none").iter_rows(named=True):
            close = closes.get(r["isin"])
            dy = r["dps"] / close if r["dps"] and close and r["currency"] == "PLN" else None
            row = {**r, "yield": dy}
            add(r["isin"], r["ex_date"], "ex", div=row)
            add(r["isin"], r["pay_date"], "pay", div=row)
            if r["isin"] in stocks and (
                (r["ex_date"] and r["ex_date"] > day)
                or (r["ex_date"] is None and r["pay_date"] and r["pay_date"] > day)
            ):
                dividends.append({**row, "stock": stocks[r["isin"]]})
    if not meets.is_empty():
        for r in meets.filter(pl.col("status") == "convened").iter_rows(named=True):
            add(r["isin"], r["meeting_date"], r["kind"], report_id=r["report_id"])
    if not sched.is_empty():
        fresh = sched.filter((pl.col("published") >= day - timedelta(days=460)) & ~pl.col("done"))
        for r in fresh.iter_rows(named=True):
            add(
                r["isin"],
                r["report_date"],
                "results",
                period=r["period"],
                fiscal_year=r["fiscal_year"],
                report_id=r["report_id"],
            )
    entries.sort(key=lambda e: (e["date"], KIND_ORDER.get(e["kind"], 9), e["stock"]["ticker"]))
    dividends.sort(key=lambda d: (d["ex_date"] or d["pay_date"], d["stock"]["ticker"]))
    days: list[dict[str, Any]] = []
    for e in entries:
        if not days or days[-1]["date"] != e["date"]:
            days.append({"date": e["date"], "entries": []})
        days[-1]["entries"].append(e)
    by_isin: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_isin.setdefault(e["isin"], []).append(e)
    counts = {k: sum(1 for e in entries if e["kind"] == k) for k in KIND_ORDER}
    return {
        "days": days,
        "dividends": dividends,
        "by_isin": by_isin,
        "counts": counts,
        "stats": stats,
        "until": until,
    }


KIND_ORDER = {"ex": 0, "pay": 1, "results": 2, "zwz": 3, "nwz": 4, "wz": 5}
