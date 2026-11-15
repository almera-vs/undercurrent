"""Stock icons, fetched politely and served from the site itself.

For each listed stock, in this order:

1. **XTB's logo** for the stock's exchange code (``logos.xtb.com/<code>_pl.svg``, 403 when
   XTB has none). The code comes from XTB's own page where ``xtb.py`` verified one, else
   from the exchange: GPW's company card ("Skrót") or the NewConnect card.
2. **The company's own website icon**: the site named on the exchange's company card, its
   ``apple-touch-icon`` or largest ``icon`` link, else ``/favicon.ico``. Icons smaller than
   ``MIN_PX`` are not used.

Files are normalised before they are kept: an SVG without scripts and at most
``MAX_SVG_BYTES`` (embedded bitmaps are re-encoded smaller first), or a ``ICON_PX`` square
PNG. They live in ``state/logos/<ISIN>.<svg|png>`` beside ``state/logos.parquet`` and are
copied into the site, so no page loads anything from another server. A logo is the
company's trademark, shown only to identify the stock; Undercurrent is not affiliated with
XTB or any company.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import polars as pl

from undercurrent import lake, xtb
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)

GPW_INFO_URL = (
    "https://www.gpw.pl/ajaxindex.php?start=infoTab&format=html&action=GPWListaSp"
    "&gls_isin={isin}&lang=PL"
)
NC_CARD_URL = "https://newconnect.pl/spolka?isin={isin}"
STATE = "logos"
RECHECK_DAYS = 90  # a stock with a logo
MISSING_RECHECK_DAYS = 30  # a stock without one
CARD_MAX_AGE_S = 30 * 86400
MAX_CHECKS = 120
MAX_SVG_BYTES = 60_000
MAX_DOWNLOAD_BYTES = 3_000_000
ICON_PX = 64
MIN_PX = 32
EMBEDDED_PX = 128
LIGHT = 215  # luminance above which a mark counts as white
DARK_TILE = (43, 47, 54, 255)
LISTED_DAYS = 14
SITE_TIMEOUT_S = 15.0  # company websites: a short timeout and no retries

SCHEMA: dict[str, pl.DataType] = {
    "isin": pl.String(),
    "market": pl.String(),
    "code": pl.String(),  # the exchange code, e.g. 11B
    "website": pl.String(),
    "source": pl.String(),  # xtb | site | None
    "source_url": pl.String(),
    "file": pl.String(),  # e.g. PL11BTS00015.svg, under state/logos/
    "sha256": pl.String(),
    "checked_at": pl.Datetime("us", "UTC"),
}

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_GPW_CODE = re.compile(r"Skrót:\s*([A-Z0-9]{2,6})\b")
_NC_CODE = re.compile(r"Nazwa\s+.{1,160}?\(([A-Z0-9]{2,6})\)")
_SITE_AFTER = re.compile(r"Strona\s+www:?\s*(\S+)", re.IGNORECASE)
_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_EMBEDDED = re.compile(r"data:image/(?:png|jpe?g|webp|gif);base64,([A-Za-z0-9+/=\s]+)")
_UNSAFE_SVG = re.compile(
    r"<script|javascript:|<foreignObject|\son[a-z]+\s*=|<iframe|<embed|<object", re.IGNORECASE
)


@dataclass(frozen=True)
class Stock:
    """A listed stock to find an icon for."""

    isin: str
    market: str  # GPW | NC
    code: str | None = None  # the exchange code when already known (NewConnect files carry it)


@dataclass(frozen=True)
class Card:
    """What the exchange's company card says: the code and the company's website."""

    code: str | None
    website: str | None


@dataclass(frozen=True)
class Icon:
    """A normalised icon ready to be written."""

    data: bytes
    ext: str  # svg | png
    source: str
    url: str


# ------------------------------------------------------------------ the exchange's card


def _text(html: str) -> str:
    return _SPACE.sub(" ", unescape(_TAGS.sub(" ", html))).strip()


def website(raw: str | None) -> str | None:
    """A company's site as an ``https://host/`` URL, or None for junk (``nan``, e-mail)."""
    if not raw:
        return None
    s = raw.strip().strip(".,;").replace("\u00a0", "")
    # the NewConnect card writes some links as "http://https://..."
    m = list(re.finditer(r"https?://", s, re.IGNORECASE))
    scheme = "https"
    if m:
        scheme = m[-1].group(0).rstrip(":/").lower()
        s = s[m[-1].end() :]
    host = s.split("/")[0].split("?")[0].lower().rstrip(".")
    if "@" in host or not _HOST.match(host) or host in {"nan", "brak"}:
        return None
    return f"{scheme}://{host}/"


def parse_gpw_card(html: str) -> Card:
    """GPW's company info tab: ``Skrót: 11B`` … ``Strona www: www.11bitstudios.pl``."""
    text = _text(html)
    code = _GPW_CODE.search(text)
    site = _SITE_AFTER.search(text)
    return Card(code.group(1) if code else None, website(site.group(1)) if site else None)


def parse_nc_card(html: str) -> Card:
    """NewConnect's company card: ``Nazwa MILISYSTEM SPÓŁKA AKCYJNA (MLM)`` … ``Strona WWW``."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = _text(body)
    code = _NC_CODE.search(text)
    site = _SITE_AFTER.search(text)
    return Card(code.group(1) if code else None, website(site.group(1)) if site else None)


def card(client: PoliteClient, stock: Stock) -> Card:
    """The exchange's company card for ``stock`` (cached for a month)."""
    if stock.market == "NC":
        res = client.get(NC_CARD_URL.format(isin=stock.isin), max_age_s=CARD_MAX_AGE_S)
        return parse_nc_card(res.text())
    res = client.get(GPW_INFO_URL.format(isin=stock.isin), max_age_s=CARD_MAX_AGE_S)
    return parse_gpw_card(res.text())


# ------------------------------------------------------------------ normalising files


def _reencode(b64: str) -> str | None:
    """An embedded bitmap re-encoded as a small PNG, as base64 (None if unreadable)."""
    from PIL import Image

    try:
        src = Image.open(io.BytesIO(base64.b64decode(re.sub(r"\s+", "", b64))))
        src.load()
    except Exception:
        return None
    img = src.convert("RGBA")
    img.thumbnail((EMBEDDED_PX, EMBEDDED_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def clean_svg(data: bytes) -> bytes | None:
    """``data`` as a safe, small SVG, or None.

    Scripts, event handlers and foreign objects are refused outright. Embedded bitmaps
    (XTB embeds some logos as a PNG with kilobytes of metadata) are re-encoded first.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "<svg" not in text[:4000] or _UNSAFE_SVG.search(text):
        return None
    if len(data) > MAX_SVG_BYTES:

        def sub(m: re.Match[str]) -> str:
            small = _reencode(m.group(1))
            return f"data:image/png;base64,{small}" if small else m.group(0)

        text = _EMBEDDED.sub(sub, text)
    out = text.encode("utf-8")
    return out if len(out) <= MAX_SVG_BYTES else None


def _light_only(img: Any) -> bool:
    """True when every visible pixel of ``img`` (RGBA) is near white."""
    alpha = img.getchannel("A")
    seen = alpha.point(lambda a: 255 if a >= 128 else 0)
    if seen.getbbox() is None:
        return False
    dark = img.convert("L").point(lambda v: 255 if v < LIGHT else 0)
    return dark.getbbox() is None or not _overlap(dark, seen)


def _overlap(a: Any, b: Any) -> bool:
    from PIL import ImageChops

    return ImageChops.multiply(a, b).getbbox() is not None


def raster_png(data: bytes) -> bytes | None:
    """A bitmap or .ico as an ``ICON_PX`` square PNG on transparency, None if too small."""
    from PIL import Image

    try:
        src = Image.open(io.BytesIO(data))  # an .ico opens at its largest size
        src.load()
    except Exception:
        return None
    if min(src.size) < MIN_PX:
        return None
    img = src.convert("RGBA")
    if img.getchannel("A").getbbox() is None:  # fully transparent
        return None
    img.thumbnail((ICON_PX, ICON_PX), Image.Resampling.LANCZOS)
    # a light mark on transparency (made for dark headers) would vanish on the light tile
    back = DARK_TILE if _light_only(img) else (0, 0, 0, 0)
    square = Image.new("RGBA", (ICON_PX, ICON_PX), back)
    square.alpha_composite(img, ((ICON_PX - img.width) // 2, (ICON_PX - img.height) // 2))
    out = io.BytesIO()
    square.save(out, "PNG", optimize=True)
    return out.getvalue()


def normalise(data: bytes, content_type: str, url: str) -> tuple[bytes, str] | None:
    """``(file bytes, extension)`` for an icon download, or None if it is not usable."""
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    head = data[:400].lstrip().lower()
    is_svg = "svg" in content_type or urlparse(url).path.lower().endswith(".svg")
    if is_svg or head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4000]):
        svg = clean_svg(data)
        return (svg, "svg") if svg else None
    png = raster_png(data)
    return (png, "png") if png else None


# ------------------------------------------------------------------ sources


def _download(client: PoliteClient, url: str) -> tuple[bytes, str, str] | None:
    try:
        res = client.get(url, max_age_s=MISSING_RECHECK_DAYS * 86400)
    except Exception as exc:  # 403/404, blocked, timeouts: no icon here
        log.debug("logo %s: %s", url, exc)
        return None
    if res.status != 200:
        return None
    return res.content, res.content_type, res.url


def from_xtb(client: PoliteClient, code: str | None) -> Icon | None:
    """XTB's logo for the exchange code ``code``."""
    if not code:
        return None
    url = xtb.LOGO_URL.format(code=code.lower())
    got = _download(client, url)
    if not got or "svg" not in got[1]:
        return None
    svg = clean_svg(got[0])
    return Icon(svg, "svg", "xtb", url) if svg else None


def icon_links(html: str, base: str) -> list[str]:
    """Icon URLs a home page declares, best first: touch icons, then the largest ``icon``."""
    from selectolax.lexbor import LexborHTMLParser as HTMLParser

    found: list[tuple[int, str]] = []
    for node in HTMLParser(html).css("link[rel][href]"):
        rel = (node.attributes.get("rel") or "").lower().split()
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith("data:") or "mask-icon" in rel:
            continue
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            rank = 1000
        elif "icon" in rel:
            sizes = node.attributes.get("sizes") or ""
            px = [int(a) for a in re.findall(r"(\d+)x\d+", sizes)]
            svg = "svg" in (node.attributes.get("type") or "") or href.lower().endswith(".svg")
            rank = 900 if svg or "any" in sizes else (max(px) if px else 16)
        else:
            continue
        found.append((rank, urljoin(base, href)))
    found.sort(key=lambda r: -r[0])
    out = [u for _, u in found if urlparse(u).scheme in ("http", "https")]
    return list(dict.fromkeys(out))


def from_site(client: PoliteClient, site: str | None, max_tries: int = 3) -> Icon | None:
    """The company website's own icon."""
    if not site:
        return None
    try:
        res = client.get(site, max_age_s=MISSING_RECHECK_DAYS * 86400)
        home, base = res.text(), res.url
    except Exception as exc:
        log.debug("site %s: %s", site, exc)
        home, base = "", site
    tries = [*icon_links(home, base)[:max_tries], urljoin(base, "/favicon.ico")]
    for url in dict.fromkeys(tries):
        got = _download(client, url)
        if not got:
            continue
        norm = normalise(*got)
        if norm:
            return Icon(norm[0], norm[1], "site", url)
    return None


# ------------------------------------------------------------------ state


def logo_dir(data_dir: Path) -> Path:
    """Where the files live: ``state/logos/``."""
    return data_dir / "state" / STATE


def read_logos(data_dir: Path) -> dict[str, Path]:
    """ISIN → its icon file, for the site."""
    df = lake.read_state(data_dir, STATE)
    if df is None:
        return {}
    root = logo_dir(data_dir)
    out: dict[str, Path] = {}
    for r in df.iter_rows(named=True):
        if r["file"] and (root / r["file"]).exists():
            out[r["isin"]] = root / r["file"]
    return out


def _due(row: dict[str, Any] | None, now: datetime) -> bool:
    if row is None:
        return True
    days = RECHECK_DAYS if row["file"] else MISSING_RECHECK_DAYS
    return bool(row["checked_at"] < now - timedelta(days=days))


def site_client(cache_dir: Path | None = None) -> PoliteClient:
    """A client for company websites: as polite, but quick to give up on a slow site."""
    if cache_dir is None:
        return PoliteClient(timeout_s=SITE_TIMEOUT_S, retry_pauses_s=())
    return PoliteClient(cache_dir, timeout_s=SITE_TIMEOUT_S, retry_pauses_s=())


def find(
    client: PoliteClient,
    stock: Stock,
    xtb_symbol: str | None,
    sites: PoliteClient | None = None,
) -> tuple[Card, Icon | None]:
    """The card and the best icon for ``stock`` (see the module's docstring).

    ``sites`` fetches the company's website (``site_client``); ``client`` by default.
    """
    try:
        c = card(client, stock)
    except Exception as exc:
        log.debug("card %s: %s", stock.isin, exc)
        c = Card(None, None)
    codes = [xtb_symbol.removesuffix(".PL") if xtb_symbol else None, stock.code, c.code]
    for code in dict.fromkeys(x for x in codes if x):
        icon = from_xtb(client, code)
        if icon:
            return Card(c.code or stock.code, c.website), icon
    return Card(c.code or stock.code, c.website), from_site(sites or client, c.website)


def refresh(
    client: PoliteClient,
    data_dir: Path,
    stocks: Iterable[Stock],
    *,
    now: datetime | None = None,
    max_checks: int = MAX_CHECKS,
    sites: PoliteClient | None = None,
) -> tuple[int, int]:
    """Look for icons of the stocks that have none yet or are due again.

    Returns ``(checked, with a logo)``. The state is written after every stock, so a run
    that is stopped keeps what it found.
    """
    now = now or datetime.now(UTC)
    old = lake.read_state(data_dir, STATE)
    rows: dict[str, dict[str, Any]] = (
        {r["isin"]: r for r in old.iter_rows(named=True)} if old is not None else {}
    )
    xtb_rows = lake.read_state(data_dir, xtb.STATE)
    symbols = (
        {r["isin"]: r.get("symbol") for r in xtb_rows.iter_rows(named=True)}
        if xtb_rows is not None
        else {}
    )
    due = [s for s in {s.isin: s for s in stocks}.values() if _due(rows.get(s.isin), now)]
    due.sort(key=lambda s: (s.isin in rows, s.isin))  # never checked first
    root = logo_dir(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    checked = 0
    for stock in due[:max_checks]:
        c, icon = find(client, stock, symbols.get(stock.isin), sites)
        prev = rows.get(stock.isin) or {}
        row = {
            "isin": stock.isin,
            "market": stock.market,
            "code": c.code,
            "website": c.website,
            "source": prev.get("source"),
            "source_url": prev.get("source_url"),
            "file": prev.get("file"),
            "sha256": prev.get("sha256"),
            "checked_at": now,
        }
        if icon:
            name = f"{stock.isin}.{icon.ext}"
            if prev.get("file") and prev["file"] != name:
                (root / prev["file"]).unlink(missing_ok=True)
            (root / name).write_bytes(icon.data)
            row |= {
                "source": icon.source,
                "source_url": icon.url,
                "file": name,
                "sha256": hashlib.sha256(icon.data).hexdigest(),
            }
        rows[stock.isin] = row  # a failed recheck keeps the file it had
        checked += 1
        if checked % 20 == 0:
            _write(data_dir, rows)
    if checked:
        _write(data_dir, rows)
    found = sum(1 for r in rows.values() if r["file"])
    log.info("logos: checked %d, %d of %d stocks have one", checked, found, len(rows))
    return checked, found


def _write(data_dir: Path, rows: dict[str, dict[str, Any]]) -> None:
    df = pl.DataFrame([{k: r.get(k) for k in SCHEMA} for r in rows.values()], schema=SCHEMA)
    lake.write_state(data_dir, STATE, df.sort("isin"))


def listed(data_dir: Path, day: date | None = None) -> list[Stock]:
    """Stocks with a price in the last ``LISTED_DAYS`` days of the lake (GPW and NewConnect)."""
    out: dict[str, Stock] = {}
    for table, market in (("prices_gpw", "GPW"), ("prices_nc", "NC")):
        base = data_dir / "bronze" / table
        days = sorted(p.name[5:] for p in base.glob("date=*") if p.is_dir())
        if not days:
            continue
        last = date.fromisoformat(days[-1]) if day is None else day
        since = last - timedelta(days=LISTED_DAYS)
        frames = []
        for d in days:
            if since <= date.fromisoformat(d) <= last:
                parts = sorted((base / f"date={d}").glob("*.parquet"))
                frames += [pl.read_parquet(p) for p in parts]
        if not frames:
            continue
        df = pl.concat(frames, how="diagonal_relaxed")
        if "ticker" not in df.columns:
            df = df.with_columns(pl.lit(None, pl.String()).alias("ticker"))
        df = df.filter(pl.col("isin").is_not_null()).sort("trade_date")
        for r in df.group_by("isin", maintain_order=True).last().iter_rows(named=True):
            out.setdefault(r["isin"], Stock(r["isin"], market, r["ticker"] or None))
    return sorted(out.values(), key=lambda s: s.isin)
