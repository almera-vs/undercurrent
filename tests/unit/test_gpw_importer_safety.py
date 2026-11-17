# ruff: noqa: E501  (fixture rows are kept one per line)
"""The checks every report must pass, and the run's own safety: retries, re-reads, alarms."""

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from undercurrent import esef, lake
from undercurrent import gpw_importer as g
from undercurrent.fetch import PoliteClient

FIX = Path(__file__).parents[1] / "fixtures" / "periodic"
KNOWN = datetime(2026, 4, 30, 21, 57, tzinfo=UTC)
FY = date(2025, 12, 31)
EURO = 4.3


def _page(name: str) -> str:
    return next(FIX.glob(f"gpw_*_{name}.html")).read_text("utf-8")


def _r(label: str, cur: float) -> tuple[str, ...]:
    """A row of the sheet: the figure for both years, then in euros at ``EURO``."""

    def fmt(x: float) -> str:
        return f"{x:.2f}".replace(".", ",")

    return (label, fmt(cur), fmt(cur), fmt(cur / EURO), fmt(cur / EURO))


def _sheet(rows: list[tuple[str, ...]]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (
        "<html><body><table><tr class='nTekst'><td>KOMISJA NADZORU FINANSOWEGO Raport roczny "
        "RR 2025 obejmujący okres od 2025-01-01 do 2025-12-31 w walucie PLN</td></tr></table>"
        "<h4>Nazwa arkusza: <a>WYBRANE DANE FINANSOWE</a></h4><table>"
        "<tr><td>WYBRANE DANE FINANSOWE</td><td>w tys. zł</td><td></td><td>w tys. EUR</td></tr>"
        f"<tr><td></td><td>2025</td><td>2024</td><td>2025</td><td>2024</td></tr>{body}</table>"
        "</body></html>"
    )


def _checked(rows: list[tuple[str, ...]], hint: float | None = None):
    _, sheet, df = g.facts_from_page(_sheet(rows), g.Candidate(1, "PLX", KNOWN), hint)
    assert sheet is not None
    return sheet, df


# ------------------------------------------------------------------ checks


def test_a_balance_sheet_that_does_not_add_up_is_refused():
    sheet, df = _checked(
        [_r("Aktywa razem", 1000), _r("Zobowiązania razem", 400), _r("Kapitał własny", 300)]
    )
    assert "balance" in sheet.problems and df.height == 0


def test_parent_equity_alone_leaves_room_for_minorities():
    parent = "Kapitał własny przypadający akcjonariuszom jednostki dominującej"
    sheet, df = _checked([_r("Aktywa razem", 1000), _r("Zobowiązania razem", 400), _r(parent, 550)])
    assert sheet.problems == [] and "minorities_gap" in sheet.warnings and df.height > 0
    sheet, _ = _checked([_r("Aktywa razem", 1000), _r("Zobowiązania razem", 600), _r(parent, 550)])
    assert "balance" in sheet.problems  # more than the assets: a misread


def test_eps_that_contradicts_profit_and_shares_is_refused():
    sheet, df = _checked(
        [
            _r("Zysk netto", 1000),
            _r("Zysk netto na jedną akcję", 5.0),
            _r("Średnia ważona liczba akcji", 1_000_000),
        ]
    )
    # 1 000 thousand ÷ 1 000 000 shares is 1.00, not 5.00
    assert sheet.problems == ["eps"] and df.height == 0


def test_eps_in_grosze_and_a_long_share_count_label():
    sheet, df = _checked(
        [
            _r("Zysk _strata_ netto", 22_877),
            _r("Zysk _strata_ na jedną akcję _w gr/eurocent_", 91),
            _r(
                "Średnia ważona liczba wyemitowanych akcji zwykłych zastosowana do obliczenia "
                "zysku na jedną akcję",
                25_496_261,
            ),
        ]
    )
    assert sheet.problems == []
    f = {(r["concept"], r["period_end"]): r["value"] for r in df.iter_rows(named=True)}
    assert f[("eps_basic", FY)] == pytest.approx(0.91)
    assert f[("shares_weighted", FY)] == 25_496_261


@pytest.mark.parametrize(
    ("rows", "problem"),
    [
        ([_r("Aktywa razem", 50), _r("Aktywa obrotowe", 10)], "assets_tiny"),
        ([_r("Aktywa razem", 1000), _r("Aktywa obrotowe", 1500)], "current_assets>assets"),
        ([_r("Przychody ze sprzedaży", -100)], "revenue<0"),
        (
            [_r("Zobowiązania razem", 100), _r("Zobowiązania krótkoterminowe", 300)],
            "current_liabilities>liabilities",
        ),
    ],
)
def test_parts_and_sizes(rows, problem):
    sheet, df = _checked(rows)
    assert problem in sheet.problems and df.height == 0


def test_euro_columns_that_are_not_an_exchange_rate_are_refused():
    rows = [
        ("Przychody ze sprzedaży", "1 000", "900", "1 000", "900"),
        ("Zysk netto", "100", "90", "100", "90"),
        ("Aktywa razem", "5 000", "4 000", "5 000", "4 000"),
    ]
    sheet, df = _checked(rows)
    assert "eur_columns" in sheet.problems and df.height == 0


# ------------------------------------------------------------------ the run's own safety


def _one_report(root: Path) -> None:
    df = pl.DataFrame(
        [{"site": "gpw", "geru_id": 487865, "isin": "PLOPTEM00012", "report_number": "/2025", "kind": "Bieżący", "published_at": KNOWN}],
        schema={"site": pl.String, "geru_id": pl.Int64, "isin": pl.String, "report_number": pl.String, "kind": pl.String, "published_at": pl.Datetime("us", "UTC")},
    )  # fmt: skip
    lake.append_partition(root, "bronze", "reports", KNOWN.date(), df, key="geru_id")


def _serving(root: Path, pages: dict[int, httpx.Response]) -> PoliteClient:
    def handler(req: httpx.Request) -> httpx.Response:
        for gid, resp in pages.items():
            if f"geru_id={gid}" in str(req.url):
                return resp
        return httpx.Response(404)

    return PoliteClient(
        root / "cache", min_interval_s=0, max_jitter_s=0, transport=httpx.MockTransport(handler)
    )


NOW = datetime(2026, 11, 17, tzinfo=UTC)


def _state_row(root: Path) -> dict:
    state = lake.read_state(root, g.STATE)
    assert state is not None and state.height == 1
    return state.row(0, named=True)


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(503, text="<html>Service Unavailable</html>"),
        httpx.Response(200, text="<html><body>Przerwa techniczna</body></html>"),
    ],
)
def test_a_failed_page_is_retried_then_given_up(tmp_path, resp):
    _one_report(tmp_path)
    for attempt in range(1, g.MAX_ATTEMPTS + 1):
        with _serving(tmp_path, {487865: resp}) as c:
            out = g.ingest(c, tmp_path, now=NOW, check_canary=False)
        assert out["errors"] == 1 and out["reports"] == 1
        row = _state_row(tmp_path)
        assert row["status"] == "error" and row["attempts"] == attempt
    with _serving(tmp_path, {487865: resp}) as c:
        assert g.ingest(c, tmp_path, now=NOW, check_canary=False)["reports"] == 0
    assert not (tmp_path / "bronze" / g.TABLE).exists()


def test_a_page_that_fails_once_is_read_next_run(tmp_path):
    _one_report(tmp_path)
    with _serving(tmp_path, {487865: httpx.Response(503)}) as c:
        g.ingest(c, tmp_path, now=NOW, check_canary=False)
    with _serving(tmp_path, {487865: httpx.Response(200, text=_page("rr"))}) as c:
        out = g.ingest(c, tmp_path, now=NOW, check_canary=False)
    assert out["ok"] == 1
    row = _state_row(tmp_path)
    assert row["status"] == "ok" and row["error"] is None


def test_reports_are_read_again_when_the_parser_changes(tmp_path, monkeypatch):
    _one_report(tmp_path)
    page = {487865: httpx.Response(200, text=_page("rr"))}
    with _serving(tmp_path, page) as c:
        g.ingest(c, tmp_path, now=NOW, check_canary=False)
    v = g.PARSER_VERSION
    monkeypatch.setattr(g, "PARSER_VERSION", v + 1)
    with _serving(tmp_path, page) as c:
        assert g.ingest(c, tmp_path, now=NOW, check_canary=False)["ok"] == 1
    facts = g.native_facts(tmp_path, datetime(2027, 1, 1, tzinfo=UTC))
    assert set(facts["parser_version"].to_list()) == {v + 1}
    assert facts.filter(pl.col("concept") == "revenue").height == 2  # 2025 and 2024, once each
    # a newer reading that is refused takes the older facts out of use
    monkeypatch.setattr(g, "PARSER_VERSION", v + 2)
    bad = _page("rr").replace("60 459,00", "99 459,00")  # total assets no longer add up
    shutil.rmtree(tmp_path / "cache")  # report pages are cached: serve the changed one
    with _serving(tmp_path, {487865: httpx.Response(200, text=bad)}) as c:
        out = g.ingest(c, tmp_path, now=NOW, check_canary=False)
    assert out["failed_checks"] == 1
    assert "balance" in _state_row(tmp_path)["checks"]
    assert g.native_facts(tmp_path, datetime(2027, 1, 1, tzinfo=UTC)).height == 0


def test_canary(tmp_path):
    with _serving(tmp_path, {487865: httpx.Response(200, text=_page("rr"))}) as c:
        assert g.canary(c) is None
    wrong = _page("rr").replace("100 223,00", "100 224,00")
    with _serving(tmp_path, {487865: httpx.Response(200, text=wrong)}) as c:
        assert "reads wrong" in str(g.canary(c))
    with _serving(tmp_path, {}) as c:
        assert "failed" in str(g.canary(c)) or "reads wrong" in str(g.canary(c))


def _counts(**kw):
    return {"reports": 0, "periodic": 0, "ok": 0, "errors": 0, "canary": None} | kw


@pytest.mark.parametrize(
    ("counts", "alarm"),
    [
        (_counts(reports=100, periodic=95, ok=90), None),
        (_counts(reports=5, periodic=5, ok=0), None),  # too few to judge
        (_counts(reports=40, periodic=0), "no periodic form"),
        (_counts(reports=100, periodic=90, ok=30), "only 30 of 90"),
        (_counts(reports=40, periodic=10, ok=10, errors=20), "failed to load"),
        (_counts(canary="canary report 487865 reads wrong"), "canary"),
    ],
)
def test_health(counts, alarm):
    alarms = g.health(counts)
    assert (alarms == []) if alarm is None else any(alarm in a for a in alarms)


# ------------------------------------------------------------------ reconciliation


def test_reconcile_compares_native_and_esef_of_the_same_scope(tmp_path):
    _, _, df = g.facts_from_page(_page("rr"), g.Candidate(487865, "PLOPTEM00012", KNOWN))
    lake.append_partition(tmp_path, "bronze", g.TABLE, KNOWN.date(), df, key="fact_key")
    lei = "259400TESTLEI0000001"
    rows = [
        ("revenue", date(2025, 1, 1), 100_223_400.0),
        ("assets", None, 60_459_000.0),
        ("net_profit", date(2025, 1, 1), 3_570_000.0),  # the form says 3 070
    ]
    facts = pl.DataFrame(
        [
            {"fact_key": f"bankier-1:{c}", "filing_id": "bankier-1", "lei": lei, "fiscal_period_end": FY, "concept": c, "period_start": s, "period_end": FY, "value": v, "unit": "PLN", "decimals": -3, "known_at": KNOWN}
            for c, s, v in rows
        ],
        schema=esef.FACT_SCHEMA,
    )  # fmt: skip
    lake.append_partition(tmp_path, "bronze", esef.TABLE, KNOWN.date(), facts, key="fact_key")
    lake.write_state(
        tmp_path,
        esef.LEI_STATE,
        pl.DataFrame({"lei": [lei], "isin": ["PLOPTEM00012"], "checked_at": [KNOWN]}),
    )
    summary, worst = g.reconcile(tmp_path)
    by = {r["concept"]: r for r in summary.iter_rows(named=True)}
    assert by["revenue"]["within_1pct"] == 1.0 and by["assets"]["within_1pct"] == 1.0
    assert by["net_profit"]["within_5pct"] == 0.0
    assert worst.row(0, named=True)["concept"] == "net_profit"


def test_net_profit_that_its_own_parts_contradict_is_refused():
    sheet, df = _checked(
        [
            _r("V. Zysk _strata_ netto z działalności kontynuowanej", 366),
            _r("VI. Zysk _strata_ netto z działalności zaniechanej", 0),
            _r("VII. Zysk _strata_ netto_", 531),
        ]
    )
    assert sheet.problems == ["net_parts"] and df.height == 0
    sheet, df = _checked(
        [
            _r("Zysk netto z działalności kontynuowanej", 366),
            _r("Zysk netto z działalności zaniechanej", 165),
            _r("Zysk netto", 531),
        ]
    )
    assert sheet.problems == []
    assert set(df["concept"].to_list()) == {"net_profit"}  # the parts are not kept


# ------------------------------------------------------------------ native and ESEF together


def _f(fid, concept, value, *, flow=True, unit="PLN", **kw):
    return {
        "filing_id": fid,
        "concept": concept,
        "value": value,
        "unit": unit,
        "period_start": date(2025, 1, 1) if flow else None,
        "period_end": FY,
        "fiscal_period_end": FY,
        "known_at": KNOWN,
        **kw,
    }


def _read(by_filing, concept, flow=True, unit="PLN"):
    return esef._value(esef._year_facts(by_filing, FY), concept, FY, unit, duration=flow)


def test_esef_corrects_a_mistyped_money_line_of_the_same_report():
    by_filing = {
        "gpw:1": [_f("gpw:1", "assets", 1000, flow=False, form="SRR"), _f("gpw:1", "net_profit", 4309, form="SRR"), _f("gpw:1", "eps_basic", 0.91, unit="PLN/xbrli:shares", form="SRR")],
        "bankier-2": [_f("bankier-2", "assets", 1000.4, flow=False), _f("bankier-2", "net_profit", 3409), _f("bankier-2", "eps_basic", 91.0, unit="PLN/xbrli:shares"), _f("bankier-2", "capex_ppe", 40)],
    }  # fmt: skip
    assert _read(by_filing, "net_profit") == 3409  # the audited statement wins
    assert _read(by_filing, "capex_ppe") == 40  # a line the form lacks
    assert _read(by_filing, "eps_basic", unit="PLN/xbrli:shares") == 0.91  # grosze tagged as zł
    assert _read(by_filing, "assets", flow=False) == 1000  # within rounding: the form's


def test_a_report_of_another_scope_is_never_mixed_in():
    # the native standalone report and the group's ESEF filing, assets 7 % apart
    by_filing = {
        "gpw:1": [_f("gpw:1", "assets", 457.5, flow=False, form="RR"), _f("gpw:1", "revenue", 53.6, form="RR")],
        "bankier-2": [_f("bankier-2", "assets", 491.8, flow=False), _f("bankier-2", "revenue", 122.8), _f("bankier-2", "capex_ppe", 4)],
    }  # fmt: skip
    # not one report: the group's (larger) filing is read alone, nothing is mixed
    assert _read(by_filing, "revenue") == 122.8
    assert _read(by_filing, "assets", flow=False) == 491.8
    # a native group report (SRR) is read alone beside a filing it does not match
    by_filing["gpw:1"] = [_f("gpw:1", "assets", 457.5, flow=False, form="SRR"), _f("gpw:1", "revenue", 53.6, form="SRR")]  # fmt: skip
    assert _read(by_filing, "revenue") == 53.6
    assert _read(by_filing, "capex_ppe") is None


def test_provisions_left_out_of_liabilities_are_a_warning_not_a_refusal():
    rows = [_r("Aktywa razem", 1000), _r("Zobowiązania razem", 200), _r("Kapitał własny", 760)]
    sheet, df = _checked(rows)  # 4 % short: provisions and accruals the form leaves out
    assert sheet.problems == [] and "unlisted_liabilities" in sheet.warnings and df.height
    rows = [_r("Aktywa razem", 1000), _r("Zobowiązania razem", 200), _r("Kapitał własny", 500)]
    assert "balance" in _checked(rows)[0].problems  # 30 % short: something is misread
