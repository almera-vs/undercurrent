"""Portal copies of ESPI reports: listing parsers, matching, and the collection flow."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from undercurrent import forms, lake, portals
from undercurrent.fetch import FetchResult
from undercurrent.portals import ReportRef, best_match

TITLE = "Otrzymanie powiadomień o transakcjach w trybie art. 19 ust. 1 Rozporządzenia MAR"
PARKIET_PAGE = (
    "https://www.parkiet.com/komunikaty-espi/art45357481-przyklad-spolka-akcyjna-"
    "otrzymanie-powiadomien-o-transakcjach-w-trybie-art-19-ust-1-rozporzadzenia-mar"
)
SITEMAP = (
    '<?xml version="1.0"?><urlset>'
    f"<url><loc>{PARKIET_PAGE}</loc><lastmod>2026-10-19T16:27:23+02:00</lastmod></url>"
    "<url><loc>https://www.parkiet.com/gielda/art1-not-a-report</loc>"
    "<lastmod>2026-10-19T10:00:00+02:00</lastmod></url></urlset>"
)
BANKIER = (
    '<ul><li><div class="m-quotes-announcements-item">'
    '<span class="m-quotes-announcements-item__date">19.10.2026 14:27</span>'
    '<a class="m-quotes-announcements-item__anchor" '
    'href="https://www.bankier.pl/wiadomosc/PRZYKLAD-Otrzymanie-powiadomien-9174309.html">'
    f"PRZYKLAD S.A.: {TITLE}</a></div></li></ul>"
)
BIZNESRADAR = (
    '<div class="article-card"><div><h3>'
    '<a href="/a/162994,przyklad-sa-66-2026-otrzymanie-powiadomien">'
    f"PRZYKLAD SA (66/2026) {TITLE}</a></h3>"
    '<div class="time">2026-10-19, 14:27</div></div></div>'
)

REF = ReportRef(
    datetime(2026, 10, 19, 14, 27, 23),
    TITLE,
    ("PRZYKŁAD SPÓŁKA AKCYJNA", "PRZYKLAD"),
    "66/2026",
)


def test_parkiet_sitemap_time_is_warsaw_wall_clock() -> None:
    (c,) = portals.Parkiet().parse_sitemap(SITEMAP)
    assert c.published_local == datetime(2026, 10, 19, 14, 27, 23)
    assert best_match(REF, [c]) == c


def test_bankier_and_biznesradar_listings() -> None:
    (b,) = portals.Bankier().parse_listing(BANKIER)
    assert b.issuer == "PRZYKLAD S.A." and b.published_local == datetime(2026, 10, 19, 14, 27)
    (r,) = portals.BiznesRadar().parse_listing(BIZNESRADAR)
    assert r.number == "66/2026" and r.url.startswith("https://www.biznesradar.pl/a/162994")
    assert best_match(REF, [b]) == b and best_match(REF, [r]) == r


def test_matching_requires_issuer_time_and_number() -> None:
    (c,) = portals.Parkiet().parse_sitemap(SITEMAP)
    other_issuer = ReportRef(REF.published_local, REF.title, ("INNA FIRMA SPÓŁKA AKCYJNA",), None)
    assert best_match(other_issuer, [c]) is None
    late = ReportRef(datetime(2026, 10, 19, 15, 0), REF.title, REF.issuer_names, None)
    assert best_match(late, [c]) is None
    (r,) = portals.BiznesRadar().parse_listing(BIZNESRADAR)
    wrong_no = ReportRef(REF.published_local, REF.title, REF.issuer_names, "67/2026")
    assert best_match(wrong_no, [r]) is None


def test_pdf_links_are_filtered_per_portal() -> None:
    html = (
        '<a href="https://i.gremicdn.pl/file/abc/powiadomienie.pdf">x</a>'
        '<a href="https://apps.bonnier.pl/img/regulamin.pdf">terms</a>'
        '<a href="https://bonnier.pl/static/att/emitent/2026-07/zal.pdf">zal</a>'
    )
    assert portals.Parkiet().attachments(html, "https://www.parkiet.com/x") == [
        "https://i.gremicdn.pl/file/abc/powiadomienie.pdf"
    ]
    assert portals.Bankier().attachments(html, "https://www.bankier.pl/x") == [
        "https://bonnier.pl/static/att/emitent/2026-07/zal.pdf"
    ]


class FakeClient:
    """Serves canned bodies by URL; anything else is a 404."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> FetchResult:
        self.calls.append(url)
        for key, body in self.bodies.items():
            if url.startswith(key):
                return FetchResult(url, 200, body, "text/html", False, 0.0)
        raise RuntimeError(f"404 {url}")


def _seed_report(root: Path) -> None:
    row = {
        "report_id": "gpw:1",
        "site": "gpw",
        "geru_id": 1,
        "system": "ESPI",
        "kind": "Bieżący",
        "report_number": "66/2026",
        "issuer_name": "PRZYKŁAD SPÓŁKA AKCYJNA",
        "isin": "PLPRZYK00012",
        "title": REF.title,
        "published_at_local": "2026-10-19T14:27:23",
        "published_at": datetime(2026, 10, 19, 12, 27, 23, tzinfo=UTC),
        "known_at": datetime(2026, 10, 19, 12, 27, 23, tzinfo=UTC),
        "template": "espi_legacy",
        "subject": REF.title,
        "legal_basis": "Art. 19 ust. 3 Rozporządzenia MAR - informacja o transakcjach",
        "body_text": "Zarząd informuje o otrzymaniu powiadomienia w trybie art. 19 MAR.",
        "attachments_json": "[]",
        "extract_notes": None,
        "source_url": "https://www.gpw.pl/komunikat?geru_id=1",
        "content_sha256": "x",
        "ingested_at": datetime(2026, 10, 19, 18, 0, tzinfo=UTC),
    }
    from undercurrent.ingest import REPORT_SCHEMA

    lake.append_partition(
        root, "bronze", "reports", date(2026, 10, 19), pl.DataFrame([row], schema=REPORT_SCHEMA)
    )


def test_collect_forms_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_forms import STANDARD_PL

    _seed_report(tmp_path)
    page = PARKIET_PAGE
    pdf = "https://i.gremicdn.pl/file/abc/powiadomienie.pdf"
    client = FakeClient(
        {
            "https://www.parkiet.com/sitemaps/sitemap.202610.xml": SITEMAP.encode(),
            page: f'<a href="{pdf}">PDF</a>'.encode(),
            pdf: b"%PDF-fake",
        }
    )
    monkeypatch.setattr(forms, "pdf_text", lambda content: STANDARD_PL)
    monkeypatch.setattr(portals.Bankier, "reach_days", -1)  # listing portals out of reach
    monkeypatch.setattr(portals.BiznesRadar, "reach_days", -1)
    now = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    st = forms.collect_forms(client, tmp_path, [date(2026, 10, 19)], now=now)  # type: ignore[arg-type]
    assert (st.matched, st.written, st.missing) == (1, 1, 0)
    df = lake.read_table(tmp_path, "bronze", forms.TABLE)
    assert df is not None and df.height == 2 and set(df["portal"]) == {"parkiet"}
    # a second run finds nothing to do and makes no requests
    client.calls.clear()
    st2 = forms.collect_forms(client, tmp_path, [date(2026, 10, 19)], now=now)  # type: ignore[arg-type]
    assert st2.wanted == 0 and client.calls == []


def test_unmatched_reports_wait_in_pending_then_expire(tmp_path: Path) -> None:
    _seed_report(tmp_path)
    client = FakeClient({})  # every portal is down
    now = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    st = forms.collect_forms(client, tmp_path, [date(2026, 10, 19)], now=now)  # type: ignore[arg-type]
    assert st.missing == 1
    pend = lake.read_state(tmp_path, forms.PENDING_STATE)
    assert pend is not None and pend["attempts"].to_list() == [1]
    later = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    st = forms.collect_forms(client, tmp_path, [], now=later)  # type: ignore[arg-type]
    assert st.missing == 0


def test_silver_prefers_form_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_forms import STANDARD_PL
    from undercurrent.parse import build_silver_reports

    _seed_report(tmp_path)
    page = PARKIET_PAGE
    pdf = "https://i.gremicdn.pl/file/abc/powiadomienie.pdf"
    client = FakeClient(
        {
            "https://www.parkiet.com/sitemaps/sitemap.202610.xml": SITEMAP.encode(),
            page: f'<a href="{pdf}">PDF</a>'.encode(),
            pdf: b"%PDF-fake",
        }
    )
    monkeypatch.setattr(forms, "pdf_text", lambda content: STANDARD_PL)
    monkeypatch.setattr(portals.Bankier, "reach_days", -1)
    monkeypatch.setattr(portals.BiznesRadar, "reach_days", -1)
    now = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    forms.collect_forms(client, tmp_path, [date(2026, 10, 19)], now=now)  # type: ignore[arg-type]
    df = build_silver_reports(tmp_path, tmp_path / "work")
    assert df is not None
    r = df.row(0, named=True)
    assert r["insider_source"] == "form:parkiet" and r["insider_form_url"] == page
    assert (r["insider_person"], r["insider_role"], r["insider_side"]) == (
        "Tadeusz Nowak",
        "supervisory_board",
        "buy",
    )
    assert r["insider_volume"] == 452 and r["insider_trade_date"] == date(2026, 10, 15)
