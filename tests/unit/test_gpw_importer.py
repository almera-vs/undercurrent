# ruff: noqa: E501  (fixture rows are kept one per line)
"""Periodic reports from the exchange's own pages: form, selected data, discovery, the run."""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from undercurrent import esef, lake
from undercurrent import gpw_importer as g
from undercurrent.fetch import PoliteClient
from undercurrent.ingest import parse_listing

FIX = Path(__file__).parents[1] / "fixtures" / "periodic"
KNOWN = datetime(2026, 4, 30, 21, 57, tzinfo=UTC)


def _page(name: str) -> str:
    return next(FIX.glob(f"gpw_*_{name}.html")).read_text("utf-8")


def _facts(name: str, isin: str = "PLOPTEM00012") -> dict[tuple[str, date], dict]:
    title, _, df = g.facts_from_page(_page(name), g.Candidate(1, isin, KNOWN))
    assert title is not None
    return {(r["concept"], r["period_end"]): r for r in df.iter_rows(named=True)}


# ------------------------------------------------------------------ the page


def test_title_of_annual_and_quarterly_forms():
    rr = g.parse_title(_page("rr"))
    assert rr is not None
    assert (rr.form, rr.kind, rr.consolidated, rr.fiscal_year) == ("RR", "annual", False, 2025)
    assert (rr.period_start, rr.period_end, rr.currency) == (
        date(2025, 1, 1),
        date(2025, 12, 31),
        "PLN",
    )
    q = g.parse_title(_page("qsr_mln_two_scopes"))
    assert q is not None
    assert (q.form, q.kind, q.consolidated, q.quarter) == ("QSr", "quarter", True, 4)
    assert g.parse_title("<html><body><p>Zwołanie WZ</p></body></html>") is None


def test_annual_report_facts_with_prior_year():
    f = _facts("rr")
    fy, prior = date(2025, 12, 31), date(2024, 12, 31)
    rev = f[("revenue", fy)]
    assert rev["value"] == 100_223_000 and rev["unit"] == "PLN"
    assert rev["period_start"] == date(2025, 1, 1) and rev["period_kind"] == "annual"
    assert f[("revenue", prior)]["value"] == 70_301_000
    assert f[("revenue", prior)]["period_start"] == date(2024, 1, 1)
    assert f[("net_profit", prior)]["value"] == -10_259_000
    assert f[("assets", fy)]["period_start"] is None  # a balance: an instant
    assert f[("eps_basic", fy)]["value"] == 0.39
    assert f[("eps_basic", fy)]["unit"] == "PLN/xbrli:shares"
    assert f[("shares_weighted", fy)]["value"] == 7_871_166  # a count, never scaled
    assert f[("liabilities", fy)]["value"] == 5_512_000 + 21_011_000  # long + short
    r = f[("cash", fy)]
    assert r["filing_id"] == "gpw:1" and r["lei"] is None and r["isin"] == "PLOPTEM00012"
    assert r["known_at"] == KNOWN and r["fiscal_period_end"] == fy and r["form"] == "RR"
    # "Zysk (strata) na sprzedaży" is neither gross nor operating profit
    assert f[("operating_profit", fy)]["value"] == 2_428_000
    assert ("gross_profit", fy) not in f


def test_millions_group_figures_and_ebit_not_ebitda():
    f = _facts("qsr_mln_two_scopes")
    end = date(2025, 12, 31)
    assert f[("revenue", end)]["value"] == 267_329e6
    assert f[("revenue", end)]["period_start"] == date(2025, 1, 1)  # four quarters cumulated
    assert f[("operating_profit", end)]["value"] == 18_482e6  # EBIT, not the EBITDA above it
    assert f[("net_profit_parent", end)]["value"] == 11_110e6
    assert f[("eps_basic", end)]["value"] == 9.57  # "zysk netto i rozwodniony … na jedną akcję"
    assert f[("assets", end)]["value"] == 272_401e6  # the group, not the parent's 206 502
    assert f[("investing_cash_flow", end)]["value"] == -30_849e6  # "_30 849_" is (30 849)
    assert all(r["period_kind"] == "quarter" for r in f.values())
    assert {k[1] for k in f} == {end}  # no prior column for an interim report


def test_bracketed_negatives():
    f = _facts("qsr_brackets")
    end = date(2026, 3, 31)
    assert f[("profit_before_tax", end)]["value"] == -14_937_000
    assert f[("net_profit_parent", end)]["value"] == -16_872_000
    assert f[("eps_basic", end)]["value"] == -0.57


def test_bank_with_a_millions_line_and_per_shareholder_profit():
    f = _facts("bank_mln_row")
    end = date(2026, 3, 31)
    assert f[("assets", end)]["value"] == 356_347e6
    # "zysk netto przypadający na akcjonariuszy Banku" is a profit, not a per-share figure
    assert f[("net_profit_parent", end)]["value"] == 1_232e6
    assert f[("eps_basic", end)]["value"] == 4.69
    assert ("revenue", end) not in f  # a bank's form has no sales line


@pytest.mark.parametrize(
    ("label", "concept"),
    [
        ("I. I. Przychody ze sprzedaży produktów, towarów i materiałów", "revenue"),
        ("II. Pozostałe przychody operacyjne", None),
        ("Przychody z tytułu odsetek", None),
        ("II. Zysk _strata_ brutto ze sprzedaży", "gross_profit"),
        ("III. Zysk na działalności operacyjnej", "operating_profit"),
        ("Zysk z działalności operacyjnej powiększony o amortyzację _EBITDA_", None),
        ("IV. Zysk _strata_ przed opodatkowaniem", "profit_before_tax"),
        ("V. Zysk _strata_ netto grupy kapitałowej", "net_profit"),
        ("Zysk netto z działalności kontynuowanej i zaniechanej", "net_profit"),
        ("Zysk netto z działalności kontynuowanej", "net_continuing"),  # only to check
        ("VI. Zysk _strata_ netto z działalności zaniechanej", "net_discontinued"),
        ("XVIII. Kapitał własny, ogółem", "equity"),
        ("XIX. Zobowiazania długoterminowe, razem", "noncurrent_liabilities"),
        ("IV. Zysk _strata_ do opodatkowania", "profit_before_tax"),
        ("III. Zysk operacyjny", "operating_profit"),
        ("Zysk _strata_ brutto z działalności kontynuowanej", None),
        ("Zysk netto przypadający na udziały niedające kontroli", None),
        ("Całkowite dochody netto", None),
        ("XX. Kapitał przypadający akcjonariuszom podmiotu dominującego", "equity_parent"),
        ("XIX. Rozwodniony zysk na jedną akcję", None),
        ("Wypłacona dywidenda na jedną akcję _w zł/euro_", None),
        ("XXII. Wartość księgowa na 1 akcję _w zł / EUR_", "book_value_per_share"),
        ("XII. Aktywa razem na dzień 30.06.2026 i 31.12.2025", "assets"),
        ("Aktywa trwałe zaklasyfikowane jako przeznaczone do sprzedaży", None),
        ("VIII. Środki pieniężne netto z działalności operacyjnej", "operating_cash_flow"),
        ("X. Zmiana netto stanu środków pieniężnych", None),
        ("XVI. Zobowiązania razem", "liabilities"),
        ("XV. Liczba akcji _w szt._", "shares_outstanding"),
        (
            "XI. Liczba akcji w mln zastosowana do obliczenia zysku na jedną akcję",
            "shares_weighted",
        ),
        # foreign issuers fill the form in English
        ("I. Revenues", "revenue"),
        ("II. Profit/loss from operations", "operating_profit"),
        ("VI. Profit/loss for the period", "net_profit"),
        (
            "VIII. Profit/loss for the period attributable to shareholders of the parent",
            "net_profit_parent",
        ),
        ("IV. Profit/loss for the period from continuing operations", None),
        ("XXIV. Basic earnings per share from discontinued operation _EUR_", None),
        ("XIII. Total assets", "assets"),
        ("Aktywa / pasywa razem", "assets"),
        ("Razem aktywa", "assets"),
        (
            "Aktywa, razem na koniec półrocza bieżącego roku obrotowego i na koniec poprzedniego",
            "assets",
        ),
        ("Aktywa trwałe", "noncurrent_assets"),
        ("Zobowiązania i rezerwy długoterminowe", "noncurrent_liabilities"),
        ("Średnioważona liczba akcji w sztukach", "shares_weighted"),
        ("Kapitał własny przypadający akcjonariuszom emitenta", "equity_parent"),
        ("Przepływy pieniężne netto, razem", None),
        ("IX. Cash flows from operating activities", "operating_cash_flow"),
    ],
)
def test_labels(label, concept):
    assert g.concept_for(label) == concept


@pytest.mark.parametrize(
    ("cell", "value"),
    [("100 223,00", 100_223.0), ("-11 418,00", -11_418.0), ("_14 937_", -14_937.0), ("-", 0.0)],
)
def test_cell_numbers(cell, value):
    assert g._cell_number(cell) == value


def _sheet(rows: list[tuple[str, str, str, str, str]]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (
        "<html><body><p>KOMISJA NADZORU FINANSOWEGO Raport roczny RR 2025 obejmujący okres "
        "od 2025-01-01 do 2025-12-31 w walucie PLN</p>"
        "<h4>Nazwa arkusza: <a>WYBRANE DANE FINANSOWE</a></h4><table>"
        "<tr><td>WYBRANE DANE FINANSOWE</td><td>w tys. zł</td><td></td><td>w tys. EUR</td></tr>"
        f"<tr><td></td><td>2025</td><td>2024</td><td>2025</td><td>2024</td></tr>{body}</table>"
        "</body></html>"
    )


def test_columns_that_are_not_zloty_and_euro_are_refused():
    same = [
        ("Przychody ze sprzedaży", "1 000", "900", "1 000", "900"),
        ("Zysk netto", "100", "90", "100", "90"),
        ("Aktywa razem", "5 000", "4 000", "5 000", "4 000"),
    ]
    _, sheet, df = g.facts_from_page(_sheet(same), g.Candidate(1, "PLX", KNOWN))
    assert sheet is not None and sheet.disagree == 3 and df.height == 0


def _values(rows, hint=None) -> dict[tuple[str, date], float]:
    _, _, df = g.facts_from_page(_sheet(rows), g.Candidate(1, "PLX", KNOWN), hint)
    return {(r["concept"], r["period_end"]): r["value"] for r in df.iter_rows(named=True)}


FY, PRIOR = date(2025, 12, 31), date(2024, 12, 31)


def test_money_typed_in_zloty_under_a_thousands_header():
    # the exchange's share count confirms the sheet's: the money is what is off
    rows = [
        ("Przychody ze sprzedaży", "4 300 000", "4 000 000", "1 000 000", "930 000"),
        ("Zysk netto", "430 000", "400 000", "100 000", "93 000"),
        ("Zysk netto na jedną akcję", "0,43", "0,40", "0,10", "0,09"),
        ("Średnia ważona liczba akcji", "1 000 000", "1 000 000", "1 000 000", "1 000 000"),
    ]
    f = _values(rows, hint=1_050_000)
    assert f[("net_profit", FY)] == 430_000
    assert f[("revenue", PRIOR)] == 4_000_000
    assert f[("shares_weighted", FY)] == 1_000_000
    # without the exchange's count it cannot tell which is off: the report is refused
    _, sheet, df = g.facts_from_page(_sheet(rows), g.Candidate(1, "PLX", KNOWN))
    assert sheet is not None and sheet.problems == ["eps"] and df.height == 0


def test_share_count_in_thousands_unsaid():
    # the money is right; the count is in thousands and the label does not say so
    rows = [
        ("Przychody ze sprzedaży", "932 682", "830 867", "217 916", "196 121"),
        ("Zysk netto", "39 437", "30 991", "9 214", "7 315"),
        ("Zysk netto na jedną akcję", "0,20", "0,16", "0,05", "0,04"),
        ("Liczba akcji", "199 000", "199 000", "199 000", "199 000"),
    ]
    f = _values(rows, hint=198_000_000)
    assert f[("revenue", FY)] == 932_682_000
    assert f[("shares_outstanding", FY)] == 199_000_000


def test_share_count_in_thousands_said_with_underscored_brackets():
    rows = [
        ("Zysk _strata_ netto", "3 787", "-8 543", "891", "-2 024"),
        ("Zysk _strata_ na jedną akcję zwykłą _w zł / EUR _", "2,99", "-6,74", "0,70", "-1,60"),
        ("Średnioważona liczba akcji _tys. szt._", "1 267", "1 267", "1 267", "1 267"),
    ]
    f = _values(rows)
    assert f[("net_profit", FY)] == 3_787_000
    assert f[("shares_weighted", FY)] == 1_267_000


# ------------------------------------------------------------------ finding the reports


def test_unlabelled_listing_row_is_kept_as_periodic():
    html = (
        '<li><span class="date">30-04-2026 23:57:29 | ESPI | /2025</span>'
        '<strong class="name"><a href="komunikat?geru_id=487865"> OPTEAM SPÓŁKA AKCYJNA '
        "(PLOPTEM00012) </a></strong><p> </p></li>"
    )
    (row,) = parse_listing(html, "gpw")
    assert (row.geru_id, row.kind, row.system, row.report_number) == (
        487865,
        "Okresowy",
        "ESPI",
        "/2025",
    )


def _reports(root: Path, rows: list[dict]) -> None:
    df = pl.DataFrame(
        rows,
        schema={
            "site": pl.String,
            "geru_id": pl.Int64,
            "isin": pl.String,
            "report_number": pl.String,
            "kind": pl.String,
            "published_at": pl.Datetime("us", "UTC"),
        },
    )
    for day, part in df.group_by(pl.col("published_at").dt.date()):
        lake.append_partition(root, "bronze", "reports", day[0], part, key="geru_id")


def test_lake_candidates_are_unnumbered_main_market_reports(tmp_path):
    _reports(
        tmp_path,
        [
            # an annual report the listing called "Bieżący"
            {"site": "gpw", "geru_id": 487865, "isin": "PLOPTEM00012", "report_number": "/2025", "kind": "Bieżący", "published_at": KNOWN},
            {"site": "gpw", "geru_id": 487866, "isin": "PLOPTEM00012", "report_number": "5/2026", "kind": "Bieżący", "published_at": KNOWN},
            # NewConnect's periodic reports are EBI files without the form
            {"site": "nc", "geru_id": 9, "isin": "PLNC00000001", "report_number": "/2025", "kind": "Roczny", "published_at": KNOWN},
            {"site": "gpw", "geru_id": 1, "isin": "PLOLD0000001", "report_number": "/2022", "kind": "Roczny", "published_at": datetime(2023, 4, 1, tzinfo=UTC)},
        ],
    )  # fmt: skip
    assert [c.geru_id for c in g.lake_candidates(tmp_path)] == [487865]


TAB = """<table class="footable table"><tbody>
<tr><td><a target="_blank" href="komunikat?geru_id=497610">2026-09-24 19:46:15</a></td>
<td> Półroczny | /2026<br> <a target="_blank" href="komunikat?geru_id=497610"> P </a></td></tr>
<tr><td><a target="_blank" href="komunikat?geru_id=493021&amp;title=Wykaz">2026-07-01 13:21:45</a></td>
<td> Bieżący | 5/2026<br> <a target="_blank" href="komunikat?geru_id=493021&amp;title=Wykaz"> Wykaz akcjonariuszy </a></td></tr>
<tr><td><a target="_blank" href="komunikat?geru_id=487865">2026-04-30 23:57:29</a></td>
<td> Kwartalny | /2025<br> <a target="_blank" href="komunikat?geru_id=487865"> RR </a></td></tr>
<tr><td><a target="_blank" href="komunikat?geru_id=465075">2025-04-29 17:10:59</a></td>
<td> Bieżący | /2024<br> <a target="_blank" href="komunikat?geru_id=465075"> RR </a></td></tr>
<tr><td><a target="_blank" href="komunikat?geru_id=431296">2023-11-23 17:32:25</a></td>
<td> Kwartalny | /2023<br> <a target="_blank" href="komunikat?geru_id=431296"> QSr </a></td></tr>
</tbody></table>"""


def test_company_card_names_reports_by_form_code():
    rows = g.parse_tab(TAB)
    assert [(gid, t) for gid, _, t in rows][:3] == [
        (497610, "P"),
        (493021, "Wykaz akcjonariuszy"),
        (487865, "RR"),
    ]
    assert rows[2][1] == datetime(2026, 4, 30, 21, 57, 29, tzinfo=UTC)  # CEST → UTC


CURRENT_REPORT = (FIX.parent / "pages" / "gpw_500131_espi_legacy_insider.html").read_text("utf-8")


def _client(tmp_path: Path, calls: list[str]) -> PoliteClient:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        calls.append(url)
        if "reportsTab" in url:
            return httpx.Response(200, text=TAB if "offset=0" in url else "")
        if "komunikat?geru_id=487865" in url:
            return httpx.Response(200, text=_page("rr"))
        if "komunikat?geru_id=497610" in url:  # a current report without a running number
            return httpx.Response(200, text=CURRENT_REPORT)
        if "komunikat?geru_id=465075" in url:
            return httpx.Response(200, text=_page("rr").replace("RR 2025", "RR 2024"))
        return httpx.Response(404)

    return PoliteClient(
        tmp_path / "cache", min_interval_s=0, max_jitter_s=0, transport=httpx.MockTransport(handler)
    )


def test_card_is_paged_back_to_since(tmp_path):
    calls: list[str] = []
    with _client(tmp_path, calls) as c:
        found = g.tab_candidates(c, "PLOPTEM00012", since=date(2024, 1, 1))
    assert [x.geru_id for x in found] == [497610, 487865, 465075]  # the QSr of 2023 is older
    assert len(calls) == 1  # the page reached past 2024-01-01: no second page


def test_ingest_reads_lake_candidates_and_card_gaps_once(tmp_path):
    _reports(
        tmp_path,
        [{"site": "gpw", "geru_id": 487865, "isin": "PLOPTEM00012", "report_number": "/2025", "kind": "Bieżący", "published_at": KNOWN}],
    )  # fmt: skip
    now = datetime(2026, 11, 17, tzinfo=UTC)
    calls: list[str] = []
    with _client(tmp_path, calls) as c:
        out = g.ingest(
            c, tmp_path, listed=["PLOPTEM00012", "PLOTHER00001"], now=now, check_canary=False
        )
    # OPTEAM's annual report is in the lake; the other company's card is paged for one
    assert out["ok"] == 2 and out["companies"] == 1
    assert any("gls_isin=PLOTHER00001" in u for u in calls)
    assert not any("gls_isin=PLOPTEM00012" in u for u in calls)
    state = lake.read_state(tmp_path, g.STATE)
    assert state is not None
    by_id = {r["geru_id"]: r for r in state.iter_rows(named=True)}
    assert by_id[487865]["status"] == "ok" and by_id[487865]["form"] == "RR"
    assert by_id[497610]["status"] == "not_periodic"
    # nothing is read twice; the card is not paged again within a month
    calls.clear()
    with _client(tmp_path, calls) as c:
        again = g.ingest(
            c, tmp_path, listed=["PLOPTEM00012", "PLOTHER00001"], now=now, check_canary=False
        )
    assert again["reports"] == 0 and calls == []


# ------------------------------------------------------------------ read with the ESEF facts


def test_fundamentals_as_of_reads_native_annual_facts_only(tmp_path):
    for name, gid in [("rr", 487865), ("qsr_brackets", 490370)]:
        _, _, df = g.facts_from_page(_page(name), g.Candidate(gid, "PLOPTEM00012", KNOWN))
        lake.append_partition(tmp_path, "bronze", g.TABLE, KNOWN.date(), df, key="fact_key")
    facts = esef.fundamentals_as_of(tmp_path, datetime(2026, 12, 1, tzinfo=UTC))
    assert set(facts["filing_id"].to_list()) == {"gpw:487865"}
    assert set(facts["form"].to_list()) == {"RR"}
    assert esef.fundamentals_as_of(tmp_path, datetime(2026, 4, 1, tzinfo=UTC)).height == 0
    (st,) = esef.statements(facts)
    assert st["cur"]["revenue"] == 100_223_000 and st["prev"]["revenue"] == 70_301_000
    assert st["prior_year_end"] == date(2024, 12, 31)


def _f(fid: str, concept: str, value: float, *, end=date(2025, 12, 31), flow=True, **kw):
    return {
        "filing_id": fid,
        "concept": concept,
        "value": value,
        "unit": "PLN",
        "period_start": date(end.year, 1, 1) if flow else None,
        "period_end": end,
        "fiscal_period_end": date(2025, 12, 31),
        "known_at": KNOWN,
        **kw,
    }


def test_native_report_wins_and_esef_fills_lines_of_the_same_scope():
    by_filing = {
        "gpw:1": [_f("gpw:1", "assets", 1000, flow=False, form="SRR"), _f("gpw:1", "revenue", 500, form="SRR")],
        "bankier-2": [_f("bankier-2", "assets", 1001, flow=False), _f("bankier-2", "revenue", 499), _f("bankier-2", "capex_ppe", 40)],
        # the parent company alone: another scope, never mixed in
        "bankier-3": [_f("bankier-3", "assets", 300, flow=False), _f("bankier-3", "depreciation", 9)],
    }  # fmt: skip
    mine = esef._year_facts(by_filing, date(2025, 12, 31))
    fy = date(2025, 12, 31)
    assert esef._value(mine, "revenue", fy, "PLN", duration=True) == 500
    assert esef._value(mine, "capex_ppe", fy, "PLN", duration=True) == 40
    assert esef._value(mine, "depreciation", fy, "PLN", duration=True) is None


def test_consolidated_native_form_beats_the_standalone_one_filed_with_it():
    later = datetime(2026, 4, 30, 22, tzinfo=UTC)
    by_filing = {
        "gpw:1": [_f("gpw:1", "assets", 1000, flow=False, form="SRR")],
        "gpw:2": [_f("gpw:2", "assets", 900, flow=False, form="RR", known_at=later)],
    }
    assert esef._main_filing(by_filing, date(2025, 12, 31)) == "gpw:1"
