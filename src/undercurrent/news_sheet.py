"""The hand-labelled sheet a judge of big moves' news will be scored on.

The question for each move: which item published before the close, if any, explains it in
its direction? A labeller answers it here, blind to any rule's or model's answer, and a
judge (rules, a model) is later scored against the answers.

**A fresh population.** The coverage gate's moves (liquid, ``|z| >= 3``) and its holdout
(``2.5 <= |z| < 3``) were labelled and tuned on in 2026-11, so they would flatter a judge.
The default here is liquid stocks' moves of ``2 <= |z| < 2.5``, which no one has labelled.
They are also the size of move the digest lists. Only moves whose news has been fetched
from every source are taken (not ``pending``).

**What is listed.** The move's timeline (``news_timeline.timeline``) from the previous close
to this close: filings that can move a price and headlines about the company, oldest first,
numbered from 1. Routine filings and anything after the close are left out, since neither
can explain the session. Row 0 of each move reads "nothing here explains the move".

**Labelling.** ``y`` in ``explains`` on exactly one row per move: the item that explains it in
its direction, or row 0. ``note`` is free text. ``check`` counts what is done.

Moves with nothing published are not listed: they are "nothing" by definition, and the
summary gives their share, so coverage is still measured over every move.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from undercurrent import news_coverage as nc
from undercurrent import news_timeline as nt
from undercurrent.news import NewsItem
from undercurrent.news_common import CompanyInfo, NameIndex, own_hosts

LABEL = "explains"
NOTHING = "(nothing here explains the move)"
YES = frozenset({"y", "yes", "1", "x", "t", "true"})

SHEET_SCHEMA: dict[str, Any] = {
    "move_id": pl.String,
    "ticker": pl.String,
    "name": pl.String,
    "trade_date": pl.Date,
    "move": pl.String,  # against the market, e.g. "+3.1%"
    "z": pl.Float64,
    "direction": pl.String,  # up / down
    "item_no": pl.Int64,
    "when": pl.String,  # DD.MM HH:MM Warsaw, or DD.MM for a date-only headline
    "kind": pl.String,  # filing / headline / nothing
    "source": pl.String,
    "title": pl.String,
    "url": pl.String,
    LABEL: pl.String,
    "note": pl.String,
}


@dataclass(frozen=True)
class Population:
    """The moves a sheet was drawn from."""

    moves: int  # every move in the band, news fetched
    with_items: int  # of which something was published before the close
    sampled_up: int
    sampled_down: int


def candidates(attr: pl.DataFrame, *, tier: str, min_z: float, max_z: float) -> pl.DataFrame:
    """Moves in the band whose news is complete, from ``news_coverage.attribute``'s table."""
    return attr.filter(
        pl.col("liquidity_tier") == tier,
        pl.col("surprise").abs() >= min_z,
        pl.col("surprise").abs() < max_z,
        pl.col("cause") != "pending",
    ).sort("trade_date", "isin")


def before_close(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The items that can explain the session, oldest first."""
    keep = [e for e in entries if not e["routine"] and not e["after_close"]]
    return sorted(keep, key=lambda e: (e["at"], e["id"]))


def build(
    moves: pl.DataFrame,
    store: nc.Store,
    companies: dict[str, CompanyInfo],
    reports: pl.DataFrame,
    names: NameIndex,
    *,
    per_sign: int,
    seed: int,
) -> tuple[pl.DataFrame, Population]:
    """The sheet for a random ``per_sign`` rises and ``per_sign`` falls with something published."""
    listed: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {"up": [], "down": []}
    items_cache: dict[str, list[NewsItem]] = {}
    for r in moves.iter_rows(named=True):
        info = companies.get(r["isin"])
        if info is None:
            continue
        items = items_cache.setdefault(r["isin"], store.items(r["isin"]))
        mover = nt.Mover(r["isin"], float(r["surprise"]), r["resid"], r["liquidity_tier"])
        tl = nt.timeline(
            mover, info, items, reports, names, r["trade_date"], google_hosts=own_hosts(items, info)
        )
        entries = before_close(tl)
        if entries:
            listed["up" if r["surprise"] > 0 else "down"].append((r, entries))
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    taken: dict[str, int] = {}
    for direction, pool in listed.items():
        idx = sorted(rng.sample(range(len(pool)), min(per_sign, len(pool))))
        taken[direction] = len(idx)
        for k in idx:
            r, entries = pool[k]
            rows += _move_rows(r, companies[r["isin"]], direction, entries)
    rows.sort(key=lambda x: (x["trade_date"], x["move_id"], x["item_no"]))
    pop = Population(
        moves=moves.height,
        with_items=len(listed["up"]) + len(listed["down"]),
        sampled_up=taken["up"],
        sampled_down=taken["down"],
    )
    return pl.DataFrame(rows, schema=SHEET_SCHEMA), pop


def _move_rows(
    r: dict[str, Any], info: CompanyInfo, direction: str, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    day: date = r["trade_date"]
    head = {
        "move_id": f"{r['isin']}:{day.isoformat()}",
        "ticker": info.short_name,
        "name": info.issuer_name or info.short_name,
        "trade_date": day,
        "move": f"{r['resid']:+.1%}" if r["resid"] is not None else "",
        "z": round(float(r["surprise"]), 2),
        "direction": direction,
        LABEL: None,
        "note": None,
    }
    out = [
        {
            **head,
            "item_no": 0,
            "when": "",
            "kind": "nothing",
            "source": "",
            "title": NOTHING,
            "url": "",
        }
    ]
    for n, e in enumerate(entries, start=1):
        hm, dm = nt.local_time(e["at"], e["precision"])
        out.append(
            {
                **head,
                "item_no": n,
                "when": f"{dm} {hm}".strip(),
                "kind": e["kind"],
                "source": e["source"],
                "title": e["title"],
                "url": e["url"] or "",
            }
        )
    return out


@dataclass(frozen=True)
class Progress:
    """How far the labelling has got."""

    moves: int
    labelled: int
    nothing: int  # labelled "row 0"
    errors: list[str]  # move ids with more than one row marked


def check(sheet: pl.DataFrame) -> Progress:
    """Count labelled moves; a move with two rows marked is an error, not a label."""
    marked = sheet.with_columns(
        yes=pl.col(LABEL).fill_null("").str.strip_chars().str.to_lowercase().is_in(list(YES))
    )
    per = marked.group_by("move_id").agg(
        n=pl.col("yes").sum(),
        zero=(pl.col("yes") & (pl.col("item_no").cast(pl.Int64, strict=False) == 0)).any(),
    )
    errors = sorted(per.filter(pl.col("n") > 1)["move_id"].to_list())
    ok = per.filter(pl.col("n") == 1)
    return Progress(per.height, ok.height, int(ok["zero"].sum()), errors)


def instructions(pop: Population, band: str, csv_name: str) -> str:
    """The note written beside the sheet."""
    share = pop.with_items / pop.moves if pop.moves else 0.0
    return (
        f"# Labelling sheet: which news explains the move?\n\n"
        f"File: `{csv_name}`. Population: {band}, news fetched from every source: "
        f"{pop.moves} moves, of which {pop.with_items} ({share:.0%}) had something published "
        f"before the close. Sampled: {pop.sampled_up} rises and {pop.sampled_down} falls.\n\n"
        "For each move (`move_id`), put `y` in `explains` on **one** row:\n\n"
        "- the item that explains the move **in its direction** (`direction`, `move`, `z`), or\n"
        "- row 0, *nothing here explains the move*, when none does: an item that is about the\n"
        "  company but would not move the price, or would move it the other way, is not a\n"
        "  cause.\n\n"
        "Judge from the title and, where needed, the link. Do not look up the rules' cause in\n"
        "`moves.csv`: the sheet is meant to be blind. `note` is free text.\n\n"
        "Progress: `undercurrent news-coverage sheet --check`.\n"
    )
