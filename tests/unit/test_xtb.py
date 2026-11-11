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
    "hydrotor": "<title>HDR.PL</title><dd>PLHDRTR00013</dd>",
    "s2b-pl": "<title>Syn2bio – akcje, notowania i kurs S2B.PL</title><dd>PLSNBIO00013</dd>",
    "lookalike": "<title>LKL.PL</title><dd>PLOTHER00019</dd>",
}
LOGOS = {"/hdr_pl.svg": '<svg xmlns="http://www.w3.org/2000/svg"></svg>'}


def _client(tmp_path, seen: list[str]) -> PoliteClient:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.host == "logos.xtb.com":
            if req.url.path in LOGOS:
                svg = LOGOS[req.url.path]
                return httpx.Response(200, text=svg, headers={"content-type": "image/svg+xml"})
            return httpx.Response(403, text="<Error>AccessDenied</Error>")
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
    assert xtb.candidates(c) == [
        "cd-projekt",
        "przedsiebiorstwo-lodzkie",
        "cdr-pl",
        "przedsiebiorstwo",
    ]
    assert xtb.match(["pkobp", "nope", "santander"], {"pko-bp", "santander", "pkoh-us"}) == [
        "pko-bp",
        "santander",
    ]


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
    # XTB's logo only where it serves one; S2B has none (403), so no logo
    assert xtb.read_logos(tmp_path) == {"PLHDRTR00013": "https://logos.xtb.com/hdr_pl.svg"}


def test_checked_stocks_wait_until_stale(tmp_path):
    now = datetime(2026, 11, 11, tzinfo=UTC)
    co = [xtb.Company("PLHDRTR00013", ("HYDROTOR",))]
    seen: list[str] = []
    with _client(tmp_path, seen) as c:
        assert xtb.refresh(c, tmp_path, co, now=now) == 1
        assert xtb.refresh(c, tmp_path, co, now=now + timedelta(days=5)) == 0
        assert xtb.refresh(c, tmp_path, co, now=now + timedelta(days=31)) == 1


def test_symbol_is_read_from_the_page_title(tmp_path):
    with _client(tmp_path, []) as c:
        page = xtb.verify(c, "s2b-pl", "PLSNBIO00013")
    assert page is not None and page.symbol == "S2B.PL"


def test_old_state_without_symbols_is_rechecked(tmp_path):
    import polars as pl

    from undercurrent import lake

    old = pl.DataFrame(
        {
            "isin": ["PLHDRTR00013"],
            "url": ["https://www.xtb.com/pl/akcje/hydrotor"],
            "checked_at": [datetime(2026, 11, 11, tzinfo=UTC)],
        }
    )
    lake.write_state(tmp_path, xtb.STATE, old)
    with _client(tmp_path, []) as c:
        n = xtb.refresh(
            c,
            tmp_path,
            [xtb.Company("PLHDRTR00013", ("HYDROTOR",))],
            now=datetime(2026, 11, 12, tzinfo=UTC),
        )
    assert n == 1 and xtb.read_logos(tmp_path)
