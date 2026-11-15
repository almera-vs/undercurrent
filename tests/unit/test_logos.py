"""Stock icons: XTB's logo by exchange code, else the company's own site icon, kept clean."""

import base64
import io
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl
import pytest
from PIL import Image

from undercurrent import lake, logos, xtb
from undercurrent.fetch import PoliteClient
from undercurrent.publish import icon_html

# GPW's company info tab, trimmed to the fields read (structure as served on 2026-11-15)
GPW_CARD = """
<div><span>Nazwa:</span> <strong>ABCDEF</strong></div>
<div><span>Skrót:</span> <strong>ABC</strong></div>
<div><span>Strona www:</span>
  <a href="http://www.abc-example.pl" target="_blank"> www.abc-example.pl &nbsp;</a></div>
<div><span>E-mail:</span> <a href="mailto:biuro@abc-example.pl">biuro@abc-example.pl</a></div>
"""
GPW_CARD_NO_SITE = """
<div><span>Skrót:</span> <strong>XYZ</strong></div>
<div><span>Strona www:</span> </div><div><span>E-mail:</span> biuro@xyz.pl</div>
"""
# NewConnect's company card writes some links as "http://https://..."
NC_CARD = """
<script>var x = "Nazwa (NOPE)";</script>
<h2>Dane podstawowe</h2><dl><dt>Nazwa</dt><dd>EXAMPLE SPÓŁKA AKCYJNA (EXM)</dd>
<dt>Strona WWW</dt><dd><a href="http://https://www.example-nc.pl">https://www.example-nc.pl</a></dd></dl>
"""
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<rect width="40" height="40"/></svg>'
)


def _png(size: int, *, fmt: str = "PNG") -> bytes:
    img = Image.new("RGBA", (size, size), (200, 30, 30, 255))
    out = io.BytesIO()
    img.save(out, fmt, **({"sizes": [(size, size)]} if fmt == "ICO" else {}))
    return out.getvalue()


def test_website_is_normalised():
    assert logos.website("www.abc-example.pl") == "https://www.abc-example.pl/"
    assert logos.website("http://https://www.x.pl") == "https://www.x.pl/"
    assert logos.website("http://www.x.pl/o-nas") == "http://www.x.pl/"
    for junk in (None, "", "nan", "E-mail:", "biuro@x.pl", "brak"):
        assert logos.website(junk) is None


def test_cards_give_the_code_and_the_website():
    assert logos.parse_gpw_card(GPW_CARD) == logos.Card("ABC", "https://www.abc-example.pl/")
    assert logos.parse_gpw_card(GPW_CARD_NO_SITE) == logos.Card("XYZ", None)
    assert logos.parse_nc_card(NC_CARD) == logos.Card("EXM", "https://www.example-nc.pl/")


def test_unsafe_or_large_svg_is_refused():
    assert logos.clean_svg(SVG.encode()) == SVG.encode()
    bad = SVG.replace("<rect", '<rect onload="alert(1)"').encode()
    assert logos.clean_svg(bad) is None
    assert logos.clean_svg(SVG.replace("</svg>", "<script>x()</script></svg>").encode()) is None
    assert logos.clean_svg(b"\xff\xfe not text") is None
    huge = SVG.replace("</svg>", "<!--" + "x" * logos.MAX_SVG_BYTES + "--></svg>").encode()
    assert logos.clean_svg(huge) is None


def test_embedded_bitmap_is_reencoded_to_fit():
    # a 300 px bitmap with a big metadata chunk, as XTB embeds some logos
    img = Image.new("RGBA", (300, 100), (0, 120, 200, 255))
    out = io.BytesIO()
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_itxt("XML:com.adobe.xmp", "x" * 200_000)
    img.save(out, "PNG", pnginfo=meta)
    b64 = base64.b64encode(out.getvalue()).decode()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,{b64}"/></svg>'
    )
    cleaned = logos.clean_svg(svg.encode())
    assert cleaned is not None and len(cleaned) < logos.MAX_SVG_BYTES
    assert b"data:image/png;base64," in cleaned


def test_bitmaps_become_square_pngs_and_tiny_ones_are_dropped():
    png = logos.raster_png(_png(180))
    assert png is not None
    assert Image.open(io.BytesIO(png)).size == (logos.ICON_PX, logos.ICON_PX)
    ico = logos.raster_png(_png(48, fmt="ICO"))
    assert ico is not None and Image.open(io.BytesIO(ico)).size == (64, 64)
    assert logos.raster_png(_png(16)) is None
    assert logos.raster_png(b"not an image") is None
    assert logos.normalise(SVG.encode(), "image/svg+xml", "https://x.pl/i.svg") == (
        SVG.encode(),
        "svg",
    )


def test_icon_links_prefer_touch_icons_then_the_largest():
    html = """<head>
      <link rel="icon" href="/fav-16.png" sizes="16x16">
      <link rel="mask-icon" href="/mask.svg">
      <link rel="icon" href="/fav-96.png" sizes="96x96">
      <link rel="apple-touch-icon" href="https://cdn.x.pl/touch.png">
      <link rel="stylesheet" href="/s.css">
    </head>"""
    assert logos.icon_links(html, "https://www.x.pl/") == [
        "https://cdn.x.pl/touch.png",
        "https://www.x.pl/fav-96.png",
        "https://www.x.pl/fav-16.png",
    ]


def _client(tmp_path, seen: list[str]) -> PoliteClient:
    touch = _png(180)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.url.host}{req.url.path}")
        if req.url.host == "logos.xtb.com":
            if req.url.path == "/abc_pl.svg":
                return httpx.Response(200, text=SVG, headers={"content-type": "image/svg+xml"})
            return httpx.Response(403, text="<Error>AccessDenied</Error>")
        if req.url.host == "www.gpw.pl":
            isin = req.url.params["gls_isin"]
            card = GPW_CARD if isin == "PLABC0000001" else GPW_CARD_NO_SITE
            return httpx.Response(200, text=card)
        if req.url.host == "newconnect.pl":
            return httpx.Response(200, text=NC_CARD)
        if req.url.host == "www.example-nc.pl":
            if req.url.path == "/":
                return httpx.Response(200, text='<link rel="apple-touch-icon" href="/t.png">')
            if req.url.path == "/t.png":
                return httpx.Response(200, content=touch, headers={"content-type": "image/png"})
        return httpx.Response(404)

    return PoliteClient(
        tmp_path / "cache", min_interval_s=0, max_jitter_s=0, transport=httpx.MockTransport(handler)
    )


def test_refresh_tries_xtb_then_the_website_and_keeps_files(tmp_path):
    seen: list[str] = []
    stocks = [
        logos.Stock("PLABC0000001", "GPW"),
        logos.Stock("PLEXM0000001", "NC", "EXM"),
        logos.Stock("PLXYZ0000001", "GPW"),
    ]
    now = datetime(2026, 11, 15, tzinfo=UTC)
    with _client(tmp_path, seen) as c:
        checked, found = logos.refresh(c, tmp_path, stocks, now=now)
    assert (checked, found) == (3, 2)
    files = logos.read_logos(tmp_path)
    assert files["PLABC0000001"].name == "PLABC0000001.svg"
    assert files["PLEXM0000001"].name == "PLEXM0000001.png"
    assert "PLXYZ0000001" not in files
    state = lake.read_state(tmp_path, logos.STATE)
    assert state is not None
    rows = {r["isin"]: r for r in state.iter_rows(named=True)}
    assert rows["PLABC0000001"]["source"] == "xtb" and rows["PLABC0000001"]["code"] == "ABC"
    assert rows["PLEXM0000001"]["source"] == "site"
    assert rows["PLEXM0000001"]["source_url"] == "https://www.example-nc.pl/t.png"
    assert rows["PLXYZ0000001"]["file"] is None and rows["PLXYZ0000001"]["code"] == "XYZ"
    # the GPW stock with XTB's logo never touched its website
    assert not any(s.startswith("www.abc-example.pl") for s in seen)

    # nothing is due a week later; the stock without an icon is due after a month
    with _client(tmp_path, []) as c:
        assert logos.refresh(c, tmp_path, stocks, now=now + timedelta(days=7))[0] == 0
        assert logos.refresh(c, tmp_path, stocks, now=now + timedelta(days=31))[0] == 1


def test_a_verified_xtb_symbol_is_tried_first(tmp_path):
    lake.write_state(
        tmp_path,
        xtb.STATE,
        pl.DataFrame(
            [
                {
                    "isin": "PLXYZ0000001",
                    "url": "https://www.xtb.com/pl/akcje/xyz",
                    "symbol": "ABC.PL",
                    "logo": None,
                    "checked_at": datetime(2026, 11, 1, tzinfo=UTC),
                }
            ],
            schema=xtb.SCHEMA,
        ),
    )
    with _client(tmp_path, []) as c:
        logos.refresh(c, tmp_path, [logos.Stock("PLXYZ0000001", "GPW")])
    assert logos.read_logos(tmp_path)["PLXYZ0000001"].suffix == ".svg"


def test_listed_stocks_come_from_recent_price_files(tmp_path):
    def part(table: str, day: date, rows: list[dict]) -> None:
        d = tmp_path / "bronze" / table / f"date={day}"
        d.mkdir(parents=True)
        pl.DataFrame([{**r, "trade_date": day} for r in rows]).write_parquet(d / "part-0.parquet")

    part("prices_gpw", date(2026, 10, 1), [{"isin": "PLOLD0000001", "name": "OLD"}])
    part("prices_gpw", date(2026, 11, 12), [{"isin": "PLABC0000001", "name": "ABCDEF"}])
    part("prices_nc", date(2026, 11, 12), [{"isin": "PLEXM0000001", "ticker": "EXM"}])
    assert logos.listed(tmp_path) == [
        logos.Stock("PLABC0000001", "GPW", None),
        logos.Stock("PLEXM0000001", "NC", "EXM"),
    ]


@pytest.mark.parametrize(
    ("file", "expect"),
    [
        ("PLABC0000001.svg", 'src="../logos/PLABC0000001.svg"'),
        ("PLABC0000001.png", 'class="tlogo tlogo-png"'),
        (None, 'class="tlogo tmono"'),
    ],
)
def test_icon_markup(file, expect):
    html = str(icon_html(file, "AB<C", "../"))
    assert expect in html
    assert "<C" not in html


def test_a_white_mark_gets_a_dark_tile():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(20, 44):
        for y in range(20, 44):
            img.putpixel((x, y), (255, 255, 255, 255))
    out = io.BytesIO()
    img.save(out, "PNG")
    png = logos.raster_png(out.getvalue())
    assert png is not None
    corner = Image.open(io.BytesIO(png)).convert("RGBA").getpixel((0, 0))
    assert corner == logos.DARK_TILE
    # a coloured mark keeps its transparency
    red = logos.raster_png(_png(64))
    assert red is not None
    assert Image.open(io.BytesIO(red)).convert("RGBA").getpixel((0, 0))[3] == 255
