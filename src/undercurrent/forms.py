"""MAR Art. 19 notification forms: parse the PDF text and collect forms for insider reports.

The form is a fixed EU template (Commission Implementing Regulation 2016/523), filed in
Polish and sometimes with an English translation:

* 1a name of the person; 2a position/status; 2b initial notification or amendment;
* 3a issuer name; 3b LEI;
* 4 one block per instrument, transaction type, date and venue: a) instrument and ISIN,
  b) nature of the transaction, c) price/volume rows, d) aggregated volume and price,
  e) date, f) place.

Parsing works on the text layer that pdfplumber extracts; no OCR. Fields the form does not
state stay ``None``. The PDFs are fetched from portal copies (:mod:`undercurrent.portals`),
read in memory and never stored.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pdfplumber
import polars as pl

from undercurrent import lake
from undercurrent.classify import classify, normalize
from undercurrent.fetch import PoliteClient
from undercurrent.parse import parse_pl_date, parse_pl_number
from undercurrent.portals import PORTALS, Candidate, ReportRef, best_match

log = logging.getLogger(__name__)

TABLE = "insider_forms"
PENDING_STATE = "insider_forms_pending"
MAX_ATTEMPT_DAYS = 14  # portals publish within hours; give up after two weeks
MAX_PDFS_PER_REPORT = 12

_ISIN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")
_NUM = r"-?\d[\d\s\xa0]*(?:[.,]\d+)?"
_CCY = r"(?:PLN|EUR|USD|GBP|CHF|CZK|zł|zl)"
_ROW = re.compile(rf"^(?P<p>{_NUM})\s*(?P<c>{_CCY})?\s+(?P<v>{_NUM})\s*(?:{_CCY})?$", re.I)
_PAGE = re.compile(r"^(strona|page)\s+\d+\s+(z|of)\s+\d+$|^translation of the formal", re.I)


@dataclass
class Transaction:
    """One section-4 block of a form."""

    instrument: str | None = None
    isin: str | None = None
    nature: str | None = None
    side: str | None = None
    rows: list[tuple[float, float]] = field(default_factory=list)  # (price, volume)
    volume: float | None = None
    price: float | None = None
    currency: str | None = None
    trade_date: date | None = None
    venue: str | None = None
    venue_text: str | None = None


@dataclass
class Form:
    """A parsed notification form."""

    lang: str  # pl | en
    person: str | None = None
    position: str | None = None
    amendment: bool | None = None
    issuer: str | None = None
    lei: str | None = None
    transactions: list[Transaction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- mapping


_SIDES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pledge", re.compile(r"zastaw|pledge")),
    ("gift", re.compile(r"darowizn|gift|donation")),
    ("incentive", re.compile(r"warrant|opcj|option|program\w* motywacyjn|incentive|grant")),
    ("sell", re.compile(r"zbyci|sprzeda|\bsale\b|\bsell|dispos")),
    ("buy", re.compile(r"nabyci|kupn|zakup|objeci|subskrypc|purchase|acquisition|subscri")),
)
_VENUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("off_market", re.compile(r"poza|outside|off.?market|pozagield|nie dotyczy|n/a")),
    ("block_trade", re.compile(r"pakiet|block")),
    ("NewConnect", re.compile(r"xnco|newconnect|alternatywn")),
    ("GPW_derivatives", re.compile(r"wder|derywat|derivativ")),
    ("GPW", re.compile(r"xwar|gield\w* papierow|warsaw stock exchange|\bgpw\b")),
)


def side_of(nature: str | None) -> str | None:
    """``Nabycie`` → ``buy``; ``Ustanowienie zastawu`` → ``pledge``; unknown → None."""
    n = normalize(nature)
    if not n:
        return None
    for side, rx in _SIDES:
        if rx.search(n):
            return side
    return None


def venue_of(text: str | None) -> str | None:
    """Normalised trading venue for the place-of-transaction field."""
    n = normalize(text)
    if not n:
        return None
    for venue, rx in _VENUES:
        if rx.search(n):
            return venue
    return "other"


# --------------------------------------------------------------------------- parsing
# The form is a two-column table. pdfplumber keeps the reading order of each cell, but long
# cells wrap, so a value can sit on the line before *and* after its label. Parsing therefore
# works on regions between labels rather than on "label: value" lines.

_I = re.I
_FORM_START = re.compile(r"powiadomienie o transakcji|notification (?:on|about|of) transaction", _I)
_L_PERSON = re.compile(r"nazwa\s*/\s*nazwisko|entity name\s*/\s*last name|\ba\)\s*name\b", _I)
_L_REASON = re.compile(
    r"pow[óo]d powiadomienia|reason for (?:the |submitting the )?notification", _I
)
_L_POSITION = re.compile(
    r"(?:a\)\s*)?(?:stanowisko\s*/\s*status|position\s*/\s*status|job title\s*/\s*status)", _I
)
_L_AMEND = re.compile(
    r"(?:pierwotne powiadomienie|powiadomienie pierwotne)\s*/\s*zmiana"
    r"|(?:initial|original) notification\s*/\s*(?:change|amendment)",
    _I,
)
_L_SECTION3 = re.compile(r"dane emitenta|details of the issuer|data of the issuer", _I)
_L_ISSUER = re.compile(r"a\)\s*(?:nazwa|entity name|name)\b", _I)
_L_LEI = re.compile(r"\blei\b", _I)
_L_BLOCK = re.compile(r"opis instrumentu finansowego|description of the financial", _I)
_L_CODE = re.compile(r"kod identyfikacyjny|identification code", _I)
_L_PRICES = re.compile(
    r"c\)\s*(?:cena i wolumen|price\(s\) and volume\(s\)|price and volume)"
    r"|cena\s+wolumen|price\s+volume",
    _I,
)
_L_AGG = re.compile(
    r"informacje zbiorcze|aggregated? information|[łl][aą]czny wolumen|total volume"
    r"|aggregated volume",
    _I,
)
_L_AGG_VOL = re.compile(
    rf"(?:[łl][aą]czny wolumen|aggregated volume|total volume)\s*:?\s*(?P<n>{_NUM})", _I
)
_L_AGG_PRICE = re.compile(
    rf"(?:^|\n)\s*(?:d\)\s*)?[-–—]?\s*(?:cena|price)\s*:?\s*(?P<p>{_NUM})\s*(?P<c>{_CCY})?", _I
)
_L_DATE = re.compile(r"data transakcji|date of the transaction|transaction date", _I)
_L_VENUE = re.compile(r"miejsce transakcji|place of the transaction|trading venue", _I)
_TAIL_STOP = re.compile(r"niepotrzebne skre|delete as appropriate|data sporz|wype[łl]ni[ćc] je", _I)
_LABEL_NOISE = re.compile(
    r"\b[a-f]\)|rodzaj instrumentu|instrument type|type of instrument|kod identyfikacyjny"
    r"|identification code|rodzaj transakcji|nature of the transaction|transaction type",
    _I,
)


def _lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        ln = re.sub(r"[ \t\xa0]+", " ", raw).strip()
        if ln and not _PAGE.search(ln):
            out.append(ln)
    return out


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    t = re.sub(r"\s+", " ", text).strip(" ,;:-–—*")
    return t or None


def _date(text: str | None) -> date | None:
    if not text:
        return None
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.search(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}\b", text)
    return parse_pl_date(m.group(0) if m else text)


def _parse_block(block: str) -> Transaction:
    t = Transaction()
    isin = _ISIN.search(block)
    if isin is not None:
        t.isin = isin.group(0)
    code = _L_CODE.search(block)
    prices = _L_PRICES.search(block)
    # instrument: from the block label to the identification code
    inst = block[: code.start() if code else (prices.start() if prices else len(block))]
    inst = _LABEL_NOISE.sub(" ", _L_BLOCK.sub(" ", inst))
    inst = re.sub(r"(finansowego|instrument)\s*,?\s*(rodzaj)?", " ", inst, flags=_I)
    if t.isin:
        inst = inst.replace(t.isin, " ")
    t.instrument = _clean(re.sub(r"\bISIN\s*:?", " ", inst))
    # nature: between the identification code and the price table
    head_end = code.end() if code else (isin.end() if isin else 0)
    nat = block[head_end : prices.start() if prices else len(block)]
    t.nature = _clean(_LABEL_NOISE.sub(" ", nat))
    t.side = side_of(t.nature)
    # price/volume rows: between the price label and the aggregated section
    agg = _L_AGG.search(block, prices.end() if prices else 0)
    rows_text = block[prices.end() if prices else 0 : agg.start() if agg else len(block)]
    for ln in rows_text.splitlines():
        ln = _L_PRICES.sub(" ", ln)
        ln = re.sub(r"^\s*(c\)|cena|price|wolumen|volume)\s*", "", ln, flags=_I).strip()
        row = _ROW.match(ln)
        if row is not None:
            p, v = parse_pl_number(row["p"]), parse_pl_number(row["v"])
            if p is not None and v is not None:
                t.rows.append((p, v))
                t.currency = t.currency or (row["c"] or None)
    agg_text = block[agg.start() :] if agg else ""
    vol = _L_AGG_VOL.search(agg_text)
    if vol is not None:
        t.volume = parse_pl_number(vol["n"])
    price = _L_AGG_PRICE.search(agg_text)
    if price is not None:
        t.price = parse_pl_number(price["p"])
        t.currency = t.currency or (price["c"] or None)
    if t.volume is None and t.rows:
        t.volume = sum(v for _, v in t.rows)
    if t.price is None and t.rows and t.volume:
        t.price = sum(p * v for p, v in t.rows) / t.volume
    dlabel = _L_DATE.search(block)
    if dlabel is not None:
        t.trade_date = _date(block[dlabel.end() : dlabel.end() + 60])
        tail = block[dlabel.end() :]
        stop = _TAIL_STOP.search(tail)
        tail = tail[: stop.start()] if stop else tail
        tail = re.sub(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", " ", _L_VENUE.sub(" ", tail))
        t.venue_text = _clean(re.sub(r"\bf\)", " ", tail))
        t.venue = venue_of(t.venue_text)
    if t.currency and normalize(t.currency) in ("zl", "zł"):
        t.currency = "PLN"
    return t


def _parse_one(text: str) -> Form | None:
    head = normalize(text[:400])
    if "19" not in head or not re.search(r"\bmar\b|market abuse", head):
        return None
    en = bool(re.search(r"notification (on|about|of) transaction", head))
    f = Form(lang="en" if en else "pl")
    m = _L_PERSON.search(text)
    if m is not None:
        end = _L_REASON.search(text, m.end())
        raw = text[m.end() : end.start() if end else m.end() + 200]
        f.person = _clean(re.sub(r"(?m)^\s*\d\s*$|\s\d\s*$", " ", raw))
    m = _L_REASON.search(text)
    if m is not None:
        end = _L_AMEND.search(text, m.end())
        raw = text[m.end() : end.start() if end else m.end() + 300]
        f.position = _clean(re.sub(r"\bb\)\s*$", " ", _L_POSITION.sub(" ", raw)))
    m = _L_AMEND.search(text)
    if m is not None:
        val = normalize(text[m.end() : m.end() + 80].split("\n")[0])
        f.amendment = bool(re.search(r"zmian|korekt|change|amend", val)) if val else None
    s3 = _L_SECTION3.search(text)
    if s3 is not None:
        m = _L_ISSUER.search(text, s3.end())
        if m is not None:
            lei = _L_LEI.search(text, m.end())
            raw = text[m.end() : lei.start() if lei else m.end() + 200]
            f.issuer = _clean(re.sub(r"\bb\)\s*$", " ", raw))
            if lei is not None:
                code = re.search(r"\b[0-9A-Z]{20}\b", text[lei.end() : lei.end() + 80])
                f.lei = code.group(0) if code else None
    starts = [b.start() for b in _L_BLOCK.finditer(text)]
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(text)
        f.transactions.append(_parse_block(text[s:e]))
    if not f.transactions:
        f.notes.append("no transaction blocks found")
    if f.person is None:
        f.notes.append("person not found")
    return f


def parse_forms(text: str) -> list[Form]:
    """All notification forms in one PDF's text (a PDF may hold several)."""
    clean = "\n".join(_lines(text))
    starts = [m.start() for m in _FORM_START.finditer(clean)] or [0]
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(clean)
        form = _parse_one(clean[s:e])
        if form is not None:
            out.append(form)
    return out


def parse_form(text: str) -> Form | None:
    """The first notification form in ``text`` (``None`` if it is not one)."""
    forms = parse_forms(text)
    return forms[0] if forms else None


def pdf_text(content: bytes) -> str:
    """Text layer of a PDF (all pages)."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


# --------------------------------------------------------------------------- report summary


def summarise(forms: list[Form], isin: str | None) -> dict[str, Any]:
    """Report-level insider fields from all forms of one report.

    Polish forms win over English translations of the same filing. When a report carries
    corrected forms ("zmiana"), the last correction per person replaces the rest. Only
    transactions in the issuer's shares count (matching ISIN, or a "shares" instrument with
    no ISIN); derivatives, bonds and emission allowances stay in bronze but do not drive the
    summary.
    """
    pl_forms = [f for f in forms if f.lang == "pl"]
    use = pl_forms or forms
    if any(f.amendment for f in use):
        last: dict[str, Form] = {}
        for f in use:
            if f.amendment:
                last[normalize(f.person)] = f
        use = list(last.values())

    def is_share(t: Transaction) -> bool:
        if isin and t.isin:
            return t.isin == isin
        return bool(re.search(r"akcj|share", normalize(t.instrument)))

    txs = [t for f in use for t in f.transactions if is_share(t)]
    sides = {t.side for t in txs if t.side}
    if not txs and {t.side for f in use for t in f.transactions} == {"incentive"}:
        sides = {"incentive"}
    if sides <= {"buy"} and sides:
        side = "buy"
    elif sides <= {"sell"} and sides:
        side = "sell"
    elif sides <= {"buy", "sell"} and sides:
        side = "mixed"
    elif len(sides) == 1:
        side = next(iter(sides))
    else:
        side = None
    main = [t for t in txs if t.side == side] if side in ("buy", "sell") else txs
    vol = sum(t.volume or 0 for t in main) or None
    value = sum((t.volume or 0) * (t.price or 0) for t in main) or None
    price = value / vol if value and vol else None
    dates = [t.trade_date for t in main if t.trade_date]
    venues = [t.venue for t in main if t.venue]
    return {
        "form_person": use[0].person if use else None,
        "form_position": use[0].position if use else None,
        "form_side": side,
        "form_volume": vol,
        "form_avg_price": price,
        "form_value": value,
        "form_trade_date": min(dates) if dates else None,
        "form_venue": max(set(venues), key=venues.count) if venues else None,
        "form_amendment": any(f.amendment for f in use) if use else None,
        "form_n": len(use),
    }


def forms_from_rows(rows: list[dict[str, Any]]) -> list[Form]:
    """Rebuild :class:`Form` objects from stored ``insider_forms`` rows of one report."""
    out: dict[tuple[Any, ...], Form] = {}
    for r in sorted(rows, key=lambda r: (r["pdf_index"], r["txn_index"])):
        key = (r["pdf_index"], r["person"], r["lang"], r["amendment"])
        f = out.get(key)
        if f is None:
            f = out[key] = Form(
                lang=r["lang"],
                person=r["person"],
                position=r["position"],
                amendment=r["amendment"],
                issuer=r["form_issuer"],
                lei=r["lei"],
            )
        if r["instrument"] is not None or r["side"] is not None or r["volume"] is not None:
            f.transactions.append(
                Transaction(
                    instrument=r["instrument"],
                    isin=r["isin"],
                    nature=r["nature"],
                    side=r["side"],
                    volume=r["volume"],
                    price=r["price"],
                    currency=r["currency"],
                    trade_date=r["trade_date"],
                    venue=r["venue"],
                    venue_text=r["venue_text"],
                )
            )
    return list(out.values())


# --------------------------------------------------------------------------- collection

FORM_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "portal": pl.String,
    "page_url": pl.String,
    "pdf_url": pl.String,
    "pdf_sha256": pl.String,
    "pdf_index": pl.Int32,
    "txn_index": pl.Int32,
    "lang": pl.String,
    "person": pl.String,
    "position": pl.String,
    "amendment": pl.Boolean,
    "form_issuer": pl.String,
    "lei": pl.String,
    "instrument": pl.String,
    "isin": pl.String,
    "nature": pl.String,
    "side": pl.String,
    "volume": pl.Float64,
    "price": pl.Float64,
    "currency": pl.String,
    "trade_date": pl.Date,
    "venue": pl.String,
    "venue_text": pl.String,
    "n_rows": pl.Int32,
    "parse_notes": pl.String,
    "known_at": pl.Datetime("us", "UTC"),
    "fetched_at": pl.Datetime("us", "UTC"),
}
PENDING_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "pub_date": pl.Date,
    "attempts": pl.Int32,
    "first_tried": pl.Datetime("us", "UTC"),
    "last_tried": pl.Datetime("us", "UTC"),
    "last_error": pl.String,
}


@dataclass
class FormStats:
    """Counters for one collection run."""

    wanted: int = 0
    matched: int = 0
    forms: int = 0
    written: int = 0
    missing: int = 0
    by_portal: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Plain dict for logging."""
        return asdict(self)


def _form_rows(
    report: dict[str, Any], cand: Candidate, pdfs: list[tuple[str, bytes]], now: datetime
) -> tuple[list[dict[str, Any]], list[Form]]:
    rows: list[dict[str, Any]] = []
    forms: list[Form] = []
    for i, (url, content) in enumerate(pdfs):
        try:
            text = pdf_text(content)
        except Exception as exc:  # broken PDF: note it, keep going
            log.info("pdf %s unreadable: %s", url, exc)
            continue
        parsed = parse_forms(text)
        if not parsed:
            continue  # not a notification form (cover letter, other attachment)
        forms.extend(parsed)
        sha = hashlib.sha256(content).hexdigest()
        j = 0
        for f in parsed:
            base = {
                "report_id": report["report_id"],
                "portal": cand.portal,
                "page_url": cand.url,
                "pdf_url": url,
                "pdf_sha256": sha,
                "pdf_index": i,
                "lang": f.lang,
                "person": f.person,
                "position": f.position,
                "amendment": f.amendment,
                "form_issuer": f.issuer,
                "lei": f.lei,
                "parse_notes": "; ".join(f.notes) or None,
                "known_at": report["known_at"],
                "fetched_at": now,
            }
            for t in f.transactions or [Transaction()]:
                rows.append(_txn_row(base, j, t))
                j += 1
    return rows, forms


def _txn_row(base: dict[str, Any], j: int, t: Transaction) -> dict[str, Any]:
    return {
        **base,
        "txn_index": j,
        "instrument": t.instrument,
        "isin": t.isin,
        "nature": t.nature,
        "side": t.side,
        "volume": t.volume,
        "price": t.price,
        "currency": t.currency,
        "trade_date": t.trade_date,
        "venue": t.venue,
        "venue_text": t.venue_text,
        "n_rows": len(t.rows),
    }


def _short_names(root: Path) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for table in ("prices_gpw", "prices_nc"):
        df = lake.read_table(root, "bronze", table)
        if df is None:
            continue
        for isin, name in df.select("isin", "name").unique().iter_rows():
            if isin and name:
                names.setdefault(isin, set()).add(name)
    return names


MONTHS_STATE = "insider_forms_months"
MONTHS_SCHEMA: dict[str, Any] = {
    "month": pl.Date,
    "done_at": pl.Datetime("us", "UTC"),
    "wanted": pl.Int32,
    "matched": pl.Int32,
}


def report_months(root: Path) -> list[date]:
    """Months that have report partitions in the lake (daily or compacted)."""
    base = root / "bronze" / "reports"
    months: set[date] = set()
    if base.exists():
        for p in base.iterdir():
            if p.name.startswith("date="):
                months.add(date.fromisoformat(p.name[5:]).replace(day=1))
            elif p.name.startswith("month="):
                months.add(date.fromisoformat(p.name[6:] + "-01"))
    return sorted(months)


def _month_days(root: Path, month: date) -> list[date]:
    days = []
    d = month
    while d.month == month.month:
        if lake.partition_exists(root, "bronze", "reports", d):
            days.append(d)
        d += timedelta(days=1)
    return days


def catch_up(
    client: PoliteClient,
    root: Path,
    *,
    budget_s: float,
    skip_recent_days: int = MAX_ATTEMPT_DAYS,
    clock: Any = None,
) -> list[dict[str, Any]]:
    """Collect forms for older months, newest first, until ``budget_s`` is used up.

    Months are marked done in ``state/insider_forms_months`` so each is scanned once. The
    recent window is left to the nightly collection and its retries.
    """
    clock = clock or (lambda: datetime.now(UTC))
    start = clock()
    done_df = lake.read_state(root, MONTHS_STATE)
    done = {r["month"]: r for r in done_df.to_dicts()} if done_df is not None else {}
    limit = (start - timedelta(days=skip_recent_days)).date().replace(day=1)
    months = [m for m in reversed(report_months(root)) if m < limit and m not in done]
    finished = []
    for m in months:
        if (clock() - start).total_seconds() > budget_s:
            break
        st = collect_forms(client, root, _month_days(root, m), now=clock())
        done[m] = {"month": m, "done_at": clock(), "wanted": st.wanted, "matched": st.matched}
        finished.append(done[m])
        log.info("insider forms %s: %s", f"{m:%Y-%m}", st.as_dict())
        lake.write_state(
            root,
            MONTHS_STATE,
            pl.DataFrame(list(done.values()), schema=MONTHS_SCHEMA).sort("month"),
        )
    return finished


def insider_reports(root: Path, days: list[date]) -> list[dict[str, Any]]:
    """Insider-transaction reports published on ``days``."""
    out = []
    for d in days:
        df = lake.read_partition(root, "bronze", "reports", d)
        if df is None:
            continue
        for r in df.iter_rows(named=True):
            if (
                classify(r["legal_basis"], r["title"], r["body_text"], r["kind"]).signal_type
                == "insider_tx"
            ):
                out.append(r)
    return out


def collect_forms(
    client: PoliteClient,
    root: Path,
    days: list[date],
    *,
    now: datetime | None = None,
) -> FormStats:
    """Find, read and store the notification forms of insider reports on ``days``.

    Reports that already have forms are skipped without any request. Reports no portal has
    yet are recorded in ``state/insider_forms_pending`` and tried again on later runs, for
    up to ``MAX_ATTEMPT_DAYS`` after publication.
    """
    now = now or datetime.now(UTC)
    stats = FormStats()
    pending = lake.read_state(root, PENDING_STATE)
    pend: dict[str, dict[str, Any]] = (
        {r["report_id"]: r for r in pending.to_dicts()} if pending is not None else {}
    )
    retry_days = {r["pub_date"] for r in pend.values()}
    all_days = sorted(set(days) | retry_days)
    reports = insider_reports(root, all_days)
    have: set[str] = set()
    for d in all_days:
        f = lake.read_partition(root, "bronze", TABLE, d)
        if f is not None:
            have |= set(f["report_id"].to_list())
    todo = [r for r in reports if r["report_id"] not in have]
    stats.wanted = len(todo)
    if not todo:
        return stats
    names = _short_names(root)
    todo_days = sorted({datetime.fromisoformat(r["published_at_local"]).date() for r in todo})
    candidates: dict[str, list[Candidate]] = {}
    for p in PORTALS:
        try:
            candidates[p.name] = p.candidates(client, todo_days)
        except Exception as exc:  # one portal down must not stop the others
            log.warning("portal %s listing failed: %s", p.name, exc)
            candidates[p.name] = []
    by_day: dict[date, list[dict[str, Any]]] = {}
    for r in todo:
        pub = datetime.fromisoformat(r["published_at_local"])
        ref = ReportRef(
            pub,
            r["title"] or "",
            (r["issuer_name"], *sorted(names.get(r["isin"], ()))),
            r["report_number"],
        )
        done = False
        for p in PORTALS:
            cand = best_match(ref, candidates[p.name])
            if cand is None:
                continue
            try:
                html = client.get(cand.url, use_cache=False).text()
                links = p.attachments(html, cand.url)[:MAX_PDFS_PER_REPORT]
                pdfs = [(u, client.get(u, use_cache=False).content) for u in links]
            except Exception as exc:
                log.info("%s: %s failed: %s", r["report_id"], p.name, exc)
                continue
            rows, forms = _form_rows(r, cand, pdfs, now)
            if not forms:
                continue  # page found but no readable form: try the next portal
            stats.matched += 1
            stats.forms += len(forms)
            stats.by_portal[p.name] = stats.by_portal.get(p.name, 0) + 1
            by_day.setdefault(pub.date(), []).extend(rows)
            pend.pop(r["report_id"], None)
            done = True
            break
        if not done:
            prev = pend.get(r["report_id"])
            pend[r["report_id"]] = {
                "report_id": r["report_id"],
                "pub_date": pub.date(),
                "attempts": (prev["attempts"] if prev else 0) + 1,
                "first_tried": prev["first_tried"] if prev else now,
                "last_tried": now,
                "last_error": "no portal copy with a readable form",
            }
    for d, rows in by_day.items():
        df = pl.DataFrame(rows, schema=FORM_SCHEMA).sort("report_id", "pdf_index", "txn_index")
        if lake.append_partition(root, "bronze", TABLE, d, df, key="report_id"):
            stats.written += df["report_id"].n_unique()
    cutoff = (now - timedelta(days=MAX_ATTEMPT_DAYS)).date()
    keep = [v for v in pend.values() if v["pub_date"] >= cutoff]
    stats.missing = len(keep)
    if keep or pending is not None:
        lake.write_state(
            root, PENDING_STATE, pl.DataFrame(keep, schema=PENDING_SCHEMA).sort("report_id")
        )
    return stats
