"""Annual reports in ESEF from Bankier's copies of the ESPI attachments (fiscal 2024 on).

filings.xbrl.org stops at Polish filings added in July 2024 (``esef.py``), and the exchange
serves attachments only behind bot protection. bankier.pl republishes every periodic report
as a "Wyniki finansowe" page and hosts its attachments, among them the annual report's ESEF
package ``<name>-<yyyy-mm-dd>-<n>-<lang>.zip``. This module finds each main-market
company's annual reports there and reads the package's inline XBRL into the same facts as
``esef.py`` (``bronze/fundamentals_esef``), so ``esef.statements`` and ``valuation`` use them
unchanged.

1. The company's announcements on Bankier (``/gielda/notowania/akcje/<short name>/komunikaty``,
   newest first, ``/<n>`` for older pages) are read back to ``SINCE``.
2. For each fiscal year, the consolidated annual report (``SRR``) is read, else the
   standalone one (``RR``): its page lists the ESEF package among the attachments.
3. The package is opened in memory; its report is parsed for the facts of ``esef.CONCEPTS``.
   The issuer's LEI comes from the report and must map, at GLEIF, to the ISIN that was
   looked for; otherwise nothing is kept.

``known_at`` is the report's publication time on Bankier. Pages are read once
(``state/esef_bankier``); a company is looked up again after ``RECHECK_DAYS``. One request a
second; a package is 1–30 MB, read in memory and never stored.
"""

from __future__ import annotations

import io
import logging
import math
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import polars as pl

from undercurrent import esef, lake
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)

BASE = "https://www.bankier.pl"
LISTING_URL = BASE + "/gielda/notowania/akcje/{symbol}/komunikaty"
STATE = "esef_bankier"  # one row per report page read
COMPANY_STATE = "esef_bankier_companies"  # when each company was last looked up
SINCE = date(2024, 9, 1)  # filings.xbrl.org has Polish filings added up to July 2024
RECHECK_DAYS = 30
MAX_PAGES = 12
MAX_PACKAGE_BYTES = 80_000_000
WARSAW = ZoneInfo("Europe/Warsaw")

_TITLE = re.compile(r"Wyniki finansowe\s+(S?RR)\s*(?:(\d{4})\s*)?/\s*(\d{4})", re.I)
# older pages are titled just "wyniki finansowe": their text says "Raport roczny RR 2024"
_GENERIC = re.compile(r"^\s*wyniki finansowe\s*$", re.I)
_PAGE_KIND = re.compile(r"\b(SRR|RR)\s+(\d{4})\b")
# an ESEF report: <name>-<yyyy-mm-dd>-<n>-<lang>[_stamp].zip|xbri|xhtml
_ESEF_NAME = re.compile(r"-(\d{4})-\d{2}-\d{2}(?:-\d+)?-[a-z]{2}(?:_\d+)?\.(zip|xbri|xhtml)$", re.I)
_STATEMENTS = re.compile(
    r"financial_?statements|sprawozdani[ea]_finansow|(^|[_-])[SJ]?SF[_-]|MSSF", re.I
)
_NOT_STATEMENTS = re.compile(
    r"(^|[_-])SZ[A-Z]?[_-]|zarzad|management|rada|board|oswiadczen|statement_-|list|letter|esg|audyt|audit",
    re.I,
)
_ITEM = re.compile(
    r'item__date">\s*([0-9.]+\s+[0-9:]+)\s*<.*?item__anchor"\s+href="([^"]+)"[^>]*>([^<]+)<',
    re.S,
)
IX = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"
LEI_SCHEME = "http://standards.iso.org/iso/17442"

STATE_SCHEMA: dict[str, Any] = {
    "url": pl.String,
    "isin": pl.String,
    "symbol": pl.String,
    "kind": pl.String,  # SRR | RR
    "fiscal_year": pl.Int64,
    "published": pl.Datetime("us", "UTC"),
    "package": pl.String,
    "lei": pl.String,
    "facts": pl.Int64,
    "status": pl.String,  # ok | no_package | not_ixbrl | other_issuer | not_annual | not_chosen
    "read_at": pl.Datetime("us", "UTC"),
}


@dataclass(frozen=True)
class Report:
    """A results page on Bankier that may be an annual report."""

    url: str
    published: datetime  # UTC
    kind: str | None  # SRR (consolidated) | RR (standalone) | None: read the page to know
    fiscal_year: int | None


@dataclass(frozen=True)
class Company:
    """A main-market company to look up: its ISIN and GPW short name (Bankier's symbol)."""

    isin: str
    symbol: str


# ------------------------------------------------------------------ Bankier pages


def parse_listing(html: str) -> tuple[list[Report], datetime | None]:
    """Results pages on one announcements page, and the page's oldest publication time.

    A page titled "Wyniki finansowe SRR /2025" is known to be an annual report; one titled
    just "wyniki finansowe" (Bankier's older pages) may be, and its text says which.
    """
    out: list[Report] = []
    oldest: datetime | None = None
    for when, href, title in _ITEM.findall(html):
        try:
            local = datetime.strptime(when.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=WARSAW)
        except ValueError:
            continue
        ts = local.astimezone(UTC)
        oldest = ts if oldest is None or ts < oldest else oldest
        url = urljoin(BASE, href)
        m = _TITLE.search(title)
        if m:
            out.append(Report(url, ts, m.group(1).upper(), int(m.group(3))))
        elif _GENERIC.match(title):
            out.append(Report(url, ts, None, None))
    return out, oldest


def reports_for(client: PoliteClient, symbol: str, since: date = SINCE) -> list[Report]:
    """The company's possible annual report pages published since ``since``, newest first."""
    out: list[Report] = []
    base = LISTING_URL.format(symbol=symbol)
    for page in range(1, MAX_PAGES + 1):
        url = base if page == 1 else f"{base}/{page}"
        try:
            res = client.get(url, max_age_s=86400)
        except Exception as exc:
            log.info("bankier %s page %d: %s", symbol, page, exc)
            break
        if f"/akcje/{symbol}/".lower() not in res.url.lower() + "/":
            break  # redirected away: Bankier has no such symbol
        found, oldest = parse_listing(res.text())
        out += [r for r in found if r.published.date() >= since]
        if oldest is None or oldest.date() < since:
            break
    return out


def classify(report: Report, html: str) -> Report:
    """The report's kind and fiscal year, from its title or else from its page's text."""
    if report.kind:
        return report
    text = re.sub(r"<[^>]+>", " ", html)
    m = _PAGE_KIND.search(text)
    return Report(report.url, report.published, m.group(1), int(m.group(2))) if m else report


def pick(reports: Iterable[Report]) -> list[Report]:
    """One annual report per fiscal year: the consolidated one where there is one."""
    best: dict[int, Report] = {}
    for r in reports:
        if r.kind is None or r.fiscal_year is None:
            continue
        cur = best.get(r.fiscal_year)
        if cur is None or (cur.kind == "RR" and r.kind == "SRR"):
            best[r.fiscal_year] = r
    return [best[y] for y in sorted(best)]


def report_files(html: str, url: str) -> list[str]:
    """Attachments that may hold the tagged financial statements, most likely first.

    ESEF packages (``.zip``, ``.xbri``) first, then reports named the ESEF way
    (``11bitstudios-2025-12-31-1-pl.xhtml``), then other financial statements in XHTML.
    Management reports, letters, statements and audit reports are left out.
    """
    ranked: list[tuple[int, str]] = []
    for href in dict.fromkeys(re.findall(r'href="([^"]+)"', html)):
        full = urljoin(url, href)
        if "/static/att/" not in full:
            continue
        name = full.split("?")[0].rsplit("/", 1)[-1]
        name = re.sub(r"^\d{8}_\d{6}_\d+_", "", name)  # Bankier's time-stamp prefix
        if name.lower().endswith((".xades", ".pdf")) or _NOT_STATEMENTS.search(name):
            continue
        esef_name = _ESEF_NAME.search(name)
        if esef_name and esef_name.group(2).lower() in ("zip", "xbri"):
            ranked.append((0, full))
        elif esef_name:
            ranked.append((1, full))
        elif name.lower().endswith(".xhtml") and _STATEMENTS.search(name):
            ranked.append((2, full))
    return [u for _, u in sorted(ranked, key=lambda t: t[0])]


# ------------------------------------------------------------------ inline XBRL


def _number(text: str, fmt: str) -> float | None:
    """An inline XBRL figure as written (``1 234,5``) with its transformation format."""
    t = text.strip().replace(" ", " ")
    if "zero" in fmt or t in {"-", "–", "—"}:
        return 0.0
    if "comma" in fmt:  # num-comma-decimal: 1.234,5 / 1 234,5
        t = t.replace(" ", "").replace(".", "").replace(",", ".")
    else:  # num-dot-decimal: 1,234.5 / 1 234.5
        t = t.replace(" ", "").replace(",", "")
    t = re.sub(r"[^0-9.]", "", t)
    try:
        return float(t)
    except ValueError:
        return None


def _qname(name: str, nsmap: dict[str | None, str]) -> str:
    """``ifrs-full:Revenue`` whatever prefix the document gives the IFRS namespace."""
    prefix, _, local = name.rpartition(":")
    uri = nsmap.get(prefix or None, "")
    return f"ifrs-full:{local}" if "/ifrs-full" in uri else name


def facts_from_ixbrl(
    doc: bytes, filing_id: str, known_at: datetime
) -> tuple[str | None, pl.DataFrame]:
    """The issuer's LEI and the headline facts (``esef.CONCEPTS``) of an inline XBRL report."""
    from lxml import etree  # type: ignore[import-untyped]

    parser = etree.XMLParser(huge_tree=True, recover=True, resolve_entities=False)
    root = etree.fromstring(doc, parser)
    if root is None:
        return None, pl.DataFrame(schema=esef.FACT_SCHEMA)
    contexts: dict[str, tuple[date | None, date]] = {}
    lei = None
    for ctx in root.iter(f"{{{XBRLI}}}context"):
        ident = ctx.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}identifier")
        if ident is not None and ident.get("scheme") == LEI_SCHEME and ident.text:
            lei = lei or ident.text.strip()
        if (
            ctx.find(f".//{{{XBRLI}}}segment") is not None
            or ctx.find(f".//{{{XBRLI}}}scenario") is not None
        ):
            continue  # a dimensional breakdown
        inst = ctx.find(f"{{{XBRLI}}}period/{{{XBRLI}}}instant")
        start = ctx.find(f"{{{XBRLI}}}period/{{{XBRLI}}}startDate")
        end = ctx.find(f"{{{XBRLI}}}period/{{{XBRLI}}}endDate")
        try:
            if inst is not None and inst.text:
                contexts[ctx.get("id", "")] = (None, date.fromisoformat(inst.text.strip()[:10]))
            elif start is not None and end is not None and start.text and end.text:
                contexts[ctx.get("id", "")] = (
                    date.fromisoformat(start.text.strip()[:10]),
                    date.fromisoformat(end.text.strip()[:10]),
                )
        except ValueError:
            continue
    units: dict[str, str] = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        measures = [m.text.strip() for m in u.iter(f"{{{XBRLI}}}measure") if m.text]
        if len(measures) == 1:
            units[u.get("id", "")] = measures[0]
        elif len(measures) == 2:  # a ratio: currency per share
            units[u.get("id", "")] = f"{measures[0]}/{measures[1]}"
    rows: dict[str, dict[str, Any]] = {}
    for el in root.iter(f"{{{IX}}}nonFraction"):
        concept = esef.CONCEPTS.get(_qname(el.get("name", ""), el.nsmap))
        period = contexts.get(el.get("contextRef", ""))
        unit = units.get(el.get("unitRef", ""), "")
        if (
            concept is None
            or period is None
            or el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
        ):
            continue
        if (unit == "xbrli:shares") != (concept in esef.SHARE_CONCEPTS) or (
            unit != "xbrli:shares" and not unit.startswith("iso4217:")
        ):
            continue
        value = _number("".join(el.itertext()), el.get("format", ""))
        if value is None or not math.isfinite(value):
            continue
        value *= 10 ** int(el.get("scale", "0") or 0)
        if el.get("sign") == "-":
            value = -value
        start, end = period
        key = f"{filing_id}:{concept}:{start}:{end}:{unit}"
        if key in rows:
            continue
        dec = el.get("decimals", "")
        rows[key] = {
            "fact_key": key,
            "filing_id": filing_id,
            "lei": lei,
            "fiscal_period_end": None,
            "concept": concept,
            "period_start": start,
            "period_end": end,
            "value": value,
            "unit": unit.removeprefix("iso4217:"),
            "decimals": int(dec) if re.fullmatch(r"-?\d+", dec) else None,
            "known_at": known_at,
        }
    df = pl.DataFrame(list(rows.values()), schema=esef.FACT_SCHEMA)
    if df.height:
        fy = df["period_end"].max()
        df = df.with_columns(
            pl.lit(fy).cast(pl.Date).alias("fiscal_period_end"), pl.lit(lei).alias("lei")
        )
    return lei, df.sort("fact_key")


def report_from_package(data: bytes) -> bytes | None:
    """The inline XBRL report in an ESEF package, or ``data`` itself if it is one."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return data if b"inlineXBRL" in data[:200_000] else None
    names = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html"))]
    names.sort(key=lambda n: ("/reports/" not in n.lower(), -z.getinfo(n).file_size))
    return z.read(names[0]) if names else None


# ------------------------------------------------------------------ the run


def listed_gpw(data_dir: Path, days: int = 14) -> list[Company]:
    """Main-market stocks with a price in the last ``days`` days, with their short names."""
    base = data_dir / "bronze" / "prices_gpw"
    parts = sorted(p for p in base.glob("date=*") if p.is_dir())
    if not parts:
        return []
    last = date.fromisoformat(parts[-1].name[5:])
    frames = [
        pl.read_parquet(f)
        for p in parts
        if date.fromisoformat(p.name[5:]) >= last - timedelta(days=days)
        for f in p.glob("*.parquet")
    ]
    df = pl.concat(frames, how="diagonal_relaxed").filter(pl.col("isin").is_not_null())
    rows = df.sort("trade_date").group_by("isin", maintain_order=True).last()
    return sorted(
        (Company(r["isin"], r["name"]) for r in rows.iter_rows(named=True) if r["name"]),
        key=lambda c: c.isin,
    )


def _read_report(
    client: PoliteClient,
    r: Report,
    c: Company,
    html: str,
    base: str,
    known: set[str],
    lei_isins: dict[str, set[str]],
    new_leis: list[dict[str, Any]],
    root: Path,
) -> dict[str, Any]:
    """Read the first of the page's files that holds tagged statements; write its facts.

    The report's LEI must belong to the company: GLEIF lists the ISIN, or GLEIF lists no
    listed share at all (it often lists only an issuer's bonds), and then the link is added.
    """
    files = report_files(html, base)
    if not files:
        return {"status": "no_package"}
    fid = "bankier-" + re.sub(r"\D", "", r.url.rsplit("-", 1)[-1])
    for url in files[:3]:
        try:
            res = client.get(url, use_cache=False)
        except Exception as exc:
            log.info("bankier file %s: %s", url, exc)
            continue
        doc = report_from_package(res.content[:MAX_PACKAGE_BYTES])
        if not doc:
            continue
        lei, facts = facts_from_ixbrl(doc, fid, r.published)
        if lei is None or facts.height < 5:
            continue
        if lei not in lei_isins:
            lei_isins[lei] = set(esef.isins_for(client, lei))
            new_leis += [{"lei": lei, "isin": i, "checked_at": r.published} for i in lei_isins[lei]]
        isins = lei_isins[lei]
        if c.isin not in isins:
            if isins & (known - {c.isin}):
                return {"package": url, "lei": lei, "status": "other_issuer"}
            isins.add(c.isin)
            new_leis.append({"lei": lei, "isin": c.isin, "checked_at": r.published})
        lake.append_partition(root, "bronze", esef.TABLE, r.published.date(), facts, key="fact_key")
        return {"package": url, "lei": lei, "facts": facts.height, "status": "ok"}
    return {"package": files[0], "status": "not_ixbrl"}


def _read_state(root: Path, name: str) -> pl.DataFrame | None:
    return lake.read_state(root, name)


def ingest(
    client: PoliteClient,
    root: Path,
    companies: Iterable[Company],
    *,
    max_companies: int = 60,
    now: datetime | None = None,
) -> dict[str, int]:
    """Look up the companies due, read their new annual reports, write facts and state."""
    now = now or datetime.now(UTC)
    pages = _read_state(root, STATE)
    seen = set(pages["url"].to_list()) if pages is not None else set()
    checked = _read_state(root, COMPANY_STATE)
    last = (
        {r["isin"]: r["checked_at"] for r in checked.iter_rows(named=True)}
        if checked is not None
        else {}
    )
    stale = now - timedelta(days=RECHECK_DAYS)
    due = [c for c in companies if c.isin not in last or last[c.isin] < stale]
    due.sort(key=lambda c: (c.isin in last, c.isin))
    leis_df = lake.read_state(root, esef.LEI_STATE)
    lei_isins: dict[str, set[str]] = {}
    if leis_df is not None:
        for link in leis_df.iter_rows(named=True):
            lei_isins.setdefault(link["lei"], set()).add(link["isin"])
    new_pages: list[dict[str, Any]] = []
    new_leis: list[dict[str, Any]] = []
    n_facts = 0
    looked: list[dict[str, Any]] = []
    known = {c.isin for c in companies}
    for c in due[:max_companies]:
        try:
            found = [r for r in reports_for(client, c.symbol) if r.url not in seen]
        except Exception as exc:  # one company's failure does not stop the run
            log.warning("bankier %s: %s", c.symbol, exc)
            found = []
        pages_html: dict[str, tuple[str, str]] = {}
        typed: list[Report] = []
        for r in found:
            try:
                page = client.get(r.url, max_age_s=30 * 86400)
            except Exception as exc:
                log.warning("bankier page %s: %s", r.url, exc)
                continue
            pages_html[r.url] = (page.text(), page.url)
            typed.append(classify(r, page.text()))
        chosen = {r.url for r in pick(typed)}
        for r in typed:
            row: dict[str, Any] = {
                "url": r.url,
                "isin": c.isin,
                "symbol": c.symbol,
                "kind": r.kind,
                "fiscal_year": r.fiscal_year,
                "published": r.published,
                "package": None,
                "lei": None,
                "facts": 0,
                "status": "not_annual" if r.kind is None else "not_chosen",
                "read_at": now,
            }
            if r.url in chosen:
                html, base = pages_html[r.url]
                row |= _read_report(client, r, c, html, base, known, lei_isins, new_leis, root)
                n_facts += row["facts"]
            new_pages.append(row)
            seen.add(r.url)
        looked.append({"isin": c.isin, "symbol": c.symbol, "checked_at": now})
    if new_pages:
        df = pl.DataFrame(new_pages, schema=STATE_SCHEMA)
        lake.write_state(
            root, STATE, df if pages is None else pl.concat([pages, df], how="diagonal_relaxed")
        )
    if looked:
        df = pl.DataFrame(looked)
        if checked is not None:
            checked = checked.filter(~pl.col("isin").is_in(df["isin"].implode()))
        lake.write_state(root, COMPANY_STATE, df if checked is None else pl.concat([checked, df]))
    if new_leis:
        df = pl.DataFrame(new_leis)
        lake.write_state(
            root,
            esef.LEI_STATE,
            df if leis_df is None else pl.concat([leis_df, df], how="diagonal_relaxed"),
        )
    ok = sum(1 for p in new_pages if p["status"] == "ok")
    log.info(
        "esef via bankier: %d companies, %d reports read (%d ok), %d facts",
        len(looked),
        len(new_pages),
        ok,
        n_facts,
    )
    return {"companies": len(looked), "reports": len(new_pages), "ok": ok, "facts": n_facts}
