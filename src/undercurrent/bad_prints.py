"""Review helper for the two price gates: lists what they caught and drafts the override rows.

``assert_no_reversed_factors`` and ``assert_no_unexplained_jumps`` (dbt/tests) stop the
nightly build when a price looks wrong. Settling a case means a row in
``seeds/corporate_action_overrides.csv``, and that needs a look at the sessions around it.
This module runs the same rules against the built ``adjusted_prices`` table (dbt builds the
model before it tests it, so the table is there after a failed build), prints each case with
its neighbourhood and drafts the rows.

Only one kind of case is drafted as settled: a large factor undone within a month with no
trades on any session from the print to the restoring day. Nobody traded at the printed
price, so it is the exchange's file that was wrong, and the published factors are kept: they
cancel and the stock's return over the episode is 0 (the MEGARON rows of 2026-07-23 are the
model). Everything else is printed for a person to decide; ``--append`` never writes it.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

SEED = Path(__file__).resolve().parents[2] / "seeds" / "corporate_action_overrides.csv"
CONTEXT_SESSIONS = 3

# The thresholds of dbt/tests/assert_no_reversed_factors.sql and assert_no_unexplained_jumps.sql.
BIG_FACTOR = 1.35
CANCEL_LOG = 0.1
WITHIN_DAYS = 31
JUMP = 0.35

_REVERSED = f"""
with big as (
    select
        isin, ticker, trade_date, factor, close, prev_close, override_action,
        lag(factor) over w as prev_factor,
        lag(trade_date) over w as prev_factor_date,
        lag(close) over w as prev_factor_close,
        lag(prev_close) over w as prev_factor_prev_close,
        lag(override_action) over w as prev_override_action
    from adjusted_prices
    where factor_source is not null and abs(ln(factor)) > ln({BIG_FACTOR})
    window w as (partition by isin order by trade_date)
)
select
    isin, ticker, prev_factor_date as print_date, prev_factor as print_factor,
    prev_factor_prev_close as close_before, prev_factor_close as printed_close,
    trade_date as restore_date, factor as restore_factor, close as restored_close,
    coalesce(override_action, prev_override_action) as settled_as
from big
where prev_factor is not null
  and abs(ln(factor) + ln(prev_factor)) < {CANCEL_LOG}
  and date_diff('day', prev_factor_date, trade_date) <= {WITHIN_DAYS}
  {{settled}}
order by print_date, isin
"""

_JUMPS = f"""
select isin, ticker, trade_date, prev_close, close, adj_ret_since_prev, volume, factor_source,
       override_action as settled_as
from adjusted_prices
where abs(adj_ret_since_prev) > {JUMP}
  and (
      coalesce(volume, 0) = 0
      or (factor_source = 'exchange_reference' and adj_ret is not null)
  )
  {{settled}}
order by trade_date, isin
"""

_CONTEXT = """
with s as (
    select *, row_number() over (order by trade_date) as n
    from adjusted_prices where isin = $isin
)
select trade_date, close, prev_close, change_pct, exch_reference, factor, factor_source,
       volume, value_pln
from s
where n between (select min(n) from s where trade_date >= $start) - $k
            and (select max(n) from s where trade_date <= $end) + $k
order by trade_date
"""


@dataclass(frozen=True)
class OverrideRow:
    """One line of ``corporate_action_overrides.csv``."""

    isin: str
    ex_date: date
    action: str
    factor: float
    comment: str


@dataclass
class Case:
    """Something a gate caught, with what a reviewer needs to settle it."""

    gate: str  # "reversed_factor" | "unexplained_jump"
    isin: str
    ticker: str
    start: date
    end: date
    summary: str
    context: list[dict[str, Any]] = field(default_factory=list)
    rows: list[OverrideRow] = field(default_factory=list)
    clear: bool = False
    settled_as: str | None = None


def _records(rel: duckdb.DuckDBPyRelation) -> list[dict[str, Any]]:
    cols = rel.columns
    return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]


def _fmt(x: float | None) -> str:
    return "" if x is None else f"{x:g}"


def find_cases(con: duckdb.DuckDBPyConnection, *, include_settled: bool = False) -> list[Case]:
    """Every case the two gates would fail on (or did, with ``include_settled``)."""
    cases: list[Case] = []
    rev_filter = (
        "" if include_settled else "and override_action is null and prev_override_action is null"
    )
    for r in _records(con.sql(_REVERSED.replace("{settled}", rev_filter))):
        ctx = _context(con, r["isin"], r["print_date"], r["restore_date"])
        span = [c for c in ctx if r["print_date"] <= c["trade_date"] <= r["restore_date"]]
        no_trades = all(not (c["volume"] or 0) for c in span)
        sessions = sum(1 for c in span if c["trade_date"] < r["restore_date"])
        t = r["ticker"]
        rows_ = [
            OverrideRow(
                r["isin"],
                r["print_date"],
                "bad_print",
                r["print_factor"],
                f"{t}: close printed as {_fmt(r['printed_close'])} for {sessions} "
                f"session{'s' if sessions != 1 else ''} with no trades; the exchange restored "
                f"{_fmt(r['restored_close'])} on {r['restore_date']}. Factors kept as published "
                "so they cancel and the return is 0.",
            ),
            OverrideRow(
                r["isin"],
                r["restore_date"],
                "bad_print",
                r["restore_factor"],
                f"{t}: the exchange restoring {_fmt(r['restored_close'])} after the "
                f"{_fmt(r['printed_close'])} print of {r['print_date']} (no trades).",
            ),
        ]
        verdict = (
            "no trades on any session of the episode: a bad print"
            if no_trades
            else "shares traded during the episode: check before settling"
        )
        cases.append(
            Case(
                gate="reversed_factor",
                isin=r["isin"],
                ticker=t,
                start=r["print_date"],
                end=r["restore_date"],
                summary=(
                    f"{_fmt(r['close_before'])} → {_fmt(r['printed_close'])} on {r['print_date']}, "
                    f"back to {_fmt(r['restored_close'])} on {r['restore_date']}; {verdict}"
                ),
                context=ctx,
                rows=rows_,
                clear=no_trades,
                settled_as=r["settled_as"],
            )
        )
    # A jump inside an episode already listed is the same case.
    covered = {
        (c.isin, d["trade_date"])
        for c in cases
        for d in c.context
        if c.start <= d["trade_date"] <= c.end
    }
    jump_filter = "" if include_settled else "and override_action is null"
    for r in _records(con.sql(_JUMPS.replace("{settled}", jump_filter))):
        if (r["isin"], r["trade_date"]) in covered:
            continue
        why = (
            "no trades that session"
            if not (r["volume"] or 0)
            else "on an ex-date, yet the return is used"
        )
        cases.append(
            Case(
                gate="unexplained_jump",
                isin=r["isin"],
                ticker=r["ticker"],
                start=r["trade_date"],
                end=r["trade_date"],
                summary=(
                    f"{_fmt(r['prev_close'])} → {_fmt(r['close'])} on {r['trade_date']} "
                    f"({r['adj_ret_since_prev']:+.0%} adjusted), {why}: needs a decision"
                ),
                context=_context(con, r["isin"], r["trade_date"], r["trade_date"]),
                settled_as=r["settled_as"],
            )
        )
    return cases


def _context(
    con: duckdb.DuckDBPyConnection, isin: str, start: date, end: date
) -> list[dict[str, Any]]:
    rel = con.execute(_CONTEXT, {"isin": isin, "start": start, "end": end, "k": CONTEXT_SESSIONS})
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]


def seed_rows(rows: list[OverrideRow]) -> str:
    """The rows as CSV lines in the seed's format (comment always quoted)."""
    out = io.StringIO()
    for r in rows:
        comment = r.comment.replace('"', '""')
        out.write(f'{r.isin},{r.ex_date},{r.action},{r.factor!r},"{comment}"\n')
    return out.getvalue()


def report(cases: list[Case]) -> str:
    """Markdown: one section per case, its sessions and the drafted rows."""
    if not cases:
        return "No price cases for the gates to fail on.\n"
    clear = sum(c.clear for c in cases)
    lines = [
        f"# Price cases: {len(cases)} ({clear} clear bad print{'s' if clear != 1 else ''})",
        "",
    ]
    for c in cases:
        head = f"## {c.ticker} ({c.isin}) · {c.gate.replace('_', ' ')}"
        if c.settled_as:
            head += f" · settled as {c.settled_as}"
        lines += [head, "", c.summary, ""]
        lines += [
            "| date | close | prev close | change % | reference | factor | volume | value PLN |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for d in c.context:
            mark = "**" if c.start <= d["trade_date"] <= c.end else ""
            lines.append(
                f"| {mark}{d['trade_date']}{mark} | {_fmt(d['close'])} | {_fmt(d['prev_close'])} "
                f"| {_fmt(d['change_pct'])} | {_fmt(d['exch_reference'])} | {_fmt(d['factor'])} "
                f"| {_fmt(d['volume'])} | {_fmt(d['value_pln'])} |"
            )
        lines.append("")
        if c.rows and not c.settled_as:
            label = "Drafted rows" if c.clear else "Candidate rows (not appended: check first)"
            lines += [f"{label}:", "", "```", seed_rows(c.rows).rstrip(), "```", ""]
    return "\n".join(lines) + "\n"


def append_clear(cases: list[Case], seed: Path = SEED) -> list[OverrideRow]:
    """Append the drafted rows of clear bad prints to the seed, skipping dates already there."""
    with seed.open(encoding="utf-8", newline="") as fh:
        have = {(r["isin"], r["ex_date"]) for r in csv.DictReader(fh)}
    new = [
        row
        for c in cases
        if c.clear and not c.settled_as
        for row in c.rows
        if (row.isin, str(row.ex_date)) not in have
    ]
    if new:
        text = seed.read_text(encoding="utf-8")
        with seed.open("a", encoding="utf-8", newline="") as fh:
            if text and not text.endswith("\n"):
                fh.write("\n")
            fh.write(seed_rows(new))
    return new


def review(
    duckdb_file: Path, *, include_settled: bool = False, append: bool = False, seed: Path = SEED
) -> tuple[list[Case], list[OverrideRow]]:
    """Cases in the built warehouse; with ``append``, the clear rows are written to the seed."""
    con = duckdb.connect(str(duckdb_file))
    try:
        cases = find_cases(con, include_settled=include_settled)
    finally:
        con.close()
    added = append_clear(cases, seed) if append else []
    return cases, added
