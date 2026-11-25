# ruff: noqa: E501  (the label rules are kept one per line)
"""Periodic reports straight from the exchange: the figures on gpw.pl's own report pages.

Every annual, half-year and quarterly report an issuer sends through ESPI has a page on
gpw.pl (``komunikat?geru_id=``) that holds the report's form, not only its
attachments: a title sheet (the form code, ``RR`` / ``SRR`` / ``QSr`` …, the period, the
currency) and the sheet "WYBRANE DANE FINANSOWE", the selected financial data the issuer
types into the form: revenue, operating profit, profit before tax, net profit, the three
cash flows, total assets, liabilities, equity, the share count, EPS. The attachments
themselves (the ESEF package, the PDFs) stay behind bot protection (§ 4) and are not
fetched; the page is plain HTML and served to the project's client like any report page.

1. **Finding the reports.** The listing (``bronze/reports``) already holds every report
   the nightly run saw. Its kind label is not reliable for periodic reports (an annual
   report is listed as "Bieżący" one year and "Kwartalny" the next), but a periodic report
   has no running number ("/2025", where a current report is "12/2025"), so every main-market
   row without one is a candidate. Annual reports the listing lacks are looked up on the
   company card's ESPI tab (``reportsTab``), which names each report by its form code.
2. **Reading a page.** The title sheet gives the form and period; the selected data gives
   the figures. Only the current period's column in the report currency is kept, and for an
   annual report the prior year's column too. The labels are the issuer's own words, mapped
   to the concepts of ``esef.CONCEPTS`` by ``_RULES``; a sheet whose złoty and euro columns
   do not agree (the ratio of the two is the exchange rate) is refused.
3. **Writing.** ``bronze/fundamentals_gpw`` holds one row per fact, keyed and shaped as
   ``bronze/fundamentals_esef`` plus the ISIN, the form and the period kind (annual, half,
   quarter). ``esef.fundamentals_as_of`` reads the annual ones next to the ESEF facts.

``known_at`` is the report's publication time on the exchange. Pages are read once
(``state/gpw_reports``); one request a second, ~120 kB a page.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
from selectolax.lexbor import LexborHTMLParser

from undercurrent import esef, indicators, lake
from undercurrent.fetch import PoliteClient
from undercurrent.parse import parse_pl_number
from undercurrent.sources import GPW_BASE, espi_detail_url

log = logging.getLogger(__name__)

TABLE = "fundamentals_gpw"
STATE = "gpw_reports"  # one row per report page read
COMPANY_STATE = "gpw_report_companies"  # when each company card was last paged
TAB_URL = (
    GPW_BASE + "/ajaxindex.php?start=reportsTab&type=e&action=GPWListaSp"
    "&gls_isin={isin}&format=html&lang=PL&offset={offset}"
)
TAB_PAGE_SIZE = 5  # the card shows five reports a page, whatever is asked
MAX_TAB_PAGES = 20
SINCE = date(2024, 1, 1)
RECHECK_DAYS = 30
WARSAW = ZoneInfo("Europe/Warsaw")
NATIVE_PREFIX = esef.NATIVE_PREFIX  # filing ids of these facts: gpw:<geru_id>

# form code → (period kind, consolidated). "Skonsolidowany" in the title also marks a group
# report: some issuers file "Skonsolidowany raport półroczny PS".
FORMS: dict[str, tuple[str, bool]] = {
    "RR": ("annual", False),
    "SRR": ("annual", True),
    "Q": ("quarter", False),
    "QS": ("quarter", False),
    "QSr": ("quarter", True),
    "P": ("half", False),
    "PS": ("half", False),
    "PSr": ("half", True),
}
_FORM_CODE = re.compile(r"^(?:SA-)?(S?RR|QSr|QS|Q|PSr|PS|P)$")
_TITLE = re.compile(
    r"(Skonsolidowany\s+)?raport\s+(?:roczny|kwartalny|p[oó]łroczny)\s+"
    r"(SRR|RR|QSr|QS|Q|PSr|PS|P)\s+(?:(\d)\s*/\s*)?(\d{4})",
    re.I,
)
_PERIOD = re.compile(r"obejmując\w*\s+okres\s+(?:od\s+)?(\d{4}-\d\d-\d\d)\s+do\s+(\d{4}-\d\d-\d\d)")
_HEADER_PERIOD = re.compile(r"okres\s+od\s+(\d{4}-\d\d-\d\d)\s+do\s+(\d{4}-\d\d-\d\d)")
_CURRENCY = re.compile(r"w\s+walucie\s+([A-Z]{3})")
_YEAR = re.compile(r"(?<!\d)(\d)\s?(\d{3})(?!\d)")
_TAB_ROW = re.compile(
    r'geru_id=(\d+)[^"]*">(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)</a>\s*</td>\s*<td[^>]*>'
    r"\s*([^<]*?)<br>\s*<a[^>]*>\s*([^<]*?)\s*</a>",
    re.S,
)
# EUR/PLN in the years the reports cover was 4.0–4.9; the selected data's euro columns use
# the period's average or closing rate.
_EUR_RATE = (3.7, 5.2)

FACT_SCHEMA: dict[str, Any] = {
    **esef.FACT_SCHEMA,
    "isin": pl.String,
    "geru_id": pl.Int64,
    "form": pl.String,
    "period_kind": pl.String,  # annual | half | quarter
    "parser_version": pl.Int64,
}
STATE_SCHEMA: dict[str, Any] = {
    "geru_id": pl.Int64,
    "isin": pl.String,
    "form": pl.String,
    "fiscal_year": pl.Int64,
    "period_end": pl.Date,
    "published": pl.Datetime("us", "UTC"),
    "facts": pl.Int64,
    # ok | failed_checks (see checks) | no_data | no_period | not_periodic | error
    "status": pl.String,
    "checks": pl.String,  # failed checks and warnings (``check``), ";"-separated
    "error": pl.String,
    "attempts": pl.Int64,
    "parser_version": pl.Int64,
    "read_at": pl.Datetime("us", "UTC"),
}

# Concepts the form adds to esef.CONCEPTS' short names. Not read by valuation (yet).
EXTRA_CONCEPTS = (
    "investing_cash_flow",
    "financing_cash_flow",
    "share_capital",
    "shares_outstanding",
    "book_value_per_share",
)
PER_SHARE = frozenset({"eps_basic", "book_value_per_share"})
SHARE_COUNTS = frozenset({"shares_weighted", "shares_outstanding"})
BALANCES = frozenset(
    {
        "assets", "current_assets", "noncurrent_assets", "liabilities", "current_liabilities",
        "noncurrent_liabilities", "equity", "equity_parent", "cash", "share_capital",
        "shares_outstanding", "book_value_per_share",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Candidate:
    """A report that may be periodic: its page id, issuer and publication time (UTC)."""

    geru_id: int
    isin: str
    published: datetime


@dataclass
class Title:
    """What the report's title sheet says."""

    form: str
    consolidated: bool
    fiscal_year: int
    quarter: int | None
    period_start: date | None
    period_end: date | None
    currency: str

    @property
    def kind(self) -> str:
        """annual, half or quarter."""
        return FORMS[self.form][0]


@dataclass
class Sheet:
    """The selected financial data: concept → values of the current and prior column."""

    values: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    period_start: date | None = None  # from the sheet's own header, when it gives one
    period_end: date | None = None
    swapped: bool = False  # the header put the prior period first
    checked: int = 0  # rows whose złoty and euro columns were compared
    disagree: int = 0
    problems: list[str] = field(default_factory=list)  # failed checks: nothing is kept
    warnings: list[str] = field(default_factory=list)  # kept, but worth a look


# ------------------------------------------------------------------ labels → concepts


def _norm(label: str) -> str:
    """A row label without its numbering, brackets and the date it is "as at"."""
    t = label.replace("_", " ").replace("(", " ").replace(")", " ").lower()
    t = t.replace("zobowiaz", "zobowiąz")  # typed without the ogonek
    t = re.sub(r"^\s*(?:[ivxlc]+\.\s*)+", "", t)  # "I. I. Przychody" (numbered twice)
    t = re.sub(r"(?<=\D),", " ", t)  # "Kapitał własny, ogółem" (a decimal comma stays)
    t = re.sub(r"^\s*\d+[.)]\s*", "", t)
    t = re.sub(
        r"\b(na dzień|stan na|na koniec|koniec poprzedniego|kwartał\w* narastająco)\b.*$", "", t
    )
    return re.sub(r"\s+", " ", t).strip(" .:-*")


_PROFIT = r"^(zysk|strata|wynik)\w*(\s*/\s*\w+)?"
_PARENT = r"(dominuj|akcjonariusz\w* (banku|spółki|jednostki|podmiotu|emitenta)|właściciel)"
_MINORITY = re.compile(r"niekontroluj|niedając\w* kontroli|mniejszości")
_PER_SHARE = r"na (jedn\w+ |1 )?akcj[ęei]\b"
_RULES: tuple[tuple[str, re.Pattern[str], re.Pattern[str] | None], ...] = (
    # (concept, label pattern, unless); the first rule that matches wins
    # share counts first: "liczba akcji zastosowana do obliczenia zysku na jedną akcję"
    ("shares_weighted", re.compile(r"średni\w* ?ważon\w* liczb\w*( \w+)* akcji|^liczba akcji.*obliczeni\w* zysk"), re.compile(r"rozwodnion")),
    ("shares_outstanding", re.compile(r"^liczba (udziałów/)?akcji"), re.compile(r"rozwodnion")),
    ("eps_basic", re.compile(r"(zysk|strata|wynik).*" + _PER_SHARE), re.compile(r"^rozwodnion|dywidend|liczb")),
    ("book_value_per_share", re.compile(r"warto\w+ (księgow\w+|aktywów netto).*" + _PER_SHARE), re.compile(r"^rozwodnion")),
    ("revenue", re.compile(r"^(łączne |całkowite )?przychod\w*( netto| ogółem| razem)?( ze sprzedaży| z umów| z działalności operacyjnej|$)"), re.compile(r"pozostał|finansow|odset")),
    ("gross_profit", re.compile(_PROFIT + r".*brutto (ze|na) sprzedaży"), None),
    # "zysk na sprzedaży" (after selling and general costs) is neither gross nor operating
    ("", re.compile(_PROFIT + r".*(ze|na) sprzedaży"), None),
    ("operating_profit", re.compile(_PROFIT + r".*(z|na) działalności operacyjnej|^ebit$|" + _PROFIT + r"\s+operacyjn\w*$"), re.compile(r"przepływ|środki|amortyzacj|ebitda")),
    ("profit_before_tax", re.compile(_PROFIT + r".*(brutto|przed opodatkowaniem|do opodatkowania)"), re.compile(r"odpis")),
    ("net_profit_parent", re.compile(_PROFIT + r".*netto.*" + _PARENT), _MINORITY),
    ("net_profit", re.compile(_PROFIT + r".*netto"), re.compile(r"całkowit|przed|odpis|niekontrol|niedając|" + _PARENT)),
    ("operating_cash_flow", re.compile(r"(przepływ|środki pieniężne).*operacyjn"), None),
    ("investing_cash_flow", re.compile(r"(przepływ|środki pieniężne).*inwestycyjn"), None),
    ("financing_cash_flow", re.compile(r"(przepływ|środki pieniężne).*finansow"), re.compile(r"operacyjn|inwestycyjn")),
    ("cash", re.compile(r"^środki pieniężne( i (ich )?ekwiwalent\w*( środków pieniężnych)?)?( na koniec okresu)?$"), None),
    ("assets", re.compile(r"^(aktywa,? (razem|ogółem)|razem aktywa|suma aktywów|suma bilansowa|aktywa|aktywa ?/ ?pasywa,? razem|pasywa,? razem)$"), None),
    ("current_assets", re.compile(r"^aktywa obrotowe( razem)?$"), None),
    ("noncurrent_assets", re.compile(r"^aktywa trwałe( razem)?$"), None),
    ("noncurrent_liabilities", re.compile(r"(zobowiązania( i rezerwy)? długoterminowe|długoterminowe zobowiązania)"), None),
    ("current_liabilities", re.compile(r"(zobowiązania( i rezerwy)? krótkoterminowe|krótkoterminowe zobowiązania)"), None),
    ("liabilities", re.compile(r"^(zobowiązania|zobowiązania i rezerwy na zobowiązania|zobowiązania razem|zobowiązania ogółem|suma zobowiązań)$"), None),
    ("equity_parent", re.compile(r"^kapitał\w*( własn\w*)?.*" + _PARENT), _MINORITY),
    ("equity", re.compile(r"^kapitał\w* własn\w*( razem| ogółem)?$"), None),
    ("share_capital", re.compile(r"^kapitał (zakładowy|podstawowy|akcyjny)"), None),
)  # fmt: skip
# Foreign issuers fill the form in English.
_OWNERS = r"(shareholders|owners|equity holders) of the (parent|company)"
_RULES_EN: tuple[tuple[str, re.Pattern[str], re.Pattern[str] | None], ...] = (
    ("shares_weighted", re.compile(r"weighted average number of (ordinary )?shares"), re.compile(r"diluted")),
    ("shares_outstanding", re.compile(r"^number of (ordinary )?shares"), re.compile(r"diluted")),
    ("eps_basic", re.compile(r"(earnings|profit|loss).*per (ordinary )?share"), re.compile(r"diluted|dividend")),
    ("book_value_per_share", re.compile(r"book value per (ordinary )?share"), re.compile(r"diluted")),
    ("revenue", re.compile(r"^(total )?(net )?(revenues?|sales)( from sales| from contracts with customers| revenues?)?$"), None),
    ("gross_profit", re.compile(r"^gross (profit|loss)"), None),
    ("operating_profit", re.compile(r"operating (profit|loss|result)|(profit|loss).* (from|on) operat"), re.compile(r"ebitda|cash|depreciation")),
    ("profit_before_tax", re.compile(r"(profit|loss).* before (income )?tax"), None),
    ("net_profit_parent", re.compile(r"(profit|loss|income).* attributable to (the )?" + _OWNERS), re.compile(r"non-?controlling")),
    ("net_profit", re.compile(r"^net (profit|loss|income)|^(profit|loss)( ?/ ?(profit|loss))? for the (period|year)$"), re.compile(r"attributable|per share|comprehensive|non-?controlling")),
    ("operating_cash_flow", re.compile(r"(cash flows?|net cash).*operating"), None),
    ("investing_cash_flow", re.compile(r"(cash flows?|net cash).*investing"), None),
    ("financing_cash_flow", re.compile(r"(cash flows?|net cash).*financing"), None),
    ("cash", re.compile(r"^cash and cash equivalents( at (the )?end of (the )?period)?$"), None),
    ("assets", re.compile(r"^total assets$"), None),
    ("current_assets", re.compile(r"^current assets"), None),
    ("noncurrent_liabilities", re.compile(r"^(non-?current|long-term) liabilities"), None),
    ("current_liabilities", re.compile(r"^(current|short-term) liabilities"), None),
    ("liabilities", re.compile(r"^(total )?liabilities$"), None),
    ("equity_parent", re.compile(r"equity attributable to (the )?" + _OWNERS), None),
    ("equity", re.compile(r"^(total )?(shareholders'? )?equity$"), None),
    ("share_capital", re.compile(r"^share capital"), None),
)  # fmt: skip


def concept_for(label: str) -> str | None:
    """The concept a row of the selected data reports, or None for any other line."""
    t = _norm(label)
    if ("kontynuowan" in t) != ("zaniechan" in t) or re.search(
        r"(continuing|discontinued) operation", t
    ):
        # "zysk netto z działalności kontynuowanej": a part, not the total; kept to check it
        if re.match(_PROFIT, t) and "netto" in t and "na akcj" not in t and "przypad" not in t:
            return "net_continuing" if "kontynuowan" in t else "net_discontinued"
        return None
    t = re.sub(r"\s*z działalności kontynuowanej i zaniechanej", "", t)
    if not t:
        return None
    for concept, rx, unless in (*_RULES, *_RULES_EN):
        if rx.search(t) and not (unless and unless.search(t)):
            return concept or None
    return None


# ------------------------------------------------------------------ the page


def _text(node: Any) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip()


def parse_title(html: str) -> Title | None:
    """The form, period and currency on the title sheet; None for anything not periodic."""
    tree = LexborHTMLParser(html)
    for bad in tree.css("script, style"):
        bad.decompose()
    body = tree.body
    text = _text(body) if body is not None else ""
    m = _TITLE.search(text)
    if m is None:
        return None
    form = m.group(2)
    form = next((f for f in FORMS if f.lower() == form.lower()), form)
    if form not in FORMS:
        return None
    period = _PERIOD.search(text, m.end())
    start = end = None
    if period:
        start, end = date.fromisoformat(period.group(1)), date.fromisoformat(period.group(2))
    cur = _CURRENCY.search(text, m.end())
    return Title(
        form=form,
        consolidated=FORMS[form][1] or bool(m.group(1)),
        fiscal_year=int(m.group(4)),
        quarter=int(m.group(3)) if m.group(3) else None,
        period_start=start,
        period_end=end,
        currency=cur.group(1) if cur else "PLN",
    )


def _sheet_table(tree: LexborHTMLParser) -> Any | None:
    """The table that follows the heading "Nazwa arkusza: WYBRANE DANE FINANSOWE"."""
    for h in tree.css("h4"):
        if "WYBRANE DANE FINANSOWE" in h.text().upper():
            node = h.next
            while node is not None and node.tag != "table":
                node = node.next
            return node
    return None


def _cells(row: Any) -> list[str]:
    return [re.sub(r"\s+", " ", td.text()).strip() for td in row.css("td")]


_NUMBER_CELL = re.compile(r"[-–—−]?\s*[_(]?\s*[-−]?[\d\s.,]+\s*[_)]?|[-–—]")


def _cell_number(cell: str) -> float | None:
    """A figure as the page writes it: ``1 234,00``, ``-12``, ``-`` (none: 0), ``_14 937_``.

    The last is a bracketed negative: the page renders every bracket as an underscore.
    """
    c = cell.strip()
    if re.fullmatch(r"[-–—]", c):
        return 0.0
    m = re.fullmatch(r"[_(]\s*(.+?)\s*[_)]", c)
    if m:
        v = parse_pl_number(m.group(1))
        return None if v is None else -abs(v)
    return parse_pl_number(c)


def _numbers(cells: list[str]) -> tuple[str, list[float | None]]:
    """The row's label (its first text cell) and the figures after it."""
    label = ""
    nums: list[float | None] = []
    for c in cells:
        if not c:
            continue
        if not label and not nums and not _NUMBER_CELL.fullmatch(c):
            label = c
            continue
        nums.append(_cell_number(c))
    return label, nums


def _scale(text: str, current: float) -> float:
    """The unit a header or a section line sets: thousands, millions or plain units."""
    t = text.lower()
    if re.search(r"\bmln\b|milion", t):
        return 1e6
    if re.search(r"\btys\b|tysiąc", t):
        return 1e3
    if re.search(r"\bw (zł|złotych|pln)\b", t):
        return 1.0
    return current


def parse_sheet(html: str, consolidated: bool, shares_hint: float | None = None) -> Sheet:
    """The selected financial data of a report page, scaled to units of currency.

    Columns are: the period, the prior period (in the report currency), then the same two
    in euros. A consolidated report often lists the group's figures and then the parent
    company's; only the group's are read. The first row that maps to a concept wins.
    """
    sheet = Sheet()
    tree = LexborHTMLParser(html)
    table = _sheet_table(tree)
    if table is None:
        return sheet
    scale = 1e3  # the form's default is thousands
    standalone = False
    seen_data = False
    header_years: list[int] = []
    for row in table.css("tr"):
        if row.css_first("table") is not None:
            continue  # the frame around the sheet's own table
        cells = _cells(row)
        label, nums = _numbers(cells)
        joined = " ".join(c for c in cells if c)
        filled = [n for n in nums if n is not None]
        years_only = bool(filled) and all(n.is_integer() and 1990 < n < 2100 for n in filled)
        period_line = bool(_HEADER_PERIOD.search(joined)) or "narastająco" in joined
        if (
            (not seen_data and (len(filled) < 2 or years_only or period_line))
            or (not label and not filled)
            or (period_line and concept_for(label) is None)
        ):
            # a header line: units, currencies, the periods the columns cover
            scale = _scale(joined, scale)
            if sheet.period_end is None and (pm := _HEADER_PERIOD.search(joined)):
                sheet.period_start = date.fromisoformat(pm.group(1))
                sheet.period_end = date.fromisoformat(pm.group(2))
            if not header_years:
                years = [int(a + b) for a, b in _YEAR.findall(joined)]
                header_years = [y for y in years if 1990 < y < 2100]
            low = joined.lower()
            if "jednostkow" in low:
                standalone = True
            elif "skonsolidowan" in low or "grupy kapitałowej" in low or "grupa kapitałowa" in low:
                standalone = False
            continue
        if not filled:
            low = joined.lower()
            scale = _scale(joined, scale) if re.search(r"\b(mln|tys)\b", low) else scale
            if "jednostkow" in low:
                standalone = True
            elif "skonsolidowan" in low or "grupy kapitałowej" in low:
                standalone = False
            continue
        if not seen_data:
            # the header names the periods: the prior one first on some forms
            sheet.swapped = len(header_years) >= 2 and header_years[0] < header_years[1]
        seen_data = True
        low = label.replace("_", " ").lower()  # "_tys. szt._": brackets are underscores
        if "jednostkow" in low and concept_for(label) is None:
            standalone = True
            continue
        if consolidated and standalone:
            continue
        concept = concept_for(label)
        if concept is None or concept in sheet.values:
            continue
        cur_i, prev_i = (1, 0) if sheet.swapped else (0, 1)
        row_scale = 1.0 if concept in PER_SHARE else scale
        if concept in PER_SHARE and re.search(r"\bw gr\b|\bgr\s*/|grosz|eurocent", low):
            row_scale = 0.01  # "zysk na jedną akcję (w gr/eurocent)"
        elif concept in SHARE_COUNTS:
            row_scale = 1.0
            if re.search(r"\btys\b|thousand", low):
                row_scale = 1e3
            elif re.search(r"\bmln\b|million", low):
                row_scale = 1e6
        elif re.search(r"\bmln\b", low):
            row_scale = 1e6
        elif re.search(r"w (zł|pln)\b", low) and concept not in PER_SHARE:
            row_scale = 1.0
        cur = nums[cur_i] if len(nums) > cur_i else None
        prev = nums[prev_i] if len(nums) > prev_i else None
        sheet.values[concept] = (
            None if cur is None else cur * row_scale,
            None if prev is None else prev * row_scale,
        )
        # the euro columns follow; their ratio to the złoty columns is the exchange rate
        if len(nums) >= 4 and concept not in SHARE_COUNTS:
            pln, eur = nums[cur_i], nums[2 + cur_i]
            if pln is not None and eur and abs(pln) >= 50 and abs(eur) >= 10:
                sheet.checked += 1
                if not _EUR_RATE[0] <= pln / eur <= _EUR_RATE[1]:
                    sheet.disagree += 1
    _check_scale(sheet, shares_hint)
    return sheet


def _rescale(sheet: Sheet, factor: float, concepts: Iterable[str]) -> None:
    for k in concepts:
        a, b = sheet.values[k]
        sheet.values[k] = (None if a is None else a * factor, None if b is None else b * factor)


def _check_scale(sheet: Sheet, shares_hint: float | None) -> None:
    """Fix a share count or the money scale where the form contradicts itself.

    The header says thousands; a few issuers type millions or plain złoty, or a share count
    in thousands without saying so. Net profit ÷ shares must come out at the EPS the form
    gives, but that alone cannot tell which of the two is off. The exchange's own count of
    the company's shares (``indicators_gpw``, ``shares_hint``) settles it: the sheet's count
    is put on its scale first, and only then is the money rescaled. Without the hint
    nothing is changed.
    """
    counts = [k for k in SHARE_COUNTS if k in sheet.values]
    money = [k for k in sheet.values if k not in PER_SHARE and k not in SHARE_COUNTS]
    if not shares_hint or shares_hint <= 0:
        return
    for k in counts:
        n = sheet.values[k][0]
        if not n:
            continue
        for factor in (1.0, 1e3, 1e6, 1e-3):
            if 0.4 <= n * factor / shares_hint <= 2.5:
                if factor != 1.0:
                    _rescale(sheet, factor, [k])
                break
    eps = sheet.values.get("eps_basic", (None, None))[0]
    net = (sheet.values.get("net_profit_parent") or sheet.values.get("net_profit") or (None,))[0]
    shares = (
        sheet.values.get("shares_weighted") or sheet.values.get("shares_outstanding") or (None,)
    )[0]
    if not eps or not net or abs(eps) < 0.01:
        return
    if not shares or not 0.4 <= shares / shares_hint <= 2.5:
        shares = shares_hint
    implied = net / shares
    if 0.5 <= implied / eps <= 2:
        return
    for factor in (1e-3, 1e3, 1e-6, 1e6):
        if 0.7 <= implied * factor / eps <= 1.4:
            _rescale(sheet, factor, money)
            return


MIN_ASSETS = 1e5  # a main-market company with total assets under 100 000 is a misread scale
_TOL = 0.03  # assets = liabilities + equity, to rounding
# How far short of total assets liabilities + equity may fall: provisions and accruals a
# form leaves out of "zobowiązania", or minority interests when it gives only the parent's
# equity. Beyond that a figure is misread; above the assets it always is.
_UNLISTED_GAP = 0.15
_MINORITIES_GAP = 0.5


def check(vals: dict[str, tuple[float | None, float | None]], sheet: Sheet) -> None:
    """Check the figures against each other; put what fails in ``sheet.problems``.

    A report is kept only if every check passes (``facts_from_page``):

    * the złoty and euro columns agree at an exchange rate (``_EUR_RATE``);
    * liabilities + equity is never above total assets (beyond ``_TOL``), and short of them
      by at most ``_UNLISTED_GAP`` (provisions left out), ``_MINORITIES_GAP`` when the form
      gives only the parent's equity; a gap within those limits is a warning;
    * no part exceeds its total (current assets, cash, equity ≤ assets; either kind of
      liabilities ≤ liabilities); assets are not implausibly small (``MIN_ASSETS``);
    * net profit ÷ shares comes out at the EPS the form gives, with the same sign;
    * revenue is not negative;
    * continuing plus discontinued net profit, where the form gives both, is its net profit.

    The same balance check on the prior year's column, and book value per share against
    equity ÷ shares, only add warnings: comparatives are restated, share counts differ by
    date.
    """
    if sheet.checked >= 3 and sheet.disagree * 2 > sheet.checked:
        sheet.problems.append("eur_columns")
    elif sheet.checked < 3:
        sheet.warnings.append("eur_unchecked")

    def balance(col: int) -> str | None:
        v = {k: p[col] for k, p in vals.items()}
        a, li, eq, parent = (v.get(k) for k in ("assets", "liabilities", "equity", "equity_parent"))
        if a is None or a <= 0 or li is None or (eq is None and parent is None):
            return None
        if any(e is not None and abs(li + e - a) <= _TOL * a for e in (eq, parent)):
            return None
        e = eq if eq is not None else parent
        assert e is not None
        if li + e > a * (1 + _TOL):
            return "balance"  # more than the assets: a misread
        if eq is None and li + e >= a * (1 - _MINORITIES_GAP):
            return "minorities_gap"  # only the parent's equity: the rest is minority interests
        if li + e >= a * (1 - _UNLISTED_GAP):
            # "zobowiązania" without the provisions and accruals the balance sheet also holds
            return "unlisted_liabilities"
        return "balance"

    cur = {k: p[0] for k, p in vals.items()}
    b = balance(0)
    if b == "balance":
        sheet.problems.append(b)
    elif b is not None:
        sheet.warnings.append(b)
    if any(p[1] is not None for p in vals.values()) and balance(1) == "balance":
        sheet.warnings.append("balance_prior")
    a = cur.get("assets")
    if a is not None:
        if 0 < a < MIN_ASSETS:
            sheet.problems.append("assets_tiny")
        for part in ("current_assets", "noncurrent_assets", "cash", "equity"):
            x = cur.get(part)
            if x is not None and a > 0 and x > a * (1 + _TOL):
                sheet.problems.append(f"{part}>assets")
    li = cur.get("liabilities")
    if li is not None and li > 0:
        for part in ("current_liabilities", "noncurrent_liabilities"):
            x = cur.get(part)
            if x is not None and x > li * (1 + _TOL):
                sheet.problems.append(f"{part}>liabilities")
    if (rev := cur.get("revenue")) is not None and rev < 0:
        sheet.problems.append("revenue<0")
    parts = (cur.get("net_continuing"), cur.get("net_discontinued"), cur.get("net_profit"))
    if None not in parts:
        nc, nd, total = (float(x) for x in parts)  # type: ignore[arg-type]
        if abs(nc + nd - total) > _TOL * max(abs(total), abs(nc), 1.0):
            sheet.problems.append("net_parts")  # the form's own lines do not add up
    eps = cur.get("eps_basic")
    net = cur.get("net_profit_parent")
    net = cur.get("net_profit") if net is None else net
    shares = cur.get("shares_weighted") or cur.get("shares_outstanding")
    if (
        eps is not None
        and net is not None
        and shares
        and abs(eps) >= 0.05
        and not 0.5 <= net / shares / eps <= 2
    ):
        sheet.problems.append("eps")
    bvps = cur.get("book_value_per_share")
    eq = cur.get("equity_parent") or cur.get("equity")
    count = cur.get("shares_outstanding") or cur.get("shares_weighted")
    if bvps and eq and count and abs(bvps) >= 0.05 and not 0.5 <= eq / count / bvps <= 2:
        sheet.warnings.append("bvps")


def _year_back(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def facts_from_page(
    html: str, c: Candidate, shares_hint: float | None = None
) -> tuple[Title | None, Sheet | None, pl.DataFrame]:
    """The report's title, its selected data, and the facts they give (empty if none).

    ``shares_hint`` is the exchange's count of the company's shares (``share_hints``).
    """
    empty = pl.DataFrame(schema=FACT_SCHEMA)
    title = parse_title(html)
    if title is None:
        return title, None, empty
    sheet = parse_sheet(html, title.consolidated, shares_hint)
    if title.period_end is None:  # "obejmujący okres od do": the sheet's header may say
        title.period_start, title.period_end = sheet.period_start, sheet.period_end
    end = title.period_end
    if end is None or not sheet.values:
        return title, sheet, empty
    start = title.period_start
    if sheet.period_end is not None and sheet.period_end == end and sheet.period_start:
        start = sheet.period_start  # a quarter's form gives the cumulative months here
    vals = dict(sheet.values)
    # a total the form gives only as its two parts
    for total, parts in (
        ("liabilities", ("current_liabilities", "noncurrent_liabilities")),
        ("assets", ("current_assets", "noncurrent_assets")),
    ):
        if total not in vals and set(parts) <= vals.keys():
            a, b = vals[parts[0]], vals[parts[1]]
            vals[total] = (
                None if a[0] is None or b[0] is None else a[0] + b[0],
                None if a[1] is None or b[1] is None else a[1] + b[1],
            )
    check(vals, sheet)
    if sheet.problems:
        return title, sheet, empty
    for internal in ("net_continuing", "net_discontinued"):  # read only to check the total
        vals.pop(internal, None)
    cur = title.currency  # the first two columns; the last two are always in euros
    fid = f"{NATIVE_PREFIX}{c.geru_id}"
    rows: list[dict[str, Any]] = []
    periods: list[tuple[int, date | None, date]] = [(0, start, end)]
    if title.kind == "annual" and start is not None:
        periods.append((1, _year_back(start), _year_back(end)))  # the prior year's column
    for concept, pair in vals.items():
        if concept in SHARE_COUNTS:
            unit = "xbrli:shares"
        elif concept in PER_SHARE:
            unit = f"{cur}/xbrli:shares"
        else:
            unit = cur
        for col, p_start, p_end in periods:
            value = pair[col]
            if value is None or not math.isfinite(value):
                continue
            balance = concept in BALANCES
            ps = None if balance else p_start
            key = f"{fid}:v{PARSER_VERSION}:{concept}:{ps}:{p_end}:{unit}"
            rows.append(
                {
                    "fact_key": key,
                    "filing_id": fid,
                    "lei": None,
                    "fiscal_period_end": end,
                    "concept": concept,
                    "period_start": ps,
                    "period_end": p_end,
                    "value": float(value),
                    "unit": unit,
                    "decimals": None,
                    "known_at": c.published,
                    "isin": c.isin,
                    "geru_id": c.geru_id,
                    "form": title.form,
                    "period_kind": title.kind,
                    "parser_version": PARSER_VERSION,
                }
            )
    df = pl.DataFrame(rows, schema=FACT_SCHEMA).unique("fact_key", keep="first")
    return title, sheet, df.sort("fact_key")


# ------------------------------------------------------------------ finding the reports


def lake_candidates(root: Path, since: date = SINCE) -> list[Candidate]:
    """Main-market reports in ``bronze/reports`` that may be periodic, newest first.

    A periodic report has no running number ("/2025"); whatever the listing called it.
    """
    base = root / "bronze" / "reports"
    parts = [
        f
        for p in sorted(base.glob("date=*"))
        if p.is_dir() and date.fromisoformat(p.name[5:]) >= since
        for f in p.glob("*.parquet")
    ]
    if not parts:
        return []
    df = (
        pl.concat(
            [
                pl.read_parquet(
                    f, columns=["site", "geru_id", "isin", "report_number", "published_at"]
                )
                for f in parts
            ],
            how="diagonal_relaxed",
        )
        .filter(
            (pl.col("site") == "gpw")
            & pl.col("isin").is_not_null()
            & pl.col("report_number").str.contains(r"^\s*/")
        )
        .unique("geru_id")
        .sort("published_at", descending=True)
    )
    return [Candidate(r["geru_id"], r["isin"], r["published_at"]) for r in df.iter_rows(named=True)]


def parse_tab(html: str) -> list[tuple[int, datetime, str]]:
    """The company card's ESPI tab: (geru_id, publication time UTC, title) per report."""
    out: list[tuple[int, datetime, str]] = []
    for gid, when, _label, title in _TAB_ROW.findall(html):
        local = datetime.strptime(when, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
        out.append((int(gid), local.astimezone(UTC), re.sub(r"\s+", " ", title).strip()))
    return out


def tab_candidates(
    client: PoliteClient, isin: str, since: date = SINCE, known: set[int] | None = None
) -> list[Candidate]:
    """Periodic reports on the company card, back to ``since`` or the first known one."""
    known = known or set()
    out: list[Candidate] = []
    for page in range(MAX_TAB_PAGES):
        try:
            res = client.get(
                TAB_URL.format(isin=isin, offset=page * TAB_PAGE_SIZE), use_cache=False
            )
        except Exception as exc:
            log.info("gpw card %s page %d: %s", isin, page, exc)
            break
        rows = parse_tab(res.text())
        if not rows:
            break
        periodic = [r for r in rows if _FORM_CODE.match(r[2])]
        out += [Candidate(g, isin, ts) for g, ts, _ in periodic if ts.date() >= since]
        if min(ts for _, ts, _ in rows).date() < since:
            break
        if periodic and all(g in known for g, _, _ in periodic):
            break  # reached reports read before
        if len(rows) < TAB_PAGE_SIZE:
            break
    return out


# ------------------------------------------------------------------ the run


def share_hints(root: Path) -> dict[str, float]:
    """Each main-market company's shares issued, from the exchange's latest indicators table.

    Only the order of magnitude is used (``_check_scale``), so the latest count serves for
    reports of earlier years too.
    """
    base = root / "bronze" / indicators.TABLE
    parts = sorted(p for p in base.glob("date=*") if p.is_dir())
    for part in reversed(parts):
        files = list(part.glob("*.parquet"))
        if not files:
            continue
        df = pl.concat([pl.read_parquet(f, columns=["isin", "shares"]) for f in files])
        return {
            r["isin"]: float(r["shares"])
            for r in df.drop_nulls().iter_rows(named=True)
            if r["shares"] > 0
        }
    return {}


def _annual_gaps(state: pl.DataFrame | None, isins: Iterable[str], today: date) -> list[str]:
    """Companies with no annual report read for the last fiscal year that should have one.

    Annual reports are due four months after the year end, so from May the year before is
    expected (calendar fiscal years; others are looked up again after ``RECHECK_DAYS``).
    """
    expected = today.year - 1 if today.month >= 5 else today.year - 2
    have: set[str] = set()
    if state is not None and state.height:
        have = set(
            state.filter(pl.col("form").is_in(["RR", "SRR"]) & (pl.col("fiscal_year") >= expected))[
                "isin"
            ].to_list()
        )
    return [i for i in isins if i not in have]


PARSER_VERSION = 1  # raised when the parser changes: reports read before are read again
MAX_ATTEMPTS = 3  # a page that fails to load or parse this often is given up
# A report read on every run: if the page or the parser changes, the run fails.
CANARY = Candidate(487865, "PLOPTEM00012", datetime(2026, 4, 30, 21, 57, 29, tzinfo=UTC))
CANARY_FACTS = {"revenue": 100_223_000.0, "assets": 60_459_000.0, "eps_basic": 0.39}


def looks_like_report(html: str) -> bool:
    """Whether a page is an ESPI/EBI report page at all (any of ``ingest``'s templates)."""
    low = html.lower()
    return any(m in low for m in ('class="ntekst"', "report_data_wrap", "report-data"))


def canary(client: PoliteClient) -> str | None:
    """None if the known report still reads as it should, else what went wrong."""
    try:
        res = client.get(espi_detail_url(CANARY.geru_id), use_cache=False)
        _, _, df = facts_from_page(res.text(), CANARY)
    except Exception as exc:
        return f"canary report {CANARY.geru_id} failed: {type(exc).__name__}: {exc}"
    end = date(2025, 12, 31)
    got = {
        r["concept"]: r["value"]
        for r in df.filter(pl.col("period_end") == end).iter_rows(named=True)
    }
    wrong = {k: got.get(k) for k, v in CANARY_FACTS.items() if got.get(k) != v}
    return f"canary report {CANARY.geru_id} reads wrong: {wrong}" if wrong else None


def health(out: dict[str, Any]) -> list[str]:
    """What in a run's counts says the source or the parser broke (empty if nothing)."""
    bad: list[str] = []
    if out.get("canary"):
        bad.append(str(out["canary"]))
    pages = out["reports"] - out["errors"]
    if pages >= 20 and out["periodic"] == 0:
        bad.append(f"{pages} pages read and no periodic form recognised: has the page changed?")
    if out["periodic"] >= 20 and out["ok"] < 0.6 * out["periodic"]:
        bad.append(f"only {out['ok']} of {out['periodic']} periodic reports gave figures")
    if out["reports"] >= 8 and out["errors"] > out["reports"] / 4:
        bad.append(f"{out['errors']} of {out['reports']} pages failed to load or parse")
    return bad


def _latest_state(state: pl.DataFrame | None) -> pl.DataFrame | None:
    """One row per report: the last time it was read."""
    if state is None or not state.height:
        return state
    for col, dtype in STATE_SCHEMA.items():
        if col not in state.columns:
            state = state.with_columns(pl.lit(None, dtype=dtype).alias(col))
    return state.select(list(STATE_SCHEMA)).cast(STATE_SCHEMA).unique("geru_id", keep="last")  # type: ignore[arg-type]


def _done(r: dict[str, Any]) -> bool:
    """Whether a report needs no further reading."""
    if r["status"] == "error":
        return (r["attempts"] or 0) >= MAX_ATTEMPTS
    if r["status"] == "not_periodic":
        return True
    return (r["parser_version"] or 0) >= PARSER_VERSION


def ingest(
    client: PoliteClient,
    root: Path,
    *,
    listed: Iterable[str] = (),
    max_reports: int = 400,
    max_companies: int = 40,
    since: date = SINCE,
    now: datetime | None = None,
    check_canary: bool = True,
) -> dict[str, Any]:
    """Read new periodic reports' pages and write their facts and state.

    Candidates come from the listing in the lake; then, for listed companies (ISINs) with
    no annual report read for the last fiscal year, from the company card, at most
    ``max_companies`` a run, each again after ``RECHECK_DAYS``. New reports are read
    first, then reports read with an older ``PARSER_VERSION``, then pages that failed
    (up to ``MAX_ATTEMPTS``). ``health`` turns the returned counts into alarms.
    """
    now = now or datetime.now(UTC)
    state = _latest_state(lake.read_state(root, STATE))
    prev = {r["geru_id"]: r for r in state.iter_rows(named=True)} if state is not None else {}
    done = {g for g, r in prev.items() if _done(r)}
    lake_c = [c for c in lake_candidates(root, since) if c.geru_id not in done]
    cands = [c for c in lake_c if c.geru_id not in prev] + [c for c in lake_c if c.geru_id in prev]

    checked = lake.read_state(root, COMPANY_STATE)
    last = (
        {r["isin"]: r["checked_at"] for r in checked.iter_rows(named=True)}
        if checked is not None
        else {}
    )
    stale = now - timedelta(days=RECHECK_DAYS)
    pending = {c.isin for c in cands}
    ok_state = state.filter(pl.col("status") == "ok") if state is not None else None
    gaps = [
        i
        for i in _annual_gaps(ok_state, sorted(set(listed)), now.date())
        if i not in pending and (i not in last or last[i] < stale)
    ]
    gaps.sort(key=lambda i: (i in last, i))
    looked: list[dict[str, Any]] = []
    queued = {c.geru_id for c in cands}
    card: list[Candidate] = []
    for isin in gaps[:max_companies]:
        for c in tab_candidates(client, isin, since, set(prev)):
            if c.geru_id not in done and c.geru_id not in queued:
                card.append(c)
                queued.add(c.geru_id)
        looked.append({"isin": isin, "checked_at": now})
    cands = card + cands  # what the listing lacks comes first

    rows: list[dict[str, Any]] = []
    n_facts = 0
    hints = share_hints(root)
    for c in cands[:max_reports]:
        before = prev.get(c.geru_id)
        row: dict[str, Any] = {
            "geru_id": c.geru_id,
            "isin": c.isin,
            "form": None,
            "fiscal_year": None,
            "period_end": None,
            "published": c.published,
            "facts": 0,
            "status": "error",
            "checks": None,
            "error": None,
            "attempts": 1,
            "parser_version": PARSER_VERSION,
            "read_at": now,
        }
        try:
            res = client.get(espi_detail_url(c.geru_id), max_age_s=365 * 86400)
            if res.status >= 400:
                raise RuntimeError(f"HTTP {res.status}")
            if not looks_like_report(res.text()):
                # an error or maintenance page served as 200: never judged "not periodic"
                raise RuntimeError("not a report page")
            title, sheet, facts = facts_from_page(res.text(), c, hints.get(c.isin))
        except Exception as exc:  # recorded and retried, up to MAX_ATTEMPTS
            log.warning("gpw report %s: %s", c.geru_id, exc)
            tries = (before["attempts"] or 0) if before and before["status"] == "error" else 0
            row |= {"error": f"{type(exc).__name__}: {exc}"[:500], "attempts": tries + 1}
            rows.append(row)
            continue
        if sheet is not None and (sheet.problems or sheet.warnings):
            row["checks"] = ";".join(sheet.problems + sheet.warnings)
        if title is None:
            row["status"] = "not_periodic"
        else:
            row |= {
                "form": title.form,
                "fiscal_year": title.fiscal_year,
                "period_end": title.period_end,
            }
            if title.period_end is None:
                row["status"] = "no_period"
            elif facts.height:
                lake.append_partition(
                    root, "bronze", TABLE, c.published.date(), facts, key="fact_key"
                )
                row |= {"facts": facts.height, "status": "ok"}
                n_facts += facts.height
            elif sheet is not None and sheet.problems:
                row["status"] = "failed_checks"
            else:
                row["status"] = "no_data"
        rows.append(row)

    if rows:
        df = pl.DataFrame(rows, schema=STATE_SCHEMA)
        merged = df if state is None else pl.concat([state, df], how="vertical")
        lake.write_state(root, STATE, merged.unique("geru_id", keep="last").sort("geru_id"))
    if looked:
        df = pl.DataFrame(
            looked, schema={"isin": pl.String, "checked_at": pl.Datetime("us", "UTC")}
        )
        if checked is not None:
            checked = checked.filter(~pl.col("isin").is_in(df["isin"].implode()))
        lake.write_state(root, COMPANY_STATE, df if checked is None else pl.concat([checked, df]))

    def count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    out: dict[str, Any] = {
        "candidates": len(cands),
        "companies": len(looked),
        "reports": len(rows),
        "periodic": sum(1 for r in rows if r["form"] is not None),
        "ok": count("ok"),
        "failed_checks": count("failed_checks"),
        "no_data": count("no_data"),
        "errors": count("error"),
        "facts": n_facts,
        "canary": canary(client) if check_canary else None,
    }
    log.info("gpw periodic reports: %s", out)
    return out


def native_facts(root: Path, as_of: datetime, kinds: Iterable[str] = ("annual",)) -> pl.DataFrame:
    """Facts from the exchange's report pages known by ``as_of``, of the given period kinds.

    Only the last reading of each report counts: the facts of the parser version that read
    it last, and none at all if that reading was refused (``failed_checks``).
    """
    parts = sorted((root / "bronze" / TABLE).glob("*/*.parquet"))
    if not parts:
        return pl.DataFrame(schema=FACT_SCHEMA)
    facts = pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed")
    if "parser_version" not in facts.columns:
        facts = facts.with_columns(pl.lit(1, dtype=pl.Int64).alias("parser_version"))
    state = _latest_state(lake.read_state(root, STATE))
    if state is not None and state.height:
        good = state.filter(pl.col("status") == "ok").select("geru_id", "parser_version")
        facts = facts.join(good, on=["geru_id", "parser_version"], how="semi")
    else:  # facts written without state (tests, a lost state file): the newest reading
        newest = facts.group_by("geru_id").agg(pl.col("parser_version").max())
        facts = facts.join(newest, on=["geru_id", "parser_version"], how="semi")
    ts = pl.lit(as_of).cast(pl.Datetime("us", "UTC"))
    return facts.filter((pl.col("known_at") <= ts) & pl.col("period_kind").is_in(list(kinds)))


# ------------------------------------------------------------------ reconciliation

RECONCILED = (
    "revenue", "operating_profit", "profit_before_tax", "net_profit", "net_profit_parent",
    "operating_cash_flow", "assets", "liabilities", "equity", "equity_parent", "cash",
    "eps_basic",
)  # fmt: skip


def reconcile(root: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The exchange's figures against ESEF filings of the same fiscal year and scope.

    Two independent readings of one annual report: the form's selected data and the
    statements' iXBRL tags. Returns a summary per line (pairs compared, share within 1 %
    and 5 %) over the pairs that are one report read twice (``same_report``: total assets
    within 1 %), and the largest differences of all pairs, scope mismatches included.
    """
    facts = esef.fundamentals_as_of(root, datetime.max.replace(tzinfo=UTC))
    rows: list[dict[str, Any]] = []
    for (isin, fy), grp in facts.group_by(["isin", "fiscal_period_end"]):
        by_filing: dict[str, list[dict[str, Any]]] = {}
        for r in grp.to_dicts():
            by_filing.setdefault(r["filing_id"], []).append(r)
        pair = esef.native_and_tagged(by_filing, fy, strict=False)
        if pair is None:
            continue
        main, other = pair
        if other is None:
            continue
        strict = esef.native_and_tagged(by_filing, fy)
        same = bool(strict and strict[1] and strict[1][0]["filing_id"] == other[0]["filing_id"])
        cur = esef._currency(main)
        if cur is None:
            continue
        for concept in RECONCILED:
            x = esef._line(main, concept, fy, cur)
            y = esef._line(other, concept, fy, cur)
            if x is None or y is None:
                continue
            diff = abs(x - y) / max(abs(y), 1e-9)
            rows.append(
                {
                    "isin": isin,
                    "fiscal_year_end": fy,
                    "concept": concept,
                    "native": x,
                    "esef": y,
                    "native_filing": main[0]["filing_id"],
                    "esef_filing": other[0]["filing_id"],
                    "same_report": same,
                    "rel_diff": diff,
                }
            )
    if not rows:
        empty = pl.DataFrame(schema={"concept": pl.String})
        return empty, empty
    df = pl.DataFrame(rows)
    summary = (
        df.filter(pl.col("same_report"))
        .group_by("concept")
        .agg(
            pl.len().alias("pairs"),
            (pl.col("rel_diff") <= 0.01).mean().round(3).alias("within_1pct"),
            (pl.col("rel_diff") <= 0.05).mean().round(3).alias("within_5pct"),
        )
        .sort("concept")
    )
    return summary, df.sort("rel_diff", descending=True).head(30)
