"""The price model and its data gates, run on hand-made rows.

The SQL is the real dbt model and tests, rendered with ``ref()`` pointing at plain tables.
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import jinja2
import pytest

DBT = Path(__file__).resolve().parents[2] / "dbt"
GATES = (
    "assert_no_unexplained_jumps",
    "assert_no_reversed_factors",
    "assert_nc_reference_matches_change",
    "assert_adjusted_returns_reconcile",
)


def _render(path: Path) -> str:
    return jinja2.Template(path.read_text("utf-8")).render(ref=lambda name: name)


def _days(n: int, start: date = date(2016, 1, 4)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _row(
    isin: str,
    day: date,
    close: float,
    change_pct: float,
    volume: float = 1000.0,
    market: str = "GPW",
    reference: float | None = None,
) -> tuple[Any, ...]:
    return (isin, day, market, isin, isin, close, change_pct, reference, volume, close * volume)


def _build(
    rows: list[tuple[Any, ...]],
    sessions: list[tuple[date, int]],
    overrides: list[tuple[str, str, str, float, str]] | None = None,
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "create table prices_daily (isin varchar, trade_date date, market varchar, "
        "ticker varchar, short_name varchar, close double, change_pct double, "
        "reference double, volume double, value_pln double, known_at timestamptz)"
    )
    con.executemany("insert into prices_daily values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())", rows)
    con.execute("create table sessions (trade_date date, session_idx bigint)")
    con.executemany("insert into sessions values (?, ?)", sessions)
    con.execute(
        "create table corporate_action_overrides (isin varchar, ex_date varchar, "
        "action varchar, factor double, comment varchar)"
    )
    if overrides:
        con.executemany("insert into corporate_action_overrides values (?, ?, ?, ?, ?)", overrides)
    model = _render(DBT / "models" / "gold" / "adjusted_prices.sql")
    con.execute(f"create table adjusted_prices as {model}")
    return con


def _failures(con: duckdb.DuckDBPyConnection, gate: str) -> int:
    return len(con.execute(_render(DBT / "tests" / f"{gate}.sql")).fetchall())


def _one(con: duckdb.DuckDBPyConnection, day: date, isin: str = "PLA") -> dict[str, Any]:
    cur = con.execute(
        "select * from adjusted_prices where isin = ? and trade_date = ?", [isin, day]
    )
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, cur.fetchone(), strict=True))


def test_clean_series_with_a_split_passes_every_gate() -> None:
    d = _days(4)
    rows = [
        _row("PLA", d[0], 10.0, 0.0),
        _row("PLA", d[1], 11.0, 10.0),
        _row("PLA", d[2], 1.21, 10.0),  # 1:10 split and +10 % on the day
        _row("PLA", d[3], 1.21, 0.0),
    ]
    con = _build(rows, [(x, i + 1) for i, x in enumerate(d)])
    split = _one(con, d[2])
    assert split["factor"] == pytest.approx(0.1, rel=1e-6)
    assert split["adj_ret"] == pytest.approx(0.10, abs=1e-9) and not split["quarantined"]
    assert _one(con, d[0])["adj_close"] == pytest.approx(1.0, rel=1e-6)
    assert all(_failures(con, g) == 0 for g in GATES)


def test_a_real_traded_jump_passes_but_one_without_a_trade_fails() -> None:
    d = _days(3)
    sessions = [(x, i + 1) for i, x in enumerate(d)]
    traded = [_row("PLA", d[0], 10.0, 0.0), _row("PLA", d[1], 15.0, 50.0)]
    assert _failures(_build(traded, sessions), "assert_no_unexplained_jumps") == 0
    silent = [_row("PLA", d[0], 10.0, 0.0), _row("PLA", d[1], 15.0, 50.0, volume=0.0)]
    assert _failures(_build(silent, sessions), "assert_no_unexplained_jumps") == 1


def test_big_move_on_an_ex_date_is_quarantined_until_overridden() -> None:
    d = _days(3)
    sessions = [(x, i + 1) for i, x in enumerate(d)]
    # reference 2.40 against a previous close of 0.15, and the close 67 % above it
    rows = [_row("PLA", d[0], 0.15, 0.0), _row("PLA", d[1], 4.0, 66.67)]
    con = _build(rows, sessions)
    day = _one(con, d[1])
    assert day["quarantined"] and day["adj_ret"] is None
    assert day["adj_ret_since_prev"] == pytest.approx(0.6667, abs=1e-3)
    assert _failures(con, "assert_no_unexplained_jumps") == 0

    con = _build(rows, sessions, [("PLA", d[1].isoformat(), "reverse_split", 16.0, "checked")])
    day = _one(con, d[1])
    assert not day["quarantined"] and day["adj_ret"] == pytest.approx(0.6667, abs=1e-3)


def test_nothing_is_derived_across_a_hole_in_the_lake() -> None:
    d = _days(2)
    later = date(2026, 10, 19)
    rows = [
        _row("PLA", d[0], 296.0, 0.0),
        _row("PLA", d[1], 296.0, 0.0),
        _row("PLA", later, 9.5, 0.53),  # ten years on, after splits nobody recorded
    ]
    con = _build(rows, [(d[0], 1), (d[1], 2), (later, 1003)])
    far = _one(con, later)
    assert far["factor"] == 1.0 and far["factor_source"] is None
    assert far["adj_ret"] is None and far["adj_ret_since_prev"] is None
    assert _one(con, d[0])["adj_close"] == pytest.approx(296.0)  # old levels are left alone
    assert all(_failures(con, g) == 0 for g in GATES)


def test_a_factor_undone_within_a_month_is_a_bad_print() -> None:
    d = _days(4)
    sessions = [(x, i + 1) for i, x in enumerate(d)]
    rows = [
        _row("PLA", d[0], 10.0, 0.0),
        _row("PLA", d[1], 100.0, 0.0),  # decimal point slipped: looks like a 10:1 merger
        _row("PLA", d[2], 10.0, 0.0),  # and back: looks like a 1:10 split
        _row("PLA", d[3], 10.0, 0.0),
    ]
    assert _failures(_build(rows, sessions), "assert_no_reversed_factors") == 1
    fixed = [("PLA", d[1].isoformat(), "bad_print", 10.0, "close garbled in the source file")]
    assert _failures(_build(rows, sessions, fixed), "assert_no_reversed_factors") == 0


def test_newconnect_reference_must_agree_with_the_change() -> None:
    d = _days(2)
    sessions = [(x, i + 1) for i, x in enumerate(d)]
    good = [
        _row("PLN", d[0], 2.0, 0.0, market="NC", reference=2.0),
        _row("PLN", d[1], 2.2, 10.0, market="NC", reference=2.0),
    ]
    assert _failures(_build(good, sessions), "assert_nc_reference_matches_change") == 0
    bad = [*good[:1], _row("PLN", d[1], 2.2, 10.0, market="NC", reference=0.2)]
    assert _failures(_build(bad, sessions), "assert_nc_reference_matches_change") == 1
