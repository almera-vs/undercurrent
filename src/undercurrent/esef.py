"""Company fundamentals from ESEF annual reports (the start of DECISIONS item "fundamentals").

Since 2021 every issuer on a regulated EU market files its annual report in ESEF: an XHTML
document with every number in the financial statements tagged in iXBRL. The exchange's copy
of the attachment sits behind bot protection (SOURCES § 4), so the filings come from
**filings.xbrl.org**, the public repository run by XBRL International, which converts each
filing to xBRL-JSON. Its LEIs are mapped to ISINs with **GLEIF**'s public API.

What is kept: a short list of consolidated headline facts (``CONCEPTS``): facts without
any extra dimension, in a currency (or per share), for every period the filing reports.
Narrative text blocks, notes and dimensional breakdowns are dropped. The 2–3 MB document is
read in memory and never cached on disk.

Point in time: a fact's ``known_at`` is the moment the repository added the filing. That is
later than the issuer's own publication (an annual report is due within four months of the
year end), so it can only make the data look older than it was, never newer.

Coverage (probed 2026-11-12): 876 Polish filings from 313 entities, fiscal years 2020–2024,
none added after 2024-07. Usable as history; it is not a live source for fiscal 2024–2025.

Tables:

* ``bronze/fundamentals_esef/date=<day added>/part-N.parquet`` — one row per fact,
  immutable, keyed by ``fact_key``;
* ``state/esef_filings`` — filings already read (never fetched twice);
* ``state/lei_isins`` — the ISINs GLEIF lists for each LEI.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from undercurrent import lake
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)

REPO_BASE = "https://filings.xbrl.org"
FILINGS_URL = f"{REPO_BASE}/api/filings"
GLEIF_ISINS_URL = "https://api.gleif.org/api/v1/lei-records/{lei}/isins"
PAGE_SIZE = 200
TABLE = "fundamentals_esef"
FILINGS_STATE = "esef_filings"
LEI_STATE = "lei_isins"
DEFAULT_MAX_FILINGS = 25  # per run: ~1 minute of polite requests and ~70 MB read

# IFRS concept → short name. Revenue has two common tags; both are kept under their names.
CONCEPTS: dict[str, str] = {
    "ifrs-full:Revenue": "revenue",
    "ifrs-full:RevenueFromContractsWithCustomers": "revenue_contracts",
    "ifrs-full:GrossProfit": "gross_profit",
    "ifrs-full:ProfitLossFromOperatingActivities": "operating_profit",
    "ifrs-full:ProfitLoss": "net_profit",
    "ifrs-full:ProfitLossAttributableToOwnersOfParent": "net_profit_parent",
    "ifrs-full:Assets": "assets",
    "ifrs-full:Liabilities": "liabilities",
    "ifrs-full:Equity": "equity",
    "ifrs-full:EquityAttributableToOwnersOfParent": "equity_parent",
    "ifrs-full:CashAndCashEquivalents": "cash",
    "ifrs-full:CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "ifrs-full:BasicEarningsLossPerShare": "eps_basic",
}
_PLAIN_DIMS = {"concept", "entity", "period", "unit"}

FACT_SCHEMA: dict[str, Any] = {
    "fact_key": pl.String,
    "filing_id": pl.String,
    "lei": pl.String,
    "fiscal_period_end": pl.Date,
    "concept": pl.String,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "value": pl.Float64,
    "unit": pl.String,
    "decimals": pl.Int64,
    "known_at": pl.Datetime("us", "UTC"),
}


@dataclass(frozen=True)
class Filing:
    """One filing as the repository lists it."""

    id: str
    lei: str
    period_end: date
    added: datetime  # UTC
    json_url: str
    errors: int


def _abs(url: str) -> str:
    return url if url.startswith("http") else REPO_BASE + url


def list_filings(client: PoliteClient, country: str = "PL") -> Iterator[Filing]:
    """Every filing of ``country`` in the repository, page by page."""
    url: str | None = (
        f"{FILINGS_URL}?filter%5Bcountry%5D={country}&page%5Bsize%5D={PAGE_SIZE}&sort=date_added"
    )
    while url:
        doc = json.loads(client.get(url, use_cache=False).content)
        for f in doc.get("data", []):
            a = f["attributes"]
            if not a.get("json_url"):
                continue
            lei = str(a["fxo_id"]).split("-", 1)[0]
            yield Filing(
                id=str(f["id"]),
                lei=lei,
                period_end=date.fromisoformat(a["period_end"]),
                added=datetime.fromisoformat(a["date_added"]).replace(tzinfo=UTC),
                json_url=_abs(a["json_url"]),
                errors=int(a.get("error_count") or 0),
            )
        nxt = doc.get("links", {}).get("next")
        url = _abs(nxt) if nxt else None


def _date(s: str) -> date:
    return date.fromisoformat(s[:10])


def facts_from_json(doc: dict[str, Any], filing: Filing) -> pl.DataFrame:
    """The headline facts of one xBRL-JSON document (see ``CONCEPTS``)."""
    rows: dict[str, dict[str, Any]] = {}
    for f in doc.get("facts", {}).values():
        dims = f.get("dimensions", {})
        name = CONCEPTS.get(dims.get("concept", ""))
        if name is None or set(dims) - _PLAIN_DIMS:
            continue
        unit = str(dims.get("unit", ""))
        if not unit.startswith("iso4217:"):
            continue
        try:
            value = float(f["value"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        period = str(dims.get("period", ""))
        if not period:
            continue
        # xBRL-JSON gives period ends as the next day's midnight: 2024-04-01T00:00 ends 31 March
        if "/" in period:
            start_s, end_s = period.split("/", 1)
            start: date | None = _date(start_s)
            end = _date(end_s) - timedelta(days=1)
        else:
            start, end = None, _date(period) - timedelta(days=1)
        key = f"{filing.id}:{name}:{start}:{end}:{unit}"
        if key in rows:
            continue
        dec = f.get("decimals")
        rows[key] = {
            "fact_key": key,
            "filing_id": filing.id,
            "lei": filing.lei,
            "fiscal_period_end": filing.period_end,
            "concept": name,
            "period_start": start,
            "period_end": end,
            "value": value,
            "unit": unit.removeprefix("iso4217:"),
            "decimals": int(dec) if isinstance(dec, int | float) and math.isfinite(dec) else None,
            "known_at": filing.added,
        }
    return pl.DataFrame(list(rows.values()), schema=FACT_SCHEMA).sort("fact_key")


def isins_for(client: PoliteClient, lei: str) -> list[str]:
    """The ISINs GLEIF links to ``lei`` (shares and bonds alike)."""
    doc = json.loads(client.get(GLEIF_ISINS_URL.format(lei=lei), use_cache=False).content)
    return sorted({str(x["attributes"]["isin"]) for x in doc.get("data", [])})


def ingest(
    client: PoliteClient,
    root: Path,
    *,
    max_filings: int = DEFAULT_MAX_FILINGS,
    now: datetime | None = None,
) -> dict[str, int]:
    """Read up to ``max_filings`` filings not read before, oldest first; map new LEIs."""
    now = now or datetime.now(UTC)
    done_df = lake.read_state(root, FILINGS_STATE)
    done = set(done_df["filing_id"].to_list()) if done_df is not None else set()
    todo = [f for f in list_filings(client) if f.id not in done][:max_filings]
    leis_df = lake.read_state(root, LEI_STATE)
    known_leis = set(leis_df["lei"].to_list()) if leis_df is not None else set()
    new_done, new_leis = [], []
    n_facts = 0
    for f in todo:
        doc = json.loads(client.get(f.json_url, use_cache=False).content)
        facts = facts_from_json(doc, f)
        if facts.height:
            lake.append_partition(root, "bronze", TABLE, f.added.date(), facts, key="fact_key")
        n_facts += facts.height
        new_done.append(
            {
                "filing_id": f.id,
                "lei": f.lei,
                "period_end": f.period_end,
                "added": f.added,
                "errors": f.errors,
                "facts": facts.height,
                "read_at": now,
            }
        )
        if f.lei not in known_leis:
            known_leis.add(f.lei)
            new_leis += [
                {"lei": f.lei, "isin": i, "checked_at": now} for i in isins_for(client, f.lei)
            ]
    if new_done:
        df = pl.DataFrame(new_done)
        lake.write_state(
            root,
            FILINGS_STATE,
            df if done_df is None else pl.concat([done_df, df], how="diagonal_relaxed"),
        )
    if new_leis:
        df = pl.DataFrame(new_leis)
        lake.write_state(
            root,
            LEI_STATE,
            df if leis_df is None else pl.concat([leis_df, df], how="diagonal_relaxed"),
        )
    log.info(
        "esef: %d filings read, %d facts, %d new LEI-ISIN links",
        len(new_done),
        n_facts,
        len(new_leis),
    )
    return {"filings": len(new_done), "facts": n_facts, "isin_links": len(new_leis)}


def write_silver(root: Path, work: Path) -> Path:
    """Every fact with every ISIN of its issuer, for dbt's ``fundamentals`` (empty if none)."""
    out = work / "silver" / "fundamentals.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    fundamentals_as_of(root, datetime.max.replace(tzinfo=UTC)).write_parquet(out)
    return out


# A fiscal year is a duration of 11–13 months; the prior year-end is about 12 months back.
_YEAR_DAYS = (330, 400)
_PRIOR_DAYS = (355, 375)
SNAPSHOT_SCHEMA: dict[str, Any] = {
    "isin": pl.String,
    "known_at": pl.Datetime("us", "UTC"),
    "fiscal_year_end": pl.Date,
    "eps_pln": pl.Float64,
    "profitability": pl.Float64,
    "asset_growth": pl.Float64,
    "accruals": pl.Float64,
}


def _latest(
    facts: list[dict[str, Any]], concept: str, end: date, *, duration: bool
) -> float | None:
    """The latest-known value of ``concept`` for the year (or instant) ending ``end``."""
    best: dict[str, Any] | None = None
    for f in facts:
        if f["concept"] != concept or f["period_end"] != end:
            continue
        if duration:
            start = f["period_start"]
            if start is None or not _YEAR_DAYS[0] <= (end - start).days <= _YEAR_DAYS[1]:
                continue
        elif f["period_start"] is not None:
            continue
        if best is None or f["known_at"] > best["known_at"]:
            best = f
    return None if best is None else float(best["value"])


def _prior_end(facts: list[dict[str, Any]], end: date) -> date | None:
    """The balance-sheet date about a year before ``end`` that the facts report assets for."""
    ends = {
        f["period_end"]
        for f in facts
        if f["concept"] == "assets"
        and f["period_start"] is None
        and _PRIOR_DAYS[0] <= (end - f["period_end"]).days <= _PRIOR_DAYS[1]
    }
    return max(ends) if ends else None


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b <= 0 else a / b


def snapshots(facts: pl.DataFrame) -> pl.DataFrame:
    """What the latest annual report said, as known at each moment a filing arrived.

    One row per (ISIN, filing time): the fiscal year is the latest one with facts known by
    then, and every value uses only facts known by then (a restatement in a later filing
    replaces the figure from that filing on, never before).

    * ``eps_pln``: basic EPS, PLN filers only (the price is in PLN);
    * ``profitability``: operating profit / total assets;
    * ``asset_growth``: total assets / the prior year-end's - 1 (the investment factor);
    * ``accruals``: (net profit - operating cash flow) / total assets.
    """
    if not facts.height:
        return pl.DataFrame(schema=SNAPSHOT_SCHEMA)
    rows: list[dict[str, Any]] = []
    for (isin,), grp in facts.sort("known_at").group_by(["isin"], maintain_order=True):
        recs = grp.to_dicts()
        for when in sorted({r["known_at"] for r in recs}):
            known = [r for r in recs if r["known_at"] <= when]
            fy = max(r["fiscal_period_end"] for r in known)
            mine = [r for r in known if r["fiscal_period_end"] <= fy]
            assets = _latest(mine, "assets", fy, duration=False)
            prior = _prior_end(mine, fy)
            assets_prev = _latest(mine, "assets", prior, duration=False) if prior else None
            op = _latest(mine, "operating_profit", fy, duration=True)
            net = _latest(mine, "net_profit", fy, duration=True)
            cfo = _latest(mine, "operating_cash_flow", fy, duration=True)
            eps = _latest(
                [r for r in mine if r["unit"] == "PLN/xbrli:shares"], "eps_basic", fy, duration=True
            )
            growth = _ratio(assets, assets_prev)
            rows.append(
                {
                    "isin": isin,
                    "known_at": when,
                    "fiscal_year_end": fy,
                    "eps_pln": eps,
                    "profitability": _ratio(op, assets),
                    "asset_growth": None if growth is None else growth - 1,
                    "accruals": _ratio(None if net is None or cfo is None else net - cfo, assets),
                }
            )
    return pl.DataFrame(rows, schema=SNAPSHOT_SCHEMA)


def fundamentals_as_of(root: Path, as_of: datetime) -> pl.DataFrame:
    """Facts known by ``as_of``, with every ISIN of the issuer: the point-in-time view."""
    parts = sorted((root / "bronze" / TABLE).glob("*/*.parquet"))
    leis = lake.read_state(root, LEI_STATE)
    if not parts or leis is None:
        return pl.DataFrame(schema={**FACT_SCHEMA, "isin": pl.String})
    facts = pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed")
    ts = pl.lit(as_of).cast(pl.Datetime("us", "UTC"))
    return (
        facts.filter(pl.col("known_at") <= ts)
        .join(leis.select("lei", "isin"), on="lei", how="inner")
        .sort(["isin", "concept", "period_end", "known_at"])
    )
