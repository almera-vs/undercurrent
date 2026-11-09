"""Fetch ESPI/EBI reports and daily prices, and land them as bronze Parquet.

Bronze keeps what the source said, mechanically extracted (labelled fields, raw text);
classification and field parsing happen in silver (:mod:`undercurrent.parse`,
:mod:`undercurrent.classify`). Every row carries ``known_at``, ``ingested_at``,
``source_url`` and ``content_sha256``.
"""

from __future__ import annotations

import html as html_lib
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
import xlrd
from selectolax.lexbor import LexborHTMLParser, LexborNode

from undercurrent import lake
from undercurrent.fetch import FetchResult, PoliteClient, SourceBlockedError
from undercurrent.sources import (
    SITE_BASE,
    TYPE_INDICES,
    TYPE_SHARES,
    espi_detail_url,
    espi_list_form,
    espi_list_url,
    gpw_archive_url,
)

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
SITES: tuple[str, ...] = ("gpw", "nc")
LIST_PAGE_SIZE = 100
MAX_BODY_CHARS = 60_000

# Price rows become knowable after the closing auction; we use 17:35 Warsaw time.
PRICE_KNOWN_AT_LOCAL = time(17, 35)

# Report kinds that are fetched in detail. Periodic reports are classified from the
# listing alone (their pages are long financial statements we don't need).
DETAIL_KINDS = frozenset({"Bieżący"})


# --------------------------------------------------------------------------- listing


@dataclass(frozen=True)
class ListingRow:
    """One entry of the ESPI/EBI listing."""

    site: str
    geru_id: int
    published_at_local: datetime  # naive, Europe/Warsaw wall clock
    kind: str  # Bieżący / Kwartalny / Półroczny / Roczny / ...
    system: str  # ESPI / EBI
    report_number: str
    issuer_name: str
    isin: str | None
    title: str | None

    @property
    def report_id(self) -> str:
        """Primary key: site-qualified ``geru_id``."""
        return f"{self.site}:{self.geru_id}"


_DATE_LINE = re.compile(
    r"(?P<d>\d{2}-\d{2}-\d{4})\s+(?P<t>\d{2}:\d{2}:\d{2})\s*\|\s*(?P<kind>[^|]+?)\s*\|"
    r"\s*(?P<system>ESPI|EBI)\s*\|\s*(?P<num>[^|<]*)"
)
_ISSUER = re.compile(r"^(?P<name>.*?)\s*\((?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\)\s*$", re.S)


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    t = re.sub(r"\s+", " ", html_lib.unescape(text)).strip()
    return t or None


def parse_listing(html: str, site: str) -> list[ListingRow]:
    """Parse the listing fragment returned by ``ajaxindex.php``."""
    tree = LexborHTMLParser(f"<ul>{html}</ul>")
    rows: list[ListingRow] = []
    for li in tree.css("li"):
        date_node = li.css_first("span.date")
        link = li.css_first("strong.name a")
        if date_node is None or link is None:
            continue
        m = _DATE_LINE.search(date_node.text())
        gm = re.search(r"geru_id=(\d+)", link.attributes.get("href") or "")
        if m is None or gm is None:
            log.warning("unparseable listing row on %s: %r", site, date_node.text()[:120])
            continue
        published = datetime.strptime(f"{m['d']} {m['t']}", "%d-%m-%Y %H:%M:%S")
        issuer_raw = _clean(link.text()) or ""
        im = _ISSUER.match(issuer_raw)
        p = li.css_first("p")
        rows.append(
            ListingRow(
                site=site,
                geru_id=int(gm.group(1)),
                published_at_local=published,
                kind=m["kind"].strip(),
                system=m["system"],
                report_number=m["num"].strip(),
                issuer_name=im["name"].strip() if im else issuer_raw,
                isin=im["isin"] if im else None,
                title=_clean(p.text()) if p is not None else None,
            )
        )
    return rows


def fetch_listing(client: PoliteClient, site: str, day: date) -> list[ListingRow]:
    """All listing rows for ``day`` on ``site``, paging until an empty page."""
    out: list[ListingRow] = []
    offset = 0
    while True:
        form = espi_list_form(day, offset=offset, limit=LIST_PAGE_SIZE, site=site)
        res = client.post(espi_list_url(site), data=form, use_cache=False)
        page = parse_listing(res.text(), site)
        page = [r for r in page if r.published_at_local.date() == day]
        out.extend(page)
        if len(page) < LIST_PAGE_SIZE:
            break
        offset += LIST_PAGE_SIZE
    seen: set[int] = set()
    unique = []
    for r in out:
        if r.geru_id not in seen:
            seen.add(r.geru_id)
            unique.append(r)
    return unique


# --------------------------------------------------------------------------- report page


@dataclass
class ReportPage:
    """Fields mechanically extracted from one report page."""

    template: str  # espi_legacy | gpwdata | ebi_legacy | unknown
    subject: str | None = None
    legal_basis: str | None = None
    body_text: str | None = None
    attachments: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_ESPI_LABELS = {
    "Temat": "subject",
    "Podstawa prawna": "legal_basis",
    "Treść raportu:": "body_text",
    "Załączniki": None,
}
_ESPI_STOP_PREFIXES = (
    "Załączniki",
    "MESSAGE",
    "Ramy prawne",
    "PODPISY OSÓB",
    "KOMISJA NADZORU",
    "Plik | Opis",
)


def _row_text(row: LexborNode) -> str:
    cells = [c.text(separator=" ", strip=True) for c in row.css("td")]
    return re.sub(r"\s+", " ", " | ".join(c for c in cells if c)).strip()


def _extract_espi_legacy(tree: LexborHTMLParser, page: ReportPage) -> None:
    current: str | None = None
    buf: dict[str, list[str]] = {"subject": [], "legal_basis": [], "body_text": []}
    for row in tree.css("tr.nTekst"):
        text = _row_text(row)
        if not text:
            continue
        if text in _ESPI_LABELS:
            current = _ESPI_LABELS[text]
            continue
        if current == "body_text" and text.startswith(_ESPI_STOP_PREFIXES):
            current = None
            continue
        if current is not None:
            buf[current].append(text)
    page.subject = _clean(" ".join(buf["subject"]))
    page.legal_basis = _clean(" ".join(buf["legal_basis"]))
    page.body_text = _clean("\n".join(buf["body_text"]))
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if href.startswith("attachment/"):
            item = {"name": _clean(a.text()) or "", "href": href}
            if item not in page.attachments:
                page.attachments.append(item)


def _extract_gpwdata(wrap: LexborNode, page: ReportPage) -> None:
    names: list[str] = [t for n in wrap.css(".rtl_table_name") if (t := _clean(n.text()))]
    if names:
        first = names[0]
        page.legal_basis = first.split(" - ", 1)[1].strip() if " - " in first else first
    body_parts: list[str] = []
    for div in wrap.css(".rtl_table_div"):
        for n in div.css(".rtl_table_name"):
            n.decompose()
        t = _clean(div.text(separator=" "))
        if t:
            body_parts.append(t)
    page.body_text = "\n".join(body_parts) or None
    if page.body_text is None:
        page.notes.append("body only in attachment (PDF), not retrievable")
    for a in wrap.css("a.file"):
        href = a.attributes.get("href") or ""
        page.attachments.append({"name": _clean(a.text()) or "", "href": href})


_EBI_BASIS = re.compile(r"Podstawa prawna\s*:?\s*(?P<basis>.+?)\s*$", re.S | re.I)


def _extract_ebi_legacy(node: LexborNode, page: ReportPage) -> None:
    text = node.text(separator="\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln)
    page.body_text = body or None
    idx = body.rfind("Podstawa prawna")
    if idx >= 0:
        m = _EBI_BASIS.match(body[idx:])
        if m:
            page.legal_basis = _clean(m["basis"][:600])
    if page.legal_basis is None:
        page.notes.append("no 'Podstawa prawna' in EBI body")


def extract_report_page(html: str) -> ReportPage:
    """Detect the page template and pull out subject, legal basis, body and attachments."""
    tree = LexborHTMLParser(html)
    for bad in tree.css("script, style"):
        bad.decompose()
    if tree.css_first("tr.nTekst") is not None:
        page = ReportPage("espi_legacy")
        _extract_espi_legacy(tree, page)
    elif (wrap := tree.css_first(".report_data_wrap")) is not None:
        page = ReportPage("gpwdata")
        _extract_gpwdata(wrap, page)
    elif nodes := tree.css("div.report-data"):
        page = ReportPage("ebi_legacy")
        _extract_ebi_legacy(nodes[-1], page)
    else:
        page = ReportPage("unknown", notes=["unrecognised report page template"])
    if page.body_text and len(page.body_text) > MAX_BODY_CHARS:
        page.body_text = page.body_text[:MAX_BODY_CHARS]
        page.notes.append(f"body truncated to {MAX_BODY_CHARS} chars")
    if page.legal_basis is None and page.template != "unknown":
        page.notes.append("legal basis not found")
    return page


# --------------------------------------------------------------------------- reports → bronze


def _to_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=WARSAW).astimezone(UTC)


REPORT_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "site": pl.String,
    "geru_id": pl.Int64,
    "system": pl.String,
    "kind": pl.String,
    "report_number": pl.String,
    "issuer_name": pl.String,
    "isin": pl.String,
    "title": pl.String,
    "published_at_local": pl.String,
    "published_at": pl.Datetime("us", "UTC"),
    "known_at": pl.Datetime("us", "UTC"),
    "template": pl.String,
    "subject": pl.String,
    "legal_basis": pl.String,
    "body_text": pl.String,
    "attachments_json": pl.String,
    "extract_notes": pl.String,
    "source_url": pl.String,
    "content_sha256": pl.String,
    "ingested_at": pl.Datetime("us", "UTC"),
}

FAILED_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "site": pl.String,
    "geru_id": pl.Int64,
    "published_date": pl.Date,
    "error": pl.String,
    "attempts": pl.Int64,
    "first_failed_at": pl.Datetime("us", "UTC"),
    "last_failed_at": pl.Datetime("us", "UTC"),
}


def _report_row(
    r: ListingRow, page: ReportPage | None, res: FetchResult | None, now: datetime
) -> dict[str, Any]:
    published = _to_utc(r.published_at_local)
    return {
        "report_id": r.report_id,
        "site": r.site,
        "geru_id": r.geru_id,
        "system": r.system,
        "kind": r.kind,
        "report_number": r.report_number,
        "issuer_name": r.issuer_name,
        "isin": r.isin,
        "title": r.title,
        "published_at_local": r.published_at_local.isoformat(),
        "published_at": published,
        "known_at": published,
        "template": page.template if page else "listing_only",
        "subject": page.subject if page else None,
        "legal_basis": page.legal_basis if page else None,
        "body_text": page.body_text if page else None,
        "attachments_json": json.dumps(page.attachments, ensure_ascii=False) if page else "[]",
        "extract_notes": "; ".join(page.notes) if page and page.notes else None,
        "source_url": res.url if res else espi_detail_url(r.geru_id, r.site),
        "content_sha256": res.sha256 if res else None,
        "ingested_at": now,
    }


@dataclass
class IngestStats:
    """Counters for the run log / health line."""

    listed: int = 0
    new: int = 0
    written: int = 0
    failed: int = 0
    blocked: int = 0
    details: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Plain dict for JSON logs."""
        return asdict(self)


def _record_failure(
    failures: dict[str, dict[str, Any]], r: ListingRow, error: str, now: datetime
) -> None:
    prev = failures.get(r.report_id)
    failures[r.report_id] = {
        "report_id": r.report_id,
        "site": r.site,
        "geru_id": r.geru_id,
        "published_date": r.published_at_local.date(),
        "error": error[:500],
        "attempts": (int(prev["attempts"]) if prev else 0) + 1,
        "first_failed_at": prev["first_failed_at"] if prev else now,
        "last_failed_at": now,
    }


def ingest_reports(
    client: PoliteClient, root: Path, day: date, *, now: datetime | None = None
) -> IngestStats:
    """Land every report published on ``day`` (both sites) into ``bronze/reports``.

    Idempotent: report IDs already in the partition are skipped without any request.
    Reports whose page fails are kept out of bronze and tracked in ``state/failed_reports``
    so the next run retries them.
    """
    now = now or datetime.now(UTC)
    stats = IngestStats()
    existing = lake.read_partition(root, "bronze", "reports", day)
    known: set[str] = set(existing["report_id"].to_list()) if existing is not None else set()
    fstate = lake.read_state(root, failed_state_name(day))
    failures: dict[str, dict[str, Any]] = (
        {row["report_id"]: row for row in fstate.to_dicts()} if fstate is not None else {}
    )

    rows: list[dict[str, Any]] = []
    for site in SITES:
        listing = fetch_listing(client, site, day)
        stats.listed += len(listing)
        for r in listing:
            if r.report_id in known:
                continue
            stats.new += 1
            if r.kind not in DETAIL_KINDS:
                rows.append(_report_row(r, None, None, now))
                continue
            url = espi_detail_url(r.geru_id, site)
            try:
                res = client.get(url, max_age_s=365 * 86400)
                page = extract_report_page(res.text())
            except SourceBlockedError as exc:
                stats.blocked += 1
                _record_failure(failures, r, f"blocked: {exc}", now)
                continue
            except Exception as exc:  # keep going; the report is retried next run
                stats.failed += 1
                _record_failure(failures, r, f"{type(exc).__name__}: {exc}", now)
                log.warning("report %s failed: %s", r.report_id, exc)
                continue
            stats.details[page.template] = stats.details.get(page.template, 0) + 1
            rows.append(_report_row(r, page, res, now))
            failures.pop(r.report_id, None)

    if rows:
        df = pl.DataFrame(rows, schema=REPORT_SCHEMA)
        if lake.append_partition(root, "bronze", "reports", day, df, key="report_id"):
            stats.written = df.height
    fdf = pl.DataFrame(list(failures.values()), schema=FAILED_SCHEMA).sort("report_id")
    if fdf.height or fstate is not None:
        lake.write_state(root, failed_state_name(day), fdf)
    return stats


def failed_state_name(day: date) -> str:
    """Failed-report state is split per month so concurrent writers never share a file."""
    return f"failed_reports/month={day:%Y-%m}"


def failed_days(root: Path, months: list[date]) -> list[date]:
    """Published dates that still have failed reports, across the given months."""
    out: set[date] = set()
    for m in months:
        df = lake.read_state(root, failed_state_name(m))
        if df is not None and df.height:
            out.update(df["published_date"].to_list())
    return sorted(out)


# --------------------------------------------------------------------------- prices


class NoSessionError(LookupError):
    """The archive has no data for this date (weekend, holiday, or not yet published)."""


def _is_html(res: FetchResult) -> bool:
    return "html" in res.content_type or res.content.lstrip()[:15].lower().startswith(b"<!doctype")


def _price_known_at(day: date) -> datetime:
    return datetime.combine(day, PRICE_KNOWN_AT_LOCAL, tzinfo=WARSAW).astimezone(UTC)


GPW_COLUMNS = {
    "Data": "trade_date",
    "Nazwa": "name",
    "ISIN": "isin",
    "Waluta": "currency",
    "Kurs otwarcia": "open",
    "Kurs max": "high",
    "Kurs min": "low",
    "Kurs zamknięcia": "close",
    "Zmiana": "change_pct",
    "Wolumen": "volume",
    "Liczba Transakcji": "trades",
    "Obrót": "turnover_kpln",
    "Liczba otwartych pozycji": "open_interest",
    "Wartość otwartych pozycji": "open_interest_value",
    "Cena nominalna": "nominal",
}
_FLOAT_COLS = (
    "open",
    "high",
    "low",
    "close",
    "change_pct",
    "volume",
    "trades",
    "turnover_kpln",
    "open_interest",
    "open_interest_value",
    "nominal",
)


def parse_gpw_xls(content: bytes, day: date) -> pl.DataFrame:
    """Parse a GPW quote-archive ``.xls`` (shares or indices) into a typed frame."""
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    if sheet.nrows < 2:
        raise NoSessionError(f"empty archive sheet for {day}")
    header = [str(h).strip() for h in sheet.row_values(0)]
    missing = set(GPW_COLUMNS) - set(header)
    if missing:
        raise ValueError(f"GPW archive columns changed; missing {sorted(missing)}")
    idx = {GPW_COLUMNS[h]: i for i, h in enumerate(header) if h in GPW_COLUMNS}
    records: list[dict[str, Any]] = []
    for r in range(1, sheet.nrows):
        vals = sheet.row_values(r)
        rec: dict[str, Any] = {k: vals[i] for k, i in idx.items()}
        records.append(rec)
    df = pl.DataFrame(records, infer_schema_length=None)
    df = df.with_columns(
        pl.col("trade_date").cast(pl.String).str.to_date("%Y-%m-%d"),
        *(pl.col(c).cast(pl.String).replace("", None).cast(pl.Float64) for c in _FLOAT_COLS),
        pl.col("name").cast(pl.String),
        pl.col("isin").cast(pl.String).replace("", None),
        pl.col("currency").cast(pl.String).replace("", None),
    )
    if df.filter(pl.col("trade_date") != day).height:
        raise ValueError(f"archive for {day} contains other dates")
    return df.sort("name")


def _nc_header(rows: list[list[Any]], n_cols: int, start: int, end: int) -> list[str]:
    heads = []
    for c in range(n_cols):
        parts = [str(rows[r][c]).strip() for r in range(start, end) if c < len(rows[r])]
        heads.append(" ".join(p for p in parts if p and p != "None"))
    return heads


# (target, keywords) — first keyword match wins, each target is used once.
_NC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("isin", ("isin",)),
    ("ticker", ("ticker", "skrót")),
    ("name", ("security", "nazwa")),
    ("currency", ("currency", "waluta")),
    ("system", ("trading system", "system notowań")),
    ("segment", ("segment",)),
    ("sector", ("sector", "sektor")),
    ("close", ("closing", "zamknięcia")),
    ("reference", ("reference", "odniesienia")),
    ("change_pct", ("change", "zmiana")),
    ("open", ("opening", "otwarcia")),
    ("low", ("low", "min.")),
    ("high", ("high", "maks.")),
    ("last", ("last", "ostatni")),
    ("vwap", ("aggregated", "zagregowany")),
    ("trades", ("number", "liczba")),
    ("volume", ("volume", "wolumen")),
    ("value_pln", ("value", "wartość")),
)
_NC_NUMERIC = (
    "close",
    "reference",
    "change_pct",
    "open",
    "low",
    "high",
    "last",
    "vwap",
    "trades",
    "volume",
    "value_pln",
)
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _nc_rows(content: bytes, xlsx: bool) -> list[list[Any]]:
    if xlsx:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb["akcje"] if "akcje" in wb.sheetnames else wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    book = xlrd.open_workbook(file_contents=content)
    names = book.sheet_names()
    sheet = book.sheet_by_name("akcje") if "akcje" in names else book.sheet_by_index(0)
    return [sheet.row_values(r) for r in range(sheet.nrows)]


def parse_nc_daily(content: bytes, day: date, *, xlsx: bool) -> pl.DataFrame:
    """Parse the NewConnect daily statistics workbook (sheet ``akcje``)."""
    rows = _nc_rows(content, xlsx)
    n_cols = max(len(r) for r in rows)
    first_data = next(
        (i for i, r in enumerate(rows) if any(isinstance(v, str) and _ISIN_RE.match(v) for v in r)),
        None,
    )
    if first_data is None:
        raise NoSessionError(f"no share rows in NewConnect file for {day}")
    heads = _nc_header(rows, n_cols, max(0, first_data - 6), first_data)
    isin_col = next(
        c
        for c in range(n_cols)
        if isinstance(rows[first_data][c], str) and _ISIN_RE.match(rows[first_data][c])
    )
    mapping: dict[str, int] = {"isin": isin_col}
    for target, keys in _NC_RULES:
        if target in mapping:
            continue
        for c, h in enumerate(heads):
            if c in mapping.values():
                continue
            if any(k in h.lower() for k in keys):
                mapping[target] = c
                break
    ca_cols = [c for c in range(min(isin_col, 5)) if c not in mapping.values()]
    records = []
    for r in rows[first_data:]:
        r = list(r) + [None] * (n_cols - len(r))
        isin = r[isin_col]
        if not (isinstance(isin, str) and _ISIN_RE.match(isin)):
            continue
        rec: dict[str, Any] = {k: r[c] for k, c in mapping.items()}
        flags = [str(r[c]).strip() for c in ca_cols if r[c] not in (None, "")]
        rec["ca_flags"] = " ".join(flags) or None
        records.append(rec)
    df = pl.DataFrame(records, infer_schema_length=None)

    def num(c: str) -> pl.Expr:
        return (
            pl.col(c)
            .cast(pl.String)
            .str.replace_all(r"[\s\xa0]", "")
            .str.replace(",", ".")
            .replace({"": None, "---": None, "-": None})
            .cast(pl.Float64, strict=False)
        )

    df = df.with_columns(
        *(num(c) for c in _NC_NUMERIC if c in df.columns),
        *(
            pl.col(c).cast(pl.String)
            for c in ("isin", "ticker", "name", "currency", "system", "segment", "sector")
            if c in df.columns
        ),
        pl.lit(day).alias("trade_date"),
    )
    return df.sort("isin")


def _with_meta(df: pl.DataFrame, res: FetchResult, day: date, now: datetime) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(_price_known_at(day)).cast(pl.Datetime("us", "UTC")).alias("known_at"),
        pl.lit(now).cast(pl.Datetime("us", "UTC")).alias("ingested_at"),
        pl.lit(res.url).alias("source_url"),
        pl.lit(res.sha256).alias("content_sha256"),
    )


def ingest_gpw_prices(
    client: PoliteClient, root: Path, day: date, *, now: datetime | None = None
) -> dict[str, int]:
    """Land main-market shares and indices for ``day``. Returns rows written per table."""
    now = now or datetime.now(UTC)
    out: dict[str, int] = {}
    for table, itype in (("prices_gpw", TYPE_SHARES), ("indices_gpw", TYPE_INDICES)):
        if lake.partition_exists(root, "bronze", table, day):
            out[table] = 0
            continue
        res = client.get(gpw_archive_url(day, instrument_type=itype, xls=True), max_age_s=3600)
        if _is_html(res):
            raise NoSessionError(f"GPW archive has no {table} for {day}")
        df = _with_meta(parse_gpw_xls(res.content, day), res, day, now)
        lake.append_partition(root, "bronze", table, day, df)
        out[table] = df.height
    return out


def nc_daily_candidates(day: date) -> list[tuple[str, bool]]:
    """Candidate URLs (with xlsx flag) for the NewConnect daily file, most likely first."""
    base = f"{SITE_BASE['nc']}/pub/NEWCONNECT/statystyki/statystyki_dzienne/NC_{day:%Y%m%d}"
    xlsx_first = day >= date(2021, 1, 1)
    order = [True, False] if xlsx_first else [False, True]
    return [(f"{base}.{'xlsx' if x else 'xls'}", x) for x in order]


def ingest_nc_prices(
    client: PoliteClient, root: Path, day: date, *, now: datetime | None = None
) -> int:
    """Land NewConnect shares for ``day``. Raises :class:`NoSessionError` if not published."""
    now = now or datetime.now(UTC)
    if lake.partition_exists(root, "bronze", "prices_nc", day):
        return 0
    for url, xlsx in nc_daily_candidates(day):
        res = client.get(url, max_age_s=0)
        if _is_html(res):
            continue
        df = _with_meta(parse_nc_daily(res.content, day, xlsx=xlsx), res, day, now)
        lake.append_partition(root, "bronze", "prices_nc", day, df)
        return df.height
    raise NoSessionError(f"NewConnect daily file for {day} not available")


def recent_weekdays(day: date, n: int) -> list[date]:
    """The ``n`` weekdays up to and including ``day``, oldest first."""
    out: list[date] = []
    d = day
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)
