"""Do reports move the price (reactions.py), and what the digest says about it."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from undercurrent import gpw_calendar, publish, reactions
from undercurrent.study import WARSAW


def _sessions(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if gpw_calendar.is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=WARSAW).astimezone(UTC)


def _reports(rows: list[tuple[str, str, str, datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "report_id": [r[0] for r in rows],
            "isin": [r[1] for r in rows],
            "signal_type": [r[2] for r in rows],
            "subtype": [None] * len(rows),
            "insider_side": [None] * len(rows),
            "known_at": [r[3] for r in rows],
        },
        schema_overrides={"known_at": pl.Datetime("us", "UTC"), "subtype": pl.String},
    )


def test_the_window_runs_from_day_0_to_the_first_close_a_reader_can_trade():
    cal = _sessions(date(2025, 3, 3), 30)  # Monday 3 March 2025 onwards
    mon, tue = cal[0], cal[1]
    sat = date(2025, 3, 8)
    reps = _reports(
        [
            ("during", "A", "periodic", _at(mon, 10)),  # tonight's digest, bought Tuesday
            ("after_close", "A", "periodic", _at(mon, 17, 30)),  # tonight's digest, day 0 Tue
            ("evening", "A", "periodic", _at(mon, 20)),  # Tuesday's digest, bought Wednesday
            ("weekend", "A", "periodic", _at(sat, 9)),  # Monday's digest, bought Tuesday
        ]
    )
    w = reactions.windows(reps, cal)
    days = {r: g["trade_date"].to_list() for (r,), g in w.group_by("report_id")}
    assert days["during"] == [mon, tue]
    assert days["after_close"] == [tue]
    assert days["evening"] == [tue, cal[2]]
    assert days["weekend"] == [date(2025, 3, 10), date(2025, 3, 11)]


def test_a_day_0_the_lake_lacks_is_not_measured():
    cal = _sessions(date(2025, 3, 3), 30)
    holed = [d for d in cal if d != cal[3]]
    reps = _reports([("x", "A", "periodic", _at(cal[3], 10))])
    assert reactions.windows(reps, holed).height == 0


def _market(seed: int = 3):
    """60 stocks × 400 sessions of calm surprises; 'loud' reports come with a ±3σ day."""
    rng = np.random.default_rng(seed)
    cal = _sessions(date(2024, 1, 2), 400)
    isins = [f"PL{i:04d}" for i in range(60)]
    z = rng.standard_normal((len(isins), len(cal)))
    reps = []
    for k in range(600):
        i, t = int(rng.integers(len(isins))), int(rng.integers(5, len(cal) - 5))
        loud = k % 2 == 0
        if loud:
            z[i, t] = 3.0 * rng.choice([-1.0, 1.0])
        # published after the 18:30 cut-off: day 0 is the next session, entry the one after
        reps.append((f"r{k}", isins[i], "periodic" if loud else "other", _at(cal[t - 1], 19)))
    state = pl.DataFrame(
        {
            "isin": np.repeat(isins, len(cal)),
            "trade_date": cal * len(isins),
            "surprise": z.ravel(),
            "resid": z.ravel() * 0.02,
            "liquidity_tier": ["liquid"] * z.size,
            "known_at": [_at(d, 18) for d in cal] * len(isins),
        },
        schema_overrides={"known_at": pl.Datetime("us", "UTC")},
    )
    return cal, _reports(reps), state


def test_a_kind_that_comes_with_big_moves_is_told_from_one_that_does_not():
    cal, reps, state = _market()
    inp = reactions.cut(reps, cal, state, _at(cal[-1], 23))
    reacts, stats, base = reactions.compute(inp)
    assert set(base) == set(range(1, reactions.MAX_WINDOW + 1))
    for k in base:  # calm windows are about ±1 whatever their length
        assert base[k]["z2"] == pytest.approx(1.0, abs=0.1)
    by = {r["signal_key"]: r for r in stats.to_dicts()}
    assert by["periodic"]["verdict"] == "moves"
    assert by["periodic"]["ratio"] > 3  # (9 + 1) / 2 for a 3σ day plus a calm one
    # a 3σ day and a calm one: (3 + noise) / √2 is beyond 2 about half the time; calm 5 %
    assert by["periodic"]["big_share"] > 0.4
    assert by["other"]["big_share"] == pytest.approx(by["other"]["base_big_share"], abs=0.04)
    assert by["other"]["verdict"] == "no_clear_move"
    assert by["other"]["ratio"] == pytest.approx(1.0, abs=0.25)
    # the signed move averages out: what moves the price is its size, not its sign
    loud = reacts.filter(pl.col("signal_key") == "periodic")["z"].to_numpy()
    assert abs(loud.mean()) < 0.5


def test_only_what_was_known_counts():
    cal, reps, state = _market()
    # as of the evening of a session: later reports are cut; a window not yet closed
    # is kept for the digest but never enters the statistics
    as_of = _at(cal[200], 23)
    inp = reactions.cut(reps, cal, state, as_of)
    assert inp.reports["known_at"].max() <= as_of
    assert inp.sessions[-1] == cal[200]
    reacts, _, _ = reactions.compute(inp)
    open_ = reacts.filter(~pl.col("complete"))
    assert open_.height and (open_["day0"] >= cal[199]).all()


def test_too_few_reports_get_no_verdict():
    cal, reps, state = _market()
    few = reps.head(10)
    _, stats, _ = reactions.compute(reactions.cut(few, cal, state, _at(cal[-1], 23)))
    assert set(stats["verdict"]) == {"too_few"}
    assert stats["q_value"].null_count() == stats.height


# --------------------------------------------------------------------------- the digest


def _item(rid, isin, z, day0, ratio=2.0, ticker="ABC"):
    return {
        "report_id": rid,
        "ticker": ticker,
        "label": "Periodic",
        "fact": "Raport kwartalny",
        "slug": rid,
        "is_event": False,
        "reaction": {"ratio": ratio, "verdict": "moves"},
        "move": {"isin": isin, "z": z, "move": z * 0.027, "day0": day0},
    }


def test_big_moves_are_today_s_one_per_stock_biggest_first():
    day = date(2026, 11, 9)
    feed = [
        _item("a", "X", -3.1, day, ratio=2.9),
        _item("b", "X", -3.1, day, ratio=1.2),  # same stock, a quieter kind: not repeated
        _item("c", "Y", 4.0, day),
        _item("d", "Z", 1.5, day),  # within ±2σ
        _item("e", "W", 5.0, day - timedelta(days=3)),  # an earlier session
    ]
    feed.append({**_item("f", "V", 3.0, day), "move": None})  # day 0 not closed yet
    assert [i["report_id"] for i in publish.big_moves(feed, day)] == ["c", "a"]


def _ctx(moves, reactions_rows, worth=None):
    return publish.SiteContext(
        day=date(2026, 11, 9),
        generated_at=datetime(2026, 11, 9, tzinfo=UTC),
        run={},
        counts={"reports": 214, "moving_kinds": 37},
        worth=worth or [],
        feed=[],
        stats={},
        summary={},
        events=[],
        runs=[],
        coverage={},
        health="Run ok",
        site_url="https://example.test/",
        reactions=reactions_rows,
        moves=moves,
    )


def test_the_digest_says_what_moved_today():
    m = _item("gpw-9", "X", -3.1, date(2026, 11, 9), ticker="CDR")
    text, markup = publish.digest_message(_ctx([m], [{"signal_key": "periodic"}]))
    assert "214 reports · 37 of kinds that move prices · 1 big move today" in text
    assert "<b>CDR</b> · Periodic: Raport kwartalny" in text
    assert "Today: -8.4% vs market (-3.1σ)" in text
    assert "moved prices significantly" not in text
    assert len(markup["inline_keyboard"][0]) == 1  # a periodic report has no event page
    quiet, _ = publish.digest_message(_ctx([], [{"signal_key": "periodic"}]))
    assert "· 0 big moves today" in quiet
    assert "No report today came with a big move (beyond ±2σ) by the close." in quiet
    # without the reaction tables the digest says nothing about moves either way
    bare, _ = publish.digest_message(_ctx([], []))
    assert "big move" not in bare and "move prices" not in bare
