"""The review helper for the price gates, run on the real dbt model over hand-made rows."""

import csv
from datetime import date
from pathlib import Path

import duckdb
import pytest

from tests.unit.test_price_gates import DBT, GATES, _build, _days, _failures, _row
from undercurrent import bad_prints, pipeline


def _silent_print(volume: float = 0.0) -> tuple[list, list[tuple[date, int]], list[date]]:
    """MEGARON's shape: 7.00, one session at 0.284, 7.00 again, the change printed as 0."""
    d = _days(7)
    closes = [7.0, 7.0, 7.0, 0.284, 7.0, 7.0, 7.0]
    rows = [
        _row("PLX", day, c, 0.0, volume=volume if i in (3, 4) else 0.0)
        for i, (day, c) in enumerate(zip(d, closes, strict=True))
    ]
    return rows, [(x, i + 1) for i, x in enumerate(d)], d


def _as_overrides(rows: list[bad_prints.OverrideRow]) -> list[tuple[str, str, str, float, str]]:
    return [(r.isin, r.ex_date.isoformat(), r.action, r.factor, r.comment) for r in rows]


def test_a_silent_bad_print_is_drafted_and_the_draft_settles_every_gate() -> None:
    rows, sessions, d = _silent_print()
    con = _build(rows, sessions)
    assert _failures(con, "assert_no_reversed_factors") == 1

    [case] = bad_prints.find_cases(con)
    assert case.gate == "reversed_factor" and case.clear
    assert (case.start, case.end) == (d[3], d[4])
    assert [r.ex_date for r in case.rows] == [d[3], d[4]]
    assert all(r.action == "bad_print" for r in case.rows)
    assert "1 session with no trades" in case.rows[0].comment
    # the sessions around the episode are shown: three either side
    assert [c["trade_date"] for c in case.context] == d

    fixed = _build(rows, sessions, _as_overrides(case.rows))
    assert all(_failures(fixed, g) == 0 for g in GATES)
    rets = fixed.execute(
        "select adj_ret from adjusted_prices where adj_ret is not null order by trade_date"
    ).fetchall()
    assert all(r[0] == pytest.approx(0.0, abs=1e-12) for r in rets)
    assert bad_prints.find_cases(fixed) == []
    [settled] = bad_prints.find_cases(fixed, include_settled=True)
    assert settled.settled_as == "bad_print"


def test_a_reversal_with_trades_is_listed_but_never_appended(tmp_path: Path) -> None:
    rows, sessions, _ = _silent_print(volume=500.0)
    [case] = bad_prints.find_cases(_build(rows, sessions))
    assert case.gate == "reversed_factor" and not case.clear
    assert "check before settling" in case.summary
    seed = tmp_path / "overrides.csv"
    seed.write_text("isin,ex_date,action,factor,comment\n", encoding="utf-8")
    assert bad_prints.append_clear([case], seed) == []
    assert "Candidate rows (not appended" in bad_prints.report([case])


def test_a_silent_jump_that_stays_is_left_for_a_person() -> None:
    d = _days(5)
    rows = [_row("PLA", x, 10.0, 0.0) for x in d[:2]]
    rows += [_row("PLA", x, 15.0, 50.0, volume=0.0) for x in d[2:3]]
    rows += [_row("PLA", x, 15.0, 0.0) for x in d[3:]]
    con = _build(rows, [(x, i + 1) for i, x in enumerate(d)])
    assert _failures(con, "assert_no_unexplained_jumps") == 1
    [case] = bad_prints.find_cases(con)
    assert case.gate == "unexplained_jump" and not case.clear and case.rows == []
    assert "needs a decision" in case.summary


def test_appending_is_idempotent_and_keeps_the_seed_format(tmp_path: Path) -> None:
    rows, sessions, d = _silent_print()
    cases = bad_prints.find_cases(_build(rows, sessions))
    seed = tmp_path / "overrides.csv"
    seed.write_text(
        'isin,ex_date,action,factor,comment\nPLA,2020-01-02,split,0.1,"kept, as is"',
        encoding="utf-8",
    )
    added = bad_prints.append_clear(cases, seed)
    assert [r.ex_date for r in added] == [d[3], d[4]]
    assert bad_prints.append_clear(cases, seed) == []
    with seed.open(encoding="utf-8", newline="") as fh:
        got = list(csv.DictReader(fh))
    assert [g["isin"] for g in got] == ["PLA", "PLX", "PLX"]
    assert got[0]["comment"] == "kept, as is"
    assert float(got[1]["factor"]) == added[0].factor  # the published factor, exactly


def test_the_helper_uses_the_gates_own_thresholds() -> None:
    rev = (DBT / "tests" / "assert_no_reversed_factors.sql").read_text("utf-8")
    jump = (DBT / "tests" / "assert_no_unexplained_jumps.sql").read_text("utf-8")
    assert f"ln({bad_prints.BIG_FACTOR})" in rev
    assert f"< {bad_prints.CANCEL_LOG}" in rev
    assert f"<= {bad_prints.WITHIN_DAYS}" in rev
    assert f"> {bad_prints.JUMP}" in jump


def test_a_failed_build_puts_the_cases_in_the_job_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, sessions, _ = _silent_print()
    con = _build(rows, sessions)
    work = tmp_path / "work"
    work.mkdir()
    con.execute(f"attach '{work / 'undercurrent.duckdb'}' as w")
    con.execute("create table w.adjusted_prices as select * from adjusted_prices")
    con.close()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    pipeline._report_price_cases(work)
    text = summary.read_text("utf-8")
    assert "PLX" in text and "Drafted rows" in text

    # a build that failed before the table existed has nothing to show, and does not raise
    empty = tmp_path / "empty"
    empty.mkdir()
    duckdb.connect(str(empty / "undercurrent.duckdb")).close()
    pipeline._report_price_cases(empty)
