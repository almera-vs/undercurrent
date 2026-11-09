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
    ("closely_associated", re.compile(r"blisko zwiazan|osob\w* zwiazan\w* z")),
    ("CEO", re.compile(r"\bprezes\w* zarzadu|\bprezes\b(?! rady)|\bceo\b")),
    ("CFO", re.compile(r"dyrektor\w* finansow|\bcfo\b|czlon\w* zarzadu ds\.? finans")),
    ("supervisory_board", re.compile(r"rad\w* nadzorcz")),
    ("board", re.compile(r"wiceprezes\w*|czlon\w* zarzadu|zarzadu|prokurent")),
)
_PERSON = re.compile(
    r"\b(?i:od|przez)\s+(?i:pana|pani|panem|panią|pania)\s+"
    r"(?P<name>[A-ZŁŚŻŹĆŃÓĘĄ][\wąćęłńóśźż-]+(?:\s+[A-ZŁŚŻŹĆŃÓĘĄ][\wąćęłńóśźż-]+){1,2})"
)
_BUY = re.compile(r"\b(nabyci\w*|nabyl\w*|zakup\w*|kupn\w*|objeci\w* akcji|subskrypc\w*)")
_SELL = re.compile(r"\b(zbyci\w*|zbyl\w*|sprzedaz\w*|sprzedal\w*)")
_VOLUME = re.compile(r"(?P<n>\d[\d\s\xa0.]*)\s*(?:szt\.?|sztuk|akcji|akcje)\b")
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
    r"(?:transakcj\w*|nabyci\w*|zbyci\w*)[^.]{0,80}?\bw dniu\s+(?P<d>[^,;()]{6,25})"
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
    side: str | None = None  # buy | sell | mixed
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
    m = _PERSON.search(raw)
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
    if (m := _TRADE_DATE.search(normalize(body))) is not None:
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
    return out


def build_silver_reports(root: Path) -> pl.DataFrame | None:
    """Rebuild ``silver/report_fields.parquet`` from all bronze reports (deterministic)."""
    bronze = lake.read_table(root, "bronze", "reports")
    if bronze is None:
        return None
    bronze = bronze.sort(["report_id", "ingested_at"]).unique("report_id", keep="last")
    rows = [parse_report_row(r) for r in bronze.iter_rows(named=True)]
    df = pl.DataFrame(rows, schema=SILVER_COLUMNS).sort("report_id")
    lake.write_frame(df, root / "silver" / "report_fields.parquet")
    log.info(
        "silver report_fields: %d rows; %s",
        df.height,
        dict(df.group_by("signal_type").len().iter_rows()),
    )
    return df
