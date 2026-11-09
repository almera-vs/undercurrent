"""Map report issuers to listed instruments (ISIN + ticker/short name).

Order of precedence, first hit wins:

1. ``seeds/issuer_overrides.csv``: hand-maintained, keyed by issuer name or ISIN.
2. Exact ISIN: the listing shows the issuer's share ISIN, which joins straight to prices.
3. rapidfuzz on normalised names, as a fallback only. Score ≥ ``ACCEPT`` is accepted; a
   score in ``[REVIEW, ACCEPT)`` goes to ``silver/resolve_review`` and stays unresolved.
   Low-confidence matches are never silently guessed.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import polars as pl
from rapidfuzz import fuzz, process

from undercurrent import lake
from undercurrent.classify import normalize

log = logging.getLogger(__name__)

ACCEPT = 95.0
REVIEW = 80.0
OVERRIDES = Path("seeds/issuer_overrides.csv")

_LEGAL_FORMS = re.compile(
    r"\b(spolka akcyjna|s\.?a\.?|spolka europejska|se|sp\.? z o\.?o\.?|spolka komandytowo"
    r"[- ]akcyjna|s\.?k\.?a\.?|w restrukturyzacji|w upadlosci|w likwidacji|n\.?v\.?|plc|ag|"
    r"asi|alternatywna spolka inwestycyjna|fundusz inwestycyjny zamkniety|fiz)\b"
)


def name_key(name: str | None) -> str:
    """Normalised issuer name without legal-form suffixes, for fuzzy matching."""
    t = normalize(name)
    t = _LEGAL_FORMS.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def load_overrides(path: Path = OVERRIDES) -> dict[str, str]:
    """``issuer_name or isin → isin`` from the overrides seed (comment column ignored)."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {
            row["match"].strip(): row["isin"].strip()
            for row in csv.DictReader(fh)
            if row.get("match") and row.get("isin")
        }


def instruments(root: Path) -> pl.DataFrame:
    """Universe of instruments seen in price files: isin, market, short name, ticker."""
    frames = []
    gpw = lake.read_table(root, "bronze", "prices_gpw")
    if gpw is not None:
        frames.append(
            gpw.select(
                "isin",
                pl.lit("GPW").alias("market"),
                pl.col("name").alias("short_name"),
                pl.col("name").alias("ticker"),
                "trade_date",
            )
        )
    nc = lake.read_table(root, "bronze", "prices_nc")
    if nc is not None:
        frames.append(
            nc.select(
                "isin",
                pl.lit("NC").alias("market"),
                pl.col("name").alias("short_name"),
                "ticker",
                "trade_date",
            )
        )
    if not frames:
        return pl.DataFrame(
            schema={
                "isin": pl.String,
                "market": pl.String,
                "short_name": pl.String,
                "ticker": pl.String,
            }
        )
    return (
        pl.concat(frames)
        .drop_nulls("isin")
        .sort("trade_date")
        .unique("isin", keep="last")
        .drop("trade_date")
        .sort("isin")
    )


def resolve_reports(
    root: Path, work: Path, overrides_path: Path = OVERRIDES
) -> pl.DataFrame | None:
    """Write ``silver/report_instrument`` (and ``silver/resolve_review``); return the former."""
    reports = lake.read_table(root, "bronze", "reports")
    if reports is None:
        return None
    issuers = (
        reports.select("report_id", "issuer_name", "isin")
        .unique("report_id", keep="last")
        .sort("report_id")
    )
    uni = instruments(root)
    known_isins = set(uni["isin"].to_list())
    overrides = load_overrides(overrides_path)

    # Fuzzy candidates: map name keys of issuers we *can* match by ISIN to that ISIN, plus
    # instrument short names. That lets an issuer without an ISIN on one report match the
    # same issuer's other reports.
    choices: dict[str, str] = {}
    for name, isin in issuers.drop_nulls("isin").select("issuer_name", "isin").iter_rows():
        if isin in known_isins:
            choices.setdefault(name_key(name), isin)
    for short, isin in uni.select("short_name", "isin").iter_rows():
        choices.setdefault(name_key(short), isin)
    keys = sorted(k for k in choices if k)

    out, review = [], []
    for report_id, name, isin in issuers.iter_rows():
        method, score, resolved = "unresolved", None, None
        if name in overrides or (isin and isin in overrides):
            resolved, method, score = overrides.get(name) or overrides[isin], "override", 100.0
        elif isin and isin in known_isins:
            resolved, method, score = isin, "isin", 100.0
        elif keys:
            hit = process.extractOne(name_key(name), keys, scorer=fuzz.token_sort_ratio)
            if hit is not None:
                key, s, _ = hit
                if s >= ACCEPT:
                    resolved, method, score = choices[key], "fuzzy", float(s)
                elif s >= REVIEW:
                    review.append(
                        {
                            "report_id": report_id,
                            "issuer_name": name,
                            "candidate_isin": choices[key],
                            "candidate_key": key,
                            "score": float(s),
                        }
                    )
                    method, score = "review", float(s)
        out.append(
            {
                "report_id": report_id,
                "resolved_isin": resolved,
                "resolve_method": method,
                "resolve_score": score,
            }
        )
    df = pl.DataFrame(
        out,
        schema={
            "report_id": pl.String,
            "resolved_isin": pl.String,
            "resolve_method": pl.String,
            "resolve_score": pl.Float64,
        },
    )
    df = df.join(uni.rename({"isin": "resolved_isin"}), on="resolved_isin", how="left")
    lake.write_frame(df.sort("report_id"), work / "silver" / "report_instrument.parquet")
    rv = pl.DataFrame(
        review,
        schema={
            "report_id": pl.String,
            "issuer_name": pl.String,
            "candidate_isin": pl.String,
            "candidate_key": pl.String,
            "score": pl.Float64,
        },
    )
    lake.write_frame(rv.sort("report_id"), work / "silver" / "resolve_review.parquet")
    log.info(
        "resolve: %s; review list %d",
        dict(df.group_by("resolve_method").len().iter_rows()),
        rv.height,
    )
    return df
