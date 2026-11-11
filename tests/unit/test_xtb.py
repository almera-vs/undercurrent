"""XTB links are kept only when XTB's own page shows the stock's ISIN."""

from datetime import UTC, datetime, timedelta

import httpx

from undercurrent import xtb
from undercurrent.fetch import PoliteClient

INDEX = (
    "<sitemapindex><sitemap><loc>https://www.xtb.com/pl/sitemap/instruments/cashstocks/1.xml"
    "</loc></sitemap></sitemapindex>"
)
STOCKS = (
    "<urlset>"
    + "".join(
        f"<url><loc>https://www.xtb.com/pl/akcje/{s}</loc></url>"
        for s in ("hydrotor", "s2b-pl", "abt-uk", "lookalike")
    )
    + "</urlset>"
)
PAGES = {
    "hydrotor": "<h1>HDR.PL</h1><dd>PLHDRTR00013</dd>",
    "s2b-pl": "<h1>S2B.PL</h1><dd>PLSNBIO00013</dd>",
    "lookalike": "<h1>LKL.PL</h1><dd>PLOTHER00019</dd>",
}


def _client(tmp_path, seen: list[str]) -> PoliteClient:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("cashstocks.xml"):
            return httpx.Response(200, text=INDEX)
        if req.url.path.endswith("1.xml"):
            return httpx.Response(200, text=STOCKS)
        slug = req.url.path.removeprefix("/pl/akcje/")
        if slug in PAGES:
            return httpx.Response(200, text=PAGES[slug])
        return httpx.Response(301, headers={"location": "https://www.xtb.com/pl/akcje"})

    return PoliteClient(
        tmp_path / "cache",
        min_interval_s=0,
        max_jitter_s=0,
        transport=httpx.MockTransport(handler),
    )


def test_slugs_from_names_and_ticker():
    c = xtb.Company("X", ("CD PROJEKT S.A.", "Przedsiębiorstwo Łódzkie SPÓŁKA AKCYJNA"), "CDR")
    assert xtb.candidates(c) == ["cd-projekt", "przedsiebiorstwo-lodzkie", "cdr-pl"]


def test_only_pages_showing_the_isin_are_linked(tmp_path):
    seen: list[str] = []
    companies = [
        xtb.Company("PLHDRTR00013", ("HYDROTOR",)),
        xtb.Company("PLSNBIO00013", ("SYN2BIO",), "S2B"),
        xtb.Company("PLLOOKA00011", ("LOOKALIKE",)),  # page exists, but for another ISIN
        xtb.Company("PLNOPAG00017", ("NOWHERE",)),  # not in the sitemap: never requested
    ]
    with _client(tmp_path, seen) as c:
        assert xtb.refresh(c, tmp_path, companies) == 4
    assert xtb.read_links(tmp_path) == {
        "PLHDRTR00013": "https://www.xtb.com/pl/akcje/hydrotor",
        "PLSNBIO00013": "https://www.xtb.com/pl/akcje/s2b-pl",
    }
    assert "/pl/akcje/nowhere" not in seen


def test_checked_stocks_wait_until_stale(tmp_path):
    now = datetime(2026, 11, 11, tzinfo=UTC)
    co = [xtb.Company("PLHDRTR00013", ("HYDROTOR",))]
    seen: list[str] = []
    with _client(tmp_path, seen) as c:
        assert xtb.refresh(c, tmp_path, co, now=now) == 1
        assert xtb.refresh(c, tmp_path, co, now=now + timedelta(days=5)) == 0
        assert xtb.refresh(c, tmp_path, co, now=now + timedelta(days=31)) == 1
