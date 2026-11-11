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
STATE = "xtb_links"
RECHECK_DAYS = 30
MAX_CHECKS = 40
MAX_CANDIDATES = 3
SITEMAP_MAX_AGE_S = 24 * 3600

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LEGAL_FORM = re.compile(
    r"\b(spolka akcyjna|s\.?\s?a\.?|se|plc|ltd|nv|n\.v\.|ab|asi|w restrukturyzacji)\s*$"
)
SCHEMA: dict[str, pl.DataType] = {
    "isin": pl.String(),
    "url": pl.String(),
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
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


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


def verify(client: PoliteClient, slug: str, isin: str) -> str | None:
    """The page URL if XTB's page for ``slug`` is about ``isin``, else None."""
    url = PAGE_URL.format(slug=slug)
    res = client.get(url)
    if urlparse(res.url).path.rstrip("/") != f"/pl/akcje/{slug}":
        return None  # redirected: no such page
    return url if isin in res.text() else None


def read_links(data_dir: Path) -> dict[str, str]:
    """ISIN → verified XTB page, for the site."""
    df = lake.read_state(data_dir, STATE)
    if df is None:
        return {}
    return {r["isin"]: r["url"] for r in df.iter_rows(named=True) if r["url"]}


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
        if c.isin not in rows or rows[c.isin]["checked_at"] < stale
    ][:max_checks]
    if not due:
        return 0
    slugs = sitemap_slugs(client)
    for c in due:
        url = None
        for slug in [s for s in candidates(c) if s in slugs][:MAX_CANDIDATES]:
            url = verify(client, slug, c.isin)
            if url:
                break
        rows[c.isin] = {"isin": c.isin, "url": url, "checked_at": now}
    df = pl.DataFrame(list(rows.values()), schema=SCHEMA).sort("isin")
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
