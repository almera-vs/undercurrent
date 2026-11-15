"""GPW's daily market indicators: shares, market value, book value, P/E, sector.

The exchange publishes, for every main-market company, a table at ``gpw.pl/wskazniki``
(domestic and foreign companies): ISIN, ticker, sector code and name, shares issued, market
value and book value in millions of złoty, the date and type of the latest financial report,
P/BV, P/E and dividend yield. It is plain HTML without bot protection (SOURCES § 13).

Only today's table is free (the exchange sells the history), so each nightly run lands it
into ``bronze/indicators_gpw/date=<session>/`` and the history grows from the first run
on, point in time like every other bronze table. A backfill cannot reach the past.

What Undercurrent keeps from it, and why:

* **shares** and the **sector**: there is no other source of either (ESEF filings almost
  never tag the share count; the lake has no sector per stock);
* **book value** and the **earnings** the table implies (market value ÷ P/E): the
  fundamentals since mid-2024, when the ESEF repository stops;
* the table's own P/E, P/BV and market value are kept as published, but every ratio the
  site shows is computed from Undercurrent's close and these shares (``valuation.py``),
  because the page does not say which session its prices are from.

The report type is kept as published (``H`` or ``J``; GPW does not explain the letters on
the page, and they are not used).
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from undercurrent import lake
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)

URL = "https://www.gpw.pl/wskazniki"
TABLE = "indicators_gpw"
TABLES = {"footable_K": "domestic", "footable_Z": "foreign"}
SCHEMA: dict[str, Any] = {
    "trade_date": pl.Date,
    "isin": pl.String,
    "ticker": pl.String,
    "listing": pl.String,
    "sector_code": pl.Int64,
    "sector": pl.String,
    "shares": pl.Int64,
    "market_value_pln": pl.Float64,
    "book_value_pln": pl.Float64,
    "report_date": pl.Date,
    "report_type": pl.String,
    "pbv": pl.Float64,
    "pe": pl.Float64,
    "dividend_yield": pl.Float64,
    "known_at": pl.Datetime("us", "UTC"),
    "source_url": pl.String,
}
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TITLE = re.compile(r'title="([^"]*)"')


def _text(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()


def _number(s: str) -> float | None:
    """``"13 921 975"`` → 13921975.0, ``"30,49"`` → 30.49; ``x``, ``--`` and blanks → None."""
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _date(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def parse(page: str, day: date, known_at: datetime) -> pl.DataFrame:
    """Every company row of both tables (domestic and foreign) of the indicators page."""
    rows: list[dict[str, Any]] = []
    for table_id, listing in TABLES.items():
        i = page.find(f'id="{table_id}"')
        if i < 0:
            continue
        body_start = page.find("<tbody>", i)
        body_end = page.find("</tbody>", body_start)
        if body_start < 0 or body_end < 0:
            continue
        for raw in _ROW.findall(page[body_start:body_end]):
            cells = _CELL.findall(raw)
            if len(cells) < 12:
                continue
            isin = _text(cells[1])
            if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", isin):
                continue
            title = _TITLE.search(cells[3])
            code = _number(_text(cells[3]).rstrip("?").strip())
            shares = _number(_text(cells[4]))
            mv, bv = _number(_text(cells[5])), _number(_text(cells[6]))
            rows.append(
                {
                    "trade_date": day,
                    "isin": isin,
                    "ticker": _text(cells[2]),
                    "listing": listing,
                    "sector_code": None if code is None else int(code),
                    "sector": html.unescape(title.group(1)) if title else None,
                    "shares": None if shares is None else int(shares),
                    "market_value_pln": None if mv is None else mv * 1e6,
                    "book_value_pln": None if bv is None else bv * 1e6,
                    "report_date": _date(_text(cells[7])),
                    "report_type": _text(cells[8]) or None,
                    "pbv": _number(_text(cells[9])),
                    "pe": _number(_text(cells[10])),
                    "dividend_yield": (
                        None if (dy := _number(_text(cells[11]))) is None else dy / 100
                    ),
                    "known_at": known_at,
                    "source_url": URL,
                }
            )
    return pl.DataFrame(rows, schema=SCHEMA)


def ingest(client: PoliteClient, root: Path, day: date, *, now: datetime | None = None) -> int:
    """Land today's indicators table as the partition of session ``day`` (once per session).

    Run in the evening of ``day``: the table is the exchange's latest, so it cannot stand in
    for an earlier session, and a backfill does not call this.
    """
    if lake.partition_exists(root, "bronze", TABLE, day):
        return 0
    now = now or datetime.now(UTC)
    res = client.get(URL, use_cache=False)
    df = parse(res.text(), day, now)
    if df.height == 0:
        raise ValueError("the GPW indicators page has no company rows (layout changed?)")
    lake.append_partition(root, "bronze", TABLE, day, df.sort("isin"))
    log.info("indicators %s: %d companies", day, df.height)
    return df.height


def latest(root: Path, day: date) -> pl.DataFrame | None:
    """Each ISIN's latest indicators row from a session up to ``day`` (None without any)."""
    df = lake.read_table(root, "bronze", TABLE)
    if df is None or not df.height:
        return None
    return (
        df.filter(pl.col("trade_date") <= day)
        .sort(["isin", "trade_date"])
        .group_by("isin", maintain_order=True)
        .last()
    )
