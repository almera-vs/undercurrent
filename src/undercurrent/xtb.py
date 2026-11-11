"""Links to a company's stock page on xtb.com, kept only when they are verified.

XTB lists its stock pages in a public sitemap (``/pl/akcje/<slug>``). Polish stocks use the
company's name (``hydrotor``, ``cd-projekt``) or the ticker with ``-pl`` (``s2b-pl``), and an
unknown slug redirects to the stock list. A guessed slug is therefore never trusted: a link
is kept only if the slug is in the sitemap and the page itself shows the stock's ISIN.

Results live in ``state/xtb_links.parquet``: one row per ISIN, checked again after
``RECHECK_DAYS``. Each run checks at most ``MAX_CHECKS`` ISINs, one request per second, so
the nightly cost is a handful of page loads. Undercurrent is not affiliated with XTB.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import polars as pl

from undercurrent import lake
from undercurrent.classify import normalize
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)

SITEMAP_URL = "https://www.xtb.com/pl/sitemap/instruments/cashstocks.xml"
PAGE_URL = "https://www.xtb.com/pl/akcje/{slug}"
# XTB's own logo for an instrument, by its symbol: PKO.PL -> pko_pl.svg (403 when it has none)
LOGO_URL = "https://logos.xtb.com/{code}_pl.svg"
STATE = "xtb_links"
RECHECK_DAYS = 30
MAX_CHECKS = 40
MAX_CANDIDATES = 4
SITEMAP_MAX_AGE_S = 24 * 3600

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_TITLE_SYMBOL = re.compile(r"<title>[^<]*?\b([A-Z0-9]{2,8})\.PL\b")
_LEGAL_FORM = re.compile(
    r"\b(spolka akcyjna|s\.?\s?a\.?|se|plc|ltd|nv|n\.v\.|ab|asi|w restrukturyzacji)\s*$"
)
SCHEMA: dict[str, pl.DataType] = {
    "isin": pl.String(),
    "url": pl.String(),
    "symbol": pl.String(),  # XTB's symbol, e.g. PKO.PL
    "logo": pl.String(),  # XTB's logo for it, when one exists
    "checked_at": pl.Datetime("us", "UTC"),
}


@dataclass(frozen=True)
class Company:
    """What is known about a stock, to guess its XTB slug from."""

    isin: str
    names: tuple[str, ...]
    ticker: str | None = None


def slugify(name: str) -> str:
    """``Przedsiębiorstwo X S.A.`` → ``przedsiebiorstwo-x``; legal forms are dropped."""
    t = normalize(name).replace("&", " and ")
    for _ in range(2):
        t = _LEGAL_FORM.sub("", t).strip(" ,.-")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def candidates(company: Company) -> list[str]:
    """Slugs XTB could use for ``company``, most likely first, without duplicates."""
    out: list[str] = []
    for name in company.names:
        if name:
            out.append(slugify(name))
    if company.ticker:
        out.append(f"{slugify(company.ticker)}-pl")
    # XTB sometimes uses just the brand: "Santander Bank Polska S.A." -> santander
    for name in company.names:
        first = slugify(name).split("-")[0] if name else ""
        if len(first) >= 5:
            out.append(first)
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def match(candidates_: list[str], slugs: set[str]) -> list[str]:
    """Candidates found in the sitemap, ignoring hyphens: PKOBP finds pko-bp."""
    squashed = {s.replace("-", ""): s for s in sorted(slugs)}
    out: list[str] = []
    for c in candidates_:
        hit = c if c in slugs else squashed.get(c.replace("-", ""))
        if hit and hit not in out:
            out.append(hit)
    return out


def sitemap_slugs(client: PoliteClient) -> set[str]:
    """Every ``/pl/akcje/<slug>`` in XTB's stock sitemap (an index of sitemap files)."""
    slugs: set[str] = set()
    todo: list[str] = [SITEMAP_URL]
    seen: set[str] = set()
    while todo:
        url = todo.pop()
        if url in seen or len(seen) > 20:
            continue
        seen.add(url)
        text = client.get(url, max_age_s=SITEMAP_MAX_AGE_S).text()
        for loc in _LOC.findall(text):
            path = urlparse(loc).path
            if loc.endswith(".xml"):
                todo.append(loc)
            elif path.startswith("/pl/akcje/"):
                slugs.add(path.removeprefix("/pl/akcje/").strip("/"))
    return slugs


@dataclass(frozen=True)
class Page:
    """A verified XTB stock page."""

    url: str
    symbol: str | None


def verify(client: PoliteClient, slug: str, isin: str) -> Page | None:
    """XTB's page for ``slug`` if it is about ``isin`` (with XTB's symbol), else None."""
    url = PAGE_URL.format(slug=slug)
    res = client.get(url)
    if urlparse(res.url).path.rstrip("/") != f"/pl/akcje/{slug}":
        return None  # redirected: no such page
    text = res.text()
    if isin not in text:
        return None
    m = _TITLE_SYMBOL.search(text)
    return Page(url, f"{m.group(1)}.PL" if m else None)


def logo_for(client: PoliteClient, symbol: str | None) -> str | None:
    """XTB's logo URL for ``symbol`` if XTB serves one (it answers 403 when it has none)."""
    if not symbol:
        return None
    url = LOGO_URL.format(code=symbol.removesuffix(".PL").lower())
    try:
        res = client.get(url, max_age_s=RECHECK_DAYS * 86400)
    except Exception:
        return None
    ok = res.status == 200 and "svg" in res.content_type and b"<svg" in res.content[:2000]
    return url if ok else None


def read_links(data_dir: Path) -> dict[str, str]:
    """ISIN → verified XTB page, for the site."""
    df = lake.read_state(data_dir, STATE)
    if df is None:
        return {}
    return {r["isin"]: r["url"] for r in df.iter_rows(named=True) if r["url"]}


def read_logos(data_dir: Path) -> dict[str, str]:
    """ISIN → XTB's logo for the stock, where one was found."""
    df = lake.read_state(data_dir, STATE)
    if df is None or "logo" not in df.columns:
        return {}
    return {r["isin"]: r["logo"] for r in df.iter_rows(named=True) if r["logo"]}


def refresh(
    client: PoliteClient,
    data_dir: Path,
    companies: Iterable[Company],
    *,
    now: datetime | None = None,
    max_checks: int = MAX_CHECKS,
) -> int:
    """Check the companies whose link is unknown or stale. Returns how many were checked."""
    now = now or datetime.now(UTC)
    old = lake.read_state(data_dir, STATE)
    rows = {r["isin"]: r for r in old.iter_rows(named=True)} if old is not None else {}
    stale = now - timedelta(days=RECHECK_DAYS)
    due = [
        c
        for c in {c.isin: c for c in companies}.values()
        if c.isin not in rows
        or rows[c.isin]["checked_at"] < stale
        or "symbol" not in rows[c.isin]  # checked before symbols and logos were recorded
    ][:max_checks]
    if not due:
        return 0
    slugs = sitemap_slugs(client)
    for c in due:
        page = None
        for slug in match(candidates(c), slugs)[:MAX_CANDIDATES]:
            page = verify(client, slug, c.isin)
            if page:
                break
        rows[c.isin] = {
            "isin": c.isin,
            "url": page.url if page else None,
            "symbol": page.symbol if page else None,
            "logo": logo_for(client, page.symbol) if page else None,
            "checked_at": now,
        }
    df = pl.DataFrame([{k: r.get(k) for k in SCHEMA} for r in rows.values()], schema=SCHEMA).sort(
        "isin"
    )
    lake.write_state(data_dir, STATE, df)
    found = sum(1 for c in due if rows[c.isin]["url"])
    log.info("xtb links: checked %d, found %d", len(due), found)
    return len(due)


def recent_companies(
    duckdb_file: Path, since: datetime, also: list[str] | None = None
) -> list[Company]:
    """Stocks the site links to: recent signal events, plus the ISINs in ``also``.

    ``also`` carries the stock of the week and its runners-up.
    """
    import duckdb

    con = duckdb.connect(str(duckdb_file))
    try:
        rows = con.execute(
            "select isin, any_value(short_name), any_value(issuer_name), any_value(ticker), "
            "any_value(market) from reports where isin is not null and (list_contains(?, isin) "
            "or (known_at >= ? and signal_type in "
            "('insider_tx', 'holder_threshold', 'inside_info'))) "
            "group by isin order by isin",
            [also or [], since],
        ).fetchall()
    finally:
        con.close()
    return [
        Company(isin, (short or "", issuer or ""), ticker if market == "NC" else None)
        for isin, short, issuer, ticker, market in rows
    ]
