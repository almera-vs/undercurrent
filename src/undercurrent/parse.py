"""Parse bronze report text into structured silver fields.

* Polish number and date parsers (property-tested with Hypothesis).
* Insider-transaction field extraction from the report body. The PDF notification
  forms are not retrievable (see ``docs/DECISIONS.md``), so fields come from what the
  issuer wrote in ``Treść raportu``. A field the text doesn't state is ``None`` and the
  reason is recorded in ``parse_notes``. Nothing is inferred or guessed.
* :func:`build_silver_reports` turns ``bronze/reports`` into ``silver/report_fields``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from undercurrent import lake
from undercurrent.classify import classify, normalize

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- numbers

_NUM_TOKEN = re.compile(r"[-−]?\d[\d\s\xa0 .,']*")


def parse_pl_number(text: str | None) -> float | None:
    """Parse a number written Polish- or English-style.

    Accepts ``1 234 567,89``, ``1.234.567,89``, ``1234567.89``, ``1,234,567.89``,
    ``12,5``, ``-3``. Returns ``None`` when the text is not a single number.
    """
    if text is None:
        return None
    s = text.strip().replace("−", "-")
    s = re.sub(r"\s*(zł|zl|pln|szt\.?|%)$", "", s, flags=re.I).strip()
    if not s or not re.fullmatch(r"-?\d[\d\s\xa0 .,']*", s):
        return None
    neg = s.startswith("-")
    s = re.sub(r"[\s\xa0 ']", "", s.lstrip("-"))
    if not s:
        return None
    has_c, has_d = "," in s, "." in s
    if has_c and has_d:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif has_c or has_d:
        sep = "," if has_c else "."
        parts = s.split(sep)
        if len(parts) > 2:
            # several separators: they are thousands groupings (1.234.567)
            if not all(len(p) == 3 for p in parts[1:]):
                return None
            s = "".join(parts)
        elif sep == ",":
            s = s.replace(",", ".")  # Polish decimal comma (1,234 means 1.234)
        elif len(parts[1]) == 3 and len(parts[0]) <= 3 and parts[0] != "0":
            s = "".join(parts)  # 1.234 → thousands grouping in Polish texts
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


# --------------------------------------------------------------------------- dates

PL_MONTHS = {
    "stycznia": 1,
    "styczen": 1,
    "luty": 2,
    "lutego": 2,
    "marca": 3,
    "marzec": 3,
    "kwietnia": 4,
    "kwiecien": 4,
    "maja": 5,
    "maj": 5,
    "czerwca": 6,
    "czerwiec": 6,
    "lipca": 7,
    "lipiec": 7,
    "sierpnia": 8,
    "sierpien": 8,
    "wrzesnia": 9,
    "wrzesien": 9,
    "pazdziernika": 10,
    "pazdziernik": 10,
    "listopada": 11,
    "listopad": 11,
    "grudnia": 12,
    "grudzien": 12,
}
_D_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_D_DMY = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_D_WORDS = re.compile(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b")


def parse_pl_date(text: str | None) -> date | None:
    """Parse the first date in ``text``: ISO, ``DD.MM.YYYY`` or ``5 listopada 2026``."""
    if not text:
        return None
    t = normalize(text)
    found: list[tuple[int, date]] = []
    for rx, order in ((_D_ISO, "ymd"), (_D_DMY, "dmy")):
        for m in rx.finditer(t):
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                found.append((m.start(), date(y, mo, d)))
            except ValueError:
                continue
    for m in _D_WORDS.finditer(t):
        month = PL_MONTHS.get(m.group(2))
        if month is None:
            continue
        try:
            found.append((m.start(), date(int(m.group(3)), month, int(m.group(1)))))
        except ValueError:
            continue
    return min(found)[1] if found else None


# --------------------------------------------------------------------------- insider fields

ROLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("closely_associated", re.compile(r"blisko (zwiazan|powiazan)|osob\w* zwiazan\w* z")),
    ("CEO", re.compile(r"\bprezes\w* zarzadu|\bprezes\b(?! rady)|\bceo\b")),
    ("CFO", re.compile(r"dyrektor\w* finansow|\bcfo\b|czlon\w* zarzadu ds\.? finans")),
    ("supervisory_board", re.compile(r"rad\w* nadzorcz")),
    ("board", re.compile(r"wiceprezes\w*|czlon\w* zarzadu|zarzadu|prokurent")),
    # Last resort: the text says "a PDMR" without naming the function.
    ("pdmr", re.compile(r"pelni\w* obowiazk\w* zarzadcz")),
)
_UP = "A-ZŁŚŻŹĆŃÓĘĄ"
_LO = "a-ząćęłńóśźżäöüéèáíúçãõ"
_PERSON = re.compile(
    r"(?<![\w-])(?i:pana|pani|panem|panią|pania)\s+"
    rf"(?P<name>[{_UP}][\w{_LO}-]+(?:\s+[{_UP}][\w{_LO}-]+){{1,2}})"
)
_PERSON_COMPANY = re.compile(
    r"\b(?i:od|przez)\s+(?:(?i:spolki|spółki|podmiotu)\s+)?"
    rf"(?P<name>[{_UP}0-9][^,;()\n]{{0,60}}?"
    r"\s(?:S\.A\.|SA|[Ss]p\. z o\.o\.|S\.K\.A\.|AG|N\.V\.|B\.V\.|Ltd\.?|Limited|SE|"
    r"ASI S\.A\.|SCA|S\.C\.A\.|GmbH|LLC|Inc\.|plc|PLC))"
    r"(?=[\s,;.)]|$)"
)
_PERSON_TJ = re.compile(
    r"\b(?i:tj\.|to jest)\s+(?!Pan|Pani)"
    rf"(?P<name>[{_UP}][{_LO}-]+\s+[{_UP}][{_LO}-]+)\b"
)
# "członka Rady Nadzorczej Spółki Konrada Miterskiego" (a name right after the role).
_PERSON_ROLE = re.compile(
    r"(?i:członk\w*|czlonk\w*|prezes\w*|wiceprezes\w*|przewodnicząc\w*|przewodniczac\w*)\s+"
    r"(?i:rady nadzorczej|zarządu|zarzadu)(?:\s+(?i:spółki|spolki|emitenta|banku))?,?\s+"
    r"(?!Pan|Pani|Spółk|Spolk|Emitent)"
    rf"(?P<name>[{_UP}][{_LO}-]+\s+[{_UP}][{_LO}-]+)(?=[\s,.;)]|$)"
)
# Foreign names after "od", ended by the notice itself:
# "od Annibala Jose Da Cunha Saraiva Soaresa powiadomienia".
_PERSON_OD = re.compile(
    r"\b(?i:od)\s+(?!Pan|Pani|Spółk|Spolk|Emitent|Prezes|Członk|Czlonk|Przewodnicz|Zarz|Rad[ay])"
    rf"(?P<name>[{_UP}][\w{_LO}-]+(?:\s+(?:[{_UP}][\w{_LO}-]+|da|de|do|dos|du|di|van|von|der|la|le)){{1,6}})"
    r"\s+(?=(?i:powiadomie|zawiadomie|informacj))"
)
_PERSON_RULES = (_PERSON_COMPANY, _PERSON, _PERSON_TJ, _PERSON_ROLE, _PERSON_OD)
_PLEDGE = re.compile(r"\bzastaw\w*")
_GIFT = re.compile(r"\bdarowizn\w*")
_INCENTIVE = re.compile(r"\bwarrant\w*|program\w* motywacyjn\w*|opcj\w* menedzersk\w*")
_BUY = re.compile(r"\b(nabyci\w*|nabyl\w*|zakup\w*|kupn\w*|objeci\w* akcji|subskrypc\w*)")
_SELL = re.compile(r"\b(zbyci\w*|zbyl\w*|sprzedaz\w*|sprzedal\w*)")
# Up to three words may sit between the count and "akcji" ("1.000.000 nowo emitowanych akcji").
_VOLUME = re.compile(
    r"(?<![\d.,])(?P<n>\d{1,3}(?:[\s\xa0.]\d{3})+|\d+)\s+(?:[a-z]+\s+){0,3}?"
    r"(?:szt\.?|sztuk\w*|akcj\w*)"
)
_PRICE = re.compile(
    r"(?:sredni\w*\s+)?cen\w*\s+(?:jednostkow\w*\s+)?(?:wynosz\w*\s+|w wysokosci\s+|po\s+)?"
    r"(?P<p>\d[\d\s\xa0]*(?:[.,]\d+)?)\s*(?:zl|pln)"
)
_PRICE_PO = re.compile(r"\bpo\s+(?:cenie\s+)?(?P<p>\d[\d\s\xa0]*[.,]\d+)\s*(?:zl|pln)")
_VALUE = re.compile(
    r"(?:laczn\w*\s+)?wartos\w*\s+(?:transakcji\s+|transakcj\w*\s+)?(?:wynosi\w*\s+|wyniosl\w*\s+)?"
    r"(?:w wysokosci\s+)?(?P<v>\d[\d\s\xa0.]*(?:,\d+)?)\s*(?:zl|pln)"
)
_TRADE_DATE = re.compile(
    r"(?:transakcj\w*|nabyci\w*|zbyci\w*|objeci\w*|dokonan\w*)[^.]{0,80}?\bw dniu\s+"
    r"(?P<d>[^,;()]{6,25})"
)
_VENUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "off_market",
        re.compile(r"poza (obrotem zorganizowanym|systemem obrotu|rynkiem regulowanym)"),
    ),
    ("block_trade", re.compile(r"transakcj\w* pakietow")),
    ("NewConnect", re.compile(r"newconnect|alternatywn\w+ system\w* obrotu")),
    ("GPW", re.compile(r"gield\w* papierow wartosciowych|\bgpw\b|rynk\w* regulowan|xwar")),
)


@dataclass
class InsiderFields:
    """Insider-transaction fields parsed from report text; ``None`` = not stated."""

    person: str | None = None
    role: str | None = None
    side: str | None = None  # buy | sell | mixed | pledge
    volume: float | None = None
    avg_price: float | None = None
    value: float | None = None
    trade_date: date | None = None
    venue: str | None = None
    pct_free_float: float | None = None  # needs free-float data we don't have: always None
    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        """Flat dict with ``insider_`` prefixes, for silver."""
        d = asdict(self)
        notes = d.pop("notes")
        out = {f"insider_{k}": v for k, v in d.items()}
        out["insider_parse_notes"] = "; ".join(notes) or None
        return out


def extract_insider_fields(title: str | None, body: str | None) -> InsiderFields:
    """Pull insider-transaction fields from the title and body of an Art. 19 MAR report."""
    f = InsiderFields()
    raw = f"{title or ''}\n{body or ''}"
    norm = normalize(raw)
    if not body:
        f.notes.append("no body text")
    # The notifier is the earliest person/entity introduced by "od"/"przez"/"tj.".
    hits = [m for rx in _PERSON_RULES if (m := rx.search(raw))]
    m = min(hits, key=lambda h: h.start()) if hits else None
    if m:
        f.person = m.group("name").strip()
    else:
        f.notes.append("person not named")
    for role, rx in ROLE_RULES:
        if rx.search(norm):
            f.role = role
            break
    if f.role is None:
        f.notes.append("role not stated")
    buy, sell = bool(_BUY.search(norm)), bool(_SELL.search(norm))
    f.side = "mixed" if buy and sell else "buy" if buy else "sell" if sell else None
    # Gifts and incentive-plan grants are not market trades, whatever verbs appear.
    if _GIFT.search(norm):
        f.side = "gift"
    elif _INCENTIVE.search(norm):
        f.side = "incentive"
    if f.side is None and _PLEDGE.search(norm):
        f.side = "pledge"
    if f.side is None:
        f.notes.append("side not stated (details only in attachment)")
    if (m := _VOLUME.search(norm)) is not None:
        f.volume = parse_pl_number(m.group("n"))
    if (m := _PRICE.search(norm) or _PRICE_PO.search(norm)) is not None:
        f.avg_price = parse_pl_number(m.group("p"))
    if (m := _VALUE.search(norm)) is not None:
        f.value = parse_pl_number(m.group("v"))
    if f.volume is None and f.avg_price is None and f.value is None:
        f.notes.append("volume/price/value not in text")
    # Only the body: titles often start with "Transakcje …" and would anchor on the
    # date the issuer *received* the notice instead of the trade date.
    body_norm = re.sub(r"tj\.", "tj", normalize(body))  # "tj." must not end the clause
    if (m := _TRADE_DATE.search(body_norm)) is not None:
        f.trade_date = parse_pl_date(m.group("d"))
    for venue, rx in _VENUES:
        if rx.search(norm):
            f.venue = venue
            break
    return f


# --------------------------------------------------------------------------- silver

SILVER_COLUMNS: dict[str, Any] = {
    "report_id": pl.String,
    "signal_type": pl.String,
    "subtype": pl.String,
    "class_rule": pl.String,
    "insider_person": pl.String,
    "insider_role": pl.String,
    "insider_side": pl.String,
    "insider_volume": pl.Float64,
    "insider_avg_price": pl.Float64,
    "insider_value": pl.Float64,
    "insider_trade_date": pl.Date,
    "insider_venue": pl.String,
    "insider_pct_free_float": pl.Float64,
    "insider_parse_notes": pl.String,
    "insider_source": pl.String,
    "insider_form_url": pl.String,
}


def parse_report_row(row: dict[str, Any]) -> dict[str, Any]:
    """Classification + insider fields for one bronze report row."""
    c = classify(
        row.get("legal_basis"),
        row.get("title") or row.get("subject"),
        row.get("body_text"),
        row.get("kind"),
    )
    out: dict[str, Any] = {
        "report_id": row["report_id"],
        "signal_type": c.signal_type,
        "subtype": c.subtype,
        "class_rule": c.rule,
    }
    if c.signal_type == "insider_tx":
        out |= extract_insider_fields(
            row.get("title") or row.get("subject"), row.get("body_text")
        ).as_row()
        out["insider_source"] = "text"
    return out


def role_of(text: str | None) -> str | None:
    """Role key for a free-text position, e.g. ``Członek Rady Nadzorczej``."""
    n = normalize(text)
    for role, rx in ROLE_RULES:
        if rx.search(n):
            return role
    return None


def apply_forms(row: dict[str, Any], form_rows: list[dict[str, Any]], isin: str | None) -> None:
    """Overlay fields from the notification form on the text-derived insider fields.

    The form is the primary record: where it states a field, it wins. Where it does not
    (or no form was found), the value parsed from the report text stays.
    """
    from undercurrent.forms import forms_from_rows, summarise

    s = summarise(forms_from_rows(form_rows), isin)
    pairs = {
        "insider_person": s["form_person"],
        "insider_role": role_of(s["form_position"]),
        "insider_side": s["form_side"],
        "insider_volume": s["form_volume"],
        "insider_avg_price": s["form_avg_price"],
        "insider_value": s["form_value"],
        "insider_trade_date": s["form_trade_date"],
        "insider_venue": s["form_venue"],
    }
    for k, v in pairs.items():
        if v is not None:
            row[k] = v
    row["insider_source"] = f"form:{form_rows[0]['portal']}"
    row["insider_form_url"] = form_rows[0]["page_url"]


def build_silver_reports(root: Path, work: Path) -> pl.DataFrame | None:
    """Rebuild ``{work}/silver/report_fields.parquet`` from all bronze reports (deterministic)."""
    bronze = lake.read_table(root, "bronze", "reports")
    if bronze is None:
        return None
    bronze = bronze.sort(["report_id", "ingested_at"]).unique("report_id", keep="last")
    rows = [parse_report_row(r) for r in bronze.iter_rows(named=True)]
    forms_df = lake.read_table(root, "bronze", "insider_forms")
    if forms_df is not None and forms_df.height:
        by_report: dict[str, list[dict[str, Any]]] = {}
        for fr in forms_df.sort("report_id", "pdf_index", "txn_index").iter_rows(named=True):
            by_report.setdefault(fr["report_id"], []).append(fr)
        isins = dict(bronze.select("report_id", "isin").iter_rows())
        for row in rows:
            if row["signal_type"] == "insider_tx" and row["report_id"] in by_report:
                apply_forms(row, by_report[row["report_id"]], isins.get(row["report_id"]))
    df = pl.DataFrame(rows, schema=SILVER_COLUMNS).sort("report_id")
    lake.write_frame(df, work / "silver" / "report_fields.parquet")
    log.info(
        "silver report_fields: %d rows; %s",
        df.height,
        dict(df.group_by("signal_type").len().iter_rows()),
    )
    return df
