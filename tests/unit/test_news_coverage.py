"""The news coverage gate: sessions, cause order, sector rule, sample and precision.

Fictional companies (Alfa Testowa, Beta Testowa); dates are real GPW sessions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from undercurrent import news
from undercurrent import news_common as common
from undercurrent import news_coverage as nc
from undercurrent.news import NewsItem

ALFA = "PLTEST000001"
BETA = "PLTEST000002"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
# Tuesday 17 Nov 2026 is a session; Monday 16 Nov is its previous session.
TUE = date(2026, 11, 17)
MON = date(2026, 11, 16)


def _warsaw(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=news.WARSAW).astimezone(UTC)


# ---- sessions -----------------------------------------------------------------------------


def test_news_window_runs_from_previous_close_to_this_close() -> None:
    start, end = nc.news_window(MON)
    assert start == _warsaw(2026, 11, 13, 17, 5)  # Friday's close
    assert end == _warsaw(2026, 11, 16, 17, 5)


# ---- sector -----------------------------------------------------------------------------


def test_sector_explains_needs_size_direction_and_share() -> None:
    assert nc.sector_explains(surprise=-3.2, resid=-0.06, excess=-0.04, z=-2.5)
    assert not nc.sector_explains(surprise=-3.2, resid=-0.06, excess=-0.02, z=-2.5)  # share 1/3
    assert not nc.sector_explains(surprise=-3.2, resid=-0.06, excess=-0.04, z=-1.5)  # too small
    assert not nc.sector_explains(surprise=3.2, resid=0.06, excess=-0.04, z=-2.5)  # other way
    assert not nc.sector_explains(surprise=3.2, resid=None, excess=0.04, z=2.5)


def test_sector_sigma_uses_only_earlier_sessions() -> None:
    days = pl.date_range(date(2026, 1, 1), date(2026, 4, 30), eager=True).to_list()
    rows = []
    for k, d in enumerate(days):
        wig = 100.0 * (1.001**k)
        sec = wig * (1 + (0.002 if k % 2 else -0.002))
        if d == days[-1]:
            sec = wig * 1.05  # one big excess day at the end
        rows += [("WIG", d, wig), ("WIG-BANKI", d, sec)]
    idx = pl.DataFrame(
        rows, schema={"name": pl.String, "trade_date": pl.Date, "close": pl.Float64}, orient="row"
    )
    out = nc.sector_moves(idx, ["WIG-BANKI"])
    excess, z = out[("WIG-BANKI", days[-1])]
    assert excess > 0.04
    assert z > 5  # scaled by the calm earlier σ, not by a σ that includes the jump itself


def test_sector_of_maps_code_through_seed() -> None:
    ind = pl.DataFrame(
        {
            "isin": [ALFA, ALFA, BETA],
            "trade_date": [date(2026, 11, 1), date(2026, 11, 20), date(2026, 11, 20)],
            "sector_code": [131, 111, 999],
        }
    )
    seed = pl.DataFrame({"sector_code": [111], "index_name": ["WIG-BANKI"]})
    assert nc.sector_of(ind, seed) == {ALFA: "WIG-BANKI"}  # latest code; unmapped code left out


# ---- attribution ------------------------------------------------------------------------


def _store(tmp_path: Path) -> nc.Store:
    s = nc.Store(tmp_path)
    for src in common.PAGE_SOURCES:
        for isin in (ALFA, BETA):
            s.mark(nc._mark(src, isin, None, NOW, NOW, "ok", "", 0, NOW))
    return s


def _moves(*rows: tuple[str, date, float, float, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "isin": i,
                "trade_date": d,
                "market": "GPW",
                "liquidity_tier": t,
                "surprise": z,
                "resid": r,
            }
            for i, d, z, r, t in rows
        ]
    )


def _inputs(moves: pl.DataFrame, **kw: object) -> nc.Inputs:
    companies = {
        ALFA: common.CompanyInfo(ALFA, "ALFA", "Alfa Testowa"),
        BETA: common.CompanyInfo(BETA, "BETA", "Beta Testowa"),
    }
    return nc.Inputs(
        moves=moves,
        companies=companies,
        reports=kw.get("reports", {}),  # type: ignore[arg-type]
        sector_index=kw.get("sector_index", {}),  # type: ignore[arg-type]
        sectors=kw.get("sectors", {}),  # type: ignore[arg-type]
        peers=kw.get("peers", {}),  # type: ignore[arg-type]
    )


def _names(inp: nc.Inputs) -> common.NameIndex:
    return common.NameIndex({i: c.company() for i, c in inp.companies.items()})


def test_report_outranks_news(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_items(
        news.BANKIER,
        ALFA,
        [
            NewsItem(
                ALFA,
                news.BANKIER,
                "Bankier",
                _warsaw(2026, 11, 17, 12, 0),
                "minute",
                "Regulator zapowiada kontrolę",
                "https://www.bankier.pl/wiadomosc/x-1.html",
                NOW,
            )
        ],
    )
    reports = {(ALFA, TUE): [{"title": "Raport bieżący 21/2026", "url": "u", "at": NOW}]}
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")), reports=reports)
    row = nc.attribute(inp, store, _names(inp)).row(0, named=True)
    assert (row["cause"], row["source"]) == ("report", "report")


def test_every_source_must_name_the_company(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_items(
        news.GOOGLE,
        BETA,
        [
            NewsItem(
                BETA,
                news.GOOGLE,
                "PB",
                _warsaw(2026, 11, 17, 12, 0),
                "minute",
                "Regulator zapowiada kontrolę",
                "https://news.google.com/a",
                NOW,
                outlet_url="https://pb.pl",
            )
        ],
    )
    store.add_items(
        news.BANKIER,
        ALFA,
        [
            NewsItem(
                ALFA,
                news.BANKIER,
                "Bankier",
                _warsaw(2026, 11, 17, 12, 0),
                "minute",
                "Regulator zapowiada kontrolę",
                "https://www.bankier.pl/wiadomosc/x-2.html",
                NOW,
            )
        ],
    )
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid"), (BETA, TUE, -4.0, -0.08, "liquid")))
    out = {r["isin"]: r for r in nc.attribute(inp, store, _names(inp)).iter_rows(named=True)}
    # neither headline names its company: a per-company page is no exception
    assert out[ALFA]["cause"] == "none"
    assert out[BETA]["cause"] == "none"


def test_news_after_close_explains_next_session_not_this_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_items(
        news.BANKIER,
        ALFA,
        [
            NewsItem(
                ALFA,
                news.BANKIER,
                "Bankier",
                _warsaw(2026, 11, 17, 18, 0),
                "minute",
                "Alfa Testowa traci kontrakt",
                "https://www.bankier.pl/wiadomosc/x-3.html",
                NOW,
            )
        ],
    )
    wed = date(2026, 11, 18)
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid"), (ALFA, wed, -3.5, -0.07, "liquid")))
    out = {
        r["trade_date"]: r["cause"]
        for r in nc.attribute(inp, store, _names(inp)).iter_rows(named=True)
    }
    assert out == {TUE: "none", wed: "news"}


def test_date_only_headline_is_a_weak_cause(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_items(
        news.GOOGLE,
        ALFA,
        [
            NewsItem(
                ALFA,
                news.GOOGLE,
                "Parkiet",
                _warsaw(2026, 11, 17, 9, 0),
                "day",
                "Alfa Testowa traci kontrakt",
                "https://news.google.com/b",
                NOW,
                outlet_url="https://parkiet.com",
            )
        ],
    )
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    row = nc.attribute(inp, store, _names(inp)).row(0, named=True)
    assert (row["cause"], row["strength"], row["kind"]) == ("news", "weak", "news_day")


def test_sector_is_the_last_cause(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inp = _inputs(
        _moves((ALFA, TUE, -4.0, -0.08, "liquid")),
        sector_index={ALFA: "WIG-BANKI"},
        sectors={("WIG-BANKI", TUE): (-0.05, -3.0)},
        peers={
            ("WIG-BANKI", TUE): [
                (ALFA, -4.0),
                ("P1", -1.5),
                ("P2", -1.2),
                ("P3", -2.0),
                ("P4", 0.1),
            ]
        },
    )
    row = nc.attribute(inp, store, _names(inp)).row(0, named=True)
    assert (row["cause"], row["strength"]) == ("sector", "weak")


def test_unfetched_company_is_pending_not_none(tmp_path: Path) -> None:
    store = nc.Store(tmp_path)  # nothing fetched
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    assert nc.attribute(inp, store, _names(inp))["cause"].to_list() == ["pending"]


def test_move_awaiting_google_is_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid"), (ALFA, MON, -2.5, -0.05, "liquid")))
    out = {
        r["trade_date"]: r["cause"]
        for r in nc.attribute(inp, store, _names(inp), google_min_z=3.0).iter_rows(named=True)
    }
    assert out == {TUE: "pending", MON: "none"}  # the small move is never sent to Google
    store.mark(nc._mark(news.GOOGLE, ALFA, TUE, NOW, NOW, "empty", "", 0, NOW))
    out = {
        r["trade_date"]: r["cause"]
        for r in nc.attribute(inp, store, _names(inp), google_min_z=3.0).iter_rows(named=True)
    }
    assert out[TUE] == "none"


# ---- report, sample and score -----------------------------------------------------------


def test_coverage_leaves_pending_out_of_the_share() -> None:
    attr = pl.DataFrame(
        {
            "liquidity_tier": ["liquid"] * 5,
            "surprise": [4.0] * 5,
            "band": ["z3"] * 5,
            "cause": ["report", "news", "none", "pending", "sector"],
            "strength": ["strong", "weak", None, None, "weak"],
        }
    )
    t = nc.coverage_table(attr, ["band"]).row(0, named=True)
    assert t["measured"] == 4 and t["pending"] == 1
    assert t["coverage"] == pytest.approx(3 / 4)
    assert t["coverage_strong"] == pytest.approx(1 / 4)


def test_sample_is_stratified_and_carries_shares() -> None:
    kinds = ["report"] * 60 + ["news_minute"] * 30 + ["news_day"] * 10
    attr = pl.DataFrame(
        {
            "isin": [f"PL{k:010d}" for k in range(100)],
            "short_name": "X",
            "trade_date": TUE,
            "liquidity_tier": "liquid",
            "surprise": -4.0,
            "cause": ["report"] * 60 + ["news"] * 40,
            "kind": kinds,
            "source": "s",
            "outlet": "o",
            "at": NOW,
            "title": "t",
            "url": "u",
        }
    )
    s = nc.sample_for_check(attr, per_class=20, seed=1)
    counts = dict(s.group_by("kind").len().iter_rows())
    assert counts == {"report": 20, "news_minute": 20, "news_day": 10}
    shares = dict(s.select("kind", "class_share").unique().iter_rows())
    assert shares == {"report": 0.6, "news_minute": 0.3, "news_day": 0.1}
    assert s["correct"].null_count() == s.height


def test_score_weights_kinds_by_their_share() -> None:
    sample = pl.DataFrame(
        {
            "kind": ["report"] * 10 + ["news_day"] * 10,
            "class_share": [0.9] * 10 + [0.1] * 10,
            "class_n": [900] * 10 + [100] * 10,
            "correct": ["y"] * 10 + ["y"] * 5 + ["n"] * 5,
        }
    )
    p = nc.score(sample)
    assert p.value == pytest.approx(0.9 * 1.0 + 0.1 * 0.5)
    assert p.by_kind["news_day"] == (5, 10, 0.1)
    assert p.low < p.value < p.high


def test_store_round_trip_and_bronze(tmp_path: Path) -> None:
    s = nc.Store(tmp_path)
    it = NewsItem(
        ALFA,
        news.BANKIER,
        "Bankier",
        _warsaw(2026, 11, 17, 12, 0),
        "minute",
        "T",
        "https://b/1",
        NOW,
    )
    s.add_items(news.BANKIER, ALFA, [it, it])
    assert s.items(ALFA) == [it]
    s.set_symbol(
        {
            "isin": ALFA,
            "source": news.BANKIER,
            "symbol": "ALFA",
            "page_name": "Alfa Testowa SA",
            "outcome": "ok",
            "checked_at": NOW,
        }
    )
    again = nc.Store(tmp_path)  # state survives a restart
    assert again.symbol(ALFA, news.BANKIER)["symbol"] == "ALFA"  # type: ignore[index]
    assert again.publish_bronze() == 1
    assert again.publish_bronze() == 0  # keyed: nothing written twice


def test_headline_naming_two_other_companies_is_excluded(tmp_path: Path) -> None:
    gamma = "PLTEST000003"
    store = _store(tmp_path)
    store.mark(nc._mark(news.BANKIER, gamma, None, NOW, NOW, "ok", "", 0, NOW))
    store.mark(nc._mark(news.BIZNESRADAR, gamma, None, NOW, NOW, "ok", "", 0, NOW))
    store.add_items(
        news.BANKIER,
        ALFA,
        [
            NewsItem(
                ALFA,
                news.BANKIER,
                "Bankier",
                _warsaw(2026, 11, 17, 12, 0),
                "minute",
                "Co zmieniły Alfa Testowa, Beta Testowa i Gamma Testowa",
                "https://www.bankier.pl/wiadomosc/x-9.html",
                NOW,
            )
        ],
    )
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    inp.companies[gamma] = common.CompanyInfo(gamma, "GAMMA", "Gamma Testowa")
    assert nc.attribute(inp, store, _names(inp))["cause"].to_list() == ["none"]


# ---- worker pool ------------------------------------------------------------------------


class _Client:
    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_pool_runs_every_job_once_on_many_workers() -> None:
    import threading

    seen: list[int] = []
    lock = threading.Lock()

    def work(_c: object, job: int, _th: news.HostThrottle) -> news.Outcome:
        with lock:
            seen.append(job)
        return news.Outcome.OK

    th = nc.run_pool(
        "t", list(range(100)), work, workers=8, make_client=_Client, stop=threading.Event()
    )  # type: ignore[arg-type]
    assert sorted(seen) == list(range(100))
    assert th.limit == 8


def test_pool_halves_workers_on_rate_limit_and_loses_no_job() -> None:
    import threading

    seen: list[int] = []
    lock = threading.Lock()

    def work(_c: object, job: int, th: news.HostThrottle) -> news.Outcome:
        if job == 3:
            th.halve()  # what fetch_page does on a 429
        with lock:
            seen.append(job)
        return news.Outcome.OK

    th = nc.run_pool(
        "t", list(range(60)), work, workers=8, make_client=_Client, stop=threading.Event()
    )  # type: ignore[arg-type]
    assert th.limit == 4
    assert sorted(seen) == list(range(60))


def test_pool_stops_the_host_at_a_block_page() -> None:
    import threading

    seen: list[int] = []

    def work(_c: object, job: int, _th: news.HostThrottle) -> news.Outcome:
        seen.append(job)
        return news.Outcome.BLOCKED if job == 0 else news.Outcome.OK

    nc.run_pool("t", list(range(50)), work, workers=1, make_client=_Client, stop=threading.Event())  # type: ignore[arg-type]
    assert seen == [0]


def test_pool_stops_a_host_that_keeps_timing_out() -> None:
    import threading

    seen: list[int] = []

    def work(_c: object, job: int, _th: news.HostThrottle) -> news.Outcome:
        seen.append(job)
        return news.Outcome.UNREACHABLE

    nc.run_pool("t", list(range(50)), work, workers=1, make_client=_Client, stop=threading.Event())  # type: ignore[arg-type]
    assert seen == list(range(nc.MAX_UNREACHABLE_IN_A_ROW))


def test_one_success_resets_the_timeout_count() -> None:
    import threading

    seen: list[int] = []

    def work(_c: object, job: int, _th: news.HostThrottle) -> news.Outcome:
        seen.append(job)
        return news.Outcome.OK if job % 2 else news.Outcome.UNREACHABLE

    nc.run_pool("t", list(range(20)), work, workers=1, make_client=_Client, stop=threading.Event())  # type: ignore[arg-type]
    assert seen == list(range(20))


@pytest.mark.parametrize("outcome", ["unreachable", "blocked", "rate_limited"])
def test_failed_page_fetch_is_retried_not_done(tmp_path: Path, outcome: str) -> None:
    store = nc.Store(tmp_path)
    store.mark(nc._mark(news.BANKIER, ALFA, None, NOW, NOW, outcome, "", 0, NOW))
    store.mark(nc._mark(news.BIZNESRADAR, ALFA, None, NOW, NOW, "ok", "", 0, NOW))
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    assert nc.attribute(inp, store, _names(inp))["cause"].to_list() == ["pending"]


# ---- unavailable sources ----------------------------------------------------------------


def _down(store: nc.Store, source: str, reason: str = "3 jobs in a row could not connect") -> None:
    store.set_source(source, nc.PoolResult(8, True, reason), NOW)


def test_unavailable_source_does_not_hold_moves_back(tmp_path: Path) -> None:
    store = nc.Store(tmp_path)
    store.mark(nc._mark(news.BANKIER, ALFA, None, NOW, NOW, "ok", "", 0, NOW))
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    assert nc.attribute(inp, store, _names(inp))["cause"].to_list() == ["pending"]
    _down(store, news.BIZNESRADAR)  # BiznesRadar stopped answering on the last run
    row = nc.attribute(inp, store, _names(inp)).row(0, named=True)
    assert row["cause"] == "none"
    assert row["missing_sources"] == news.BIZNESRADAR


def test_partial_moves_are_counted_in_the_coverage_table(tmp_path: Path) -> None:
    store = nc.Store(tmp_path)
    store.mark(nc._mark(news.BANKIER, ALFA, None, NOW, NOW, "ok", "", 0, NOW))
    store.mark(nc._mark(news.BANKIER, BETA, None, NOW, NOW, "ok", "", 0, NOW))
    store.mark(nc._mark(news.BIZNESRADAR, BETA, None, NOW, NOW, "ok", "", 0, NOW))
    _down(store, news.BIZNESRADAR)
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid"), (BETA, TUE, -4.0, -0.08, "liquid")))
    t = nc.coverage_table(nc.attribute(inp, store, _names(inp)), ["band"]).row(0, named=True)
    assert (t["measured"], t["partial"]) == (2, 1)


def test_unavailable_google_does_not_hold_moves_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    assert nc.attribute(inp, store, _names(inp), google_min_z=3.0)["cause"].to_list() == ["pending"]
    _down(store, news.GOOGLE, "block page")
    assert nc.attribute(inp, store, _names(inp), google_min_z=3.0)["cause"].to_list() == ["none"]


def test_source_is_available_again_after_a_good_run(tmp_path: Path) -> None:
    store = nc.Store(tmp_path)
    _down(store, news.BIZNESRADAR)
    assert nc.Store(tmp_path).unavailable() == {news.BIZNESRADAR}  # survives a restart
    store.set_source(news.BIZNESRADAR, nc.PoolResult(8, False), NOW)
    assert nc.Store(tmp_path).unavailable() == set()


def test_pool_says_why_it_stopped() -> None:
    import threading

    def work(_c: object, _job: int, _th: news.HostThrottle) -> news.Outcome:
        return news.Outcome.UNREACHABLE

    res = nc.run_pool(
        "t", list(range(10)), work, workers=1, make_client=_Client, stop=threading.Event()
    )  # type: ignore[arg-type]
    assert res.stopped and "could not connect" in res.reason
    ok = nc.run_pool(
        "t", [1], lambda *_: news.Outcome.OK, workers=1, make_client=_Client, stop=threading.Event()
    )  # type: ignore[arg-type]
    assert not ok.stopped and ok.reason == ""


def test_report_names_unavailable_sources(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _down(store, news.BIZNESRADAR)
    store.set_source(news.BANKIER, nc.PoolResult(8, False), NOW)
    inp = _inputs(_moves((ALFA, TUE, -4.0, -0.08, "liquid")))
    md = nc.report_markdown(
        nc.attribute(inp, store, _names(inp)),
        TUE,
        TUE,
        3.0,
        nc.fetch_summary(store),
        store.sources(),
    )
    assert "**biznesradar**: unavailable" in md and "measured without it" in md
    assert "**bankier**: available" in md


def test_second_collector_is_refused_while_one_runs(tmp_path: Path) -> None:
    with nc.collect_lock(tmp_path), pytest.raises(nc.CollectorBusy), nc.collect_lock(tmp_path):
        pass
    with nc.collect_lock(tmp_path):  # released after the first one ends
        pass
    assert not (tmp_path / "collect.lock").exists()


def test_stock_moving_its_own_index_is_not_a_sector_cause(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inp = _inputs(
        _moves((ALFA, TUE, 4.0, 0.08, "liquid")),
        sector_index={ALFA: "WIG-GORNIC"},
        sectors={("WIG-GORNIC", TUE): (0.05, 3.0)},
        peers={
            ("WIG-GORNIC", TUE): [(ALFA, 4.0), ("P1", 0.9), ("P2", 0.0), ("P3", -0.3), ("P4", 0.2)]
        },
    )
    assert nc.attribute(inp, store, _names(inp))["cause"].to_list() == ["none"]


def test_sector_needs_enough_peers() -> None:
    assert not nc.sector_is_broad([("A", 3.0), ("P1", 2.0), ("P2", 2.0)], "A", 3.0)
    assert nc.sector_is_broad(
        [("A", 3.0), *[(f"P{i}", 2.0) for i in range(3)], ("P9", 0.0)], "A", 3.0
    )
    assert not nc.sector_is_broad(
        [("A", 3.0), ("P1", 2.0), ("P2", 2.0), *[(f"Q{i}", 0.0) for i in range(3)]], "A", 3.0
    )
    assert not nc.sector_is_broad([("A", 3.0), ("P1", -2.0), ("P2", 0.0), ("P3", 0.0)], "A", 3.0)


def test_best_report_of_the_session_is_the_cause() -> None:
    at = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    ev = pl.DataFrame(
        {
            "isin": [ALFA, ALFA, ALFA],
            "known_at": [at, at.replace(hour=10), at.replace(hour=11)],
            "title": [
                "Informacja o stanie stosowania Dobrych Praktyk",
                "Aneks do umowy kredytu",
                "Porozumienie w sprawie wezwania",
            ],
            "source_url": ["u1", "u2", "u3"],
            "kind": ["Bieżący"] * 3,
            "signal_type": ["other", "inside_info", "inside_info"],
            "subtype": [None, "financing", "m_and_a"],
            "attachments_json": [None] * 3,
        }
    )
    reps = nc.report_sessions(ev)[(ALFA, TUE)]
    assert [r["url"] for r in reps] == ["u3", "u2"]
