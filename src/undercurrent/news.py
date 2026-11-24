"""Headlines that may explain a big move: three public sources, listing pages only.

Only headline, time, outlet and link are kept, never article text. Each source gives a
per-company or per-query listing with timestamps:

* BiznesRadar ``/wiadomosci/{SYMBOL},{page}``: absolute ``YYYY-MM-DD HH:MM:SS`` times.
* Bankier ``/gielda/notowania/akcje/{SYMBOL}/wiadomosci/{page}``: ``YYYY-MM-DD HH:MM`` times.
* Google News RSS ``/rss/search?q=...``: RFC 822 ``pubDate`` in GMT; some items carry no
  time of day and are marked ``precision="day"``.

The parsers are tested on trimmed copies of real listing pages (``tests/fixtures/news``,
fetched 2026-11-24), and ``tests/network`` checks the live pages from the probe workflow.
Each parser counts what it sees, so a markup change shows up as ``Outcome.DRIFT`` instead
of silently returning nothing.

Politeness: the caller decides the pacing (the news fetcher runs with no fixed interval,
by the user's decision). This module handles HTTP 429/503 by waiting for ``Retry-After``
(or 30 s) and halving the worker count for that host; it never fetches around a block page.
"""

from __future__ import annotations

import email.utils
import html as htmllib
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from enum import StrEnum
from typing import Literal
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from undercurrent.classify import normalize
from undercurrent.fetch import FetchResult, PoliteClient, SourceBlockedError

WARSAW = ZoneInfo("Europe/Warsaw")
Precision = Literal["minute", "day"]

BIZNESRADAR = "biznesradar"
BANKIER = "bankier"
GOOGLE = "google"

BIZNESRADAR_URL = "https://www.biznesradar.pl/wiadomosci/{symbol},{page}"
BANKIER_URL = "https://www.bankier.pl/gielda/notowania/akcje/{symbol}/wiadomosci/{page}"
GOOGLE_URL = (
    "https://news.google.com/rss/search?q={query}+after:{after}+before:{before}&hl=pl&gl=PL"
)

RETRY_AFTER_DEFAULT_S = 30.0
MAX_ATTEMPTS = 3
# A listing page this large with no recognisable item markup is treated as drift, not as "no news".
DRIFT_MIN_BYTES = 5_000


class Outcome(StrEnum):
    """How a fetch ended: what the caller can rely on, and what it must not treat as "no news"."""

    OK = "ok"  # parsed at least one item
    EMPTY = "empty"  # page parsed fine and has no items (or none in range)
    BLOCKED = "blocked"  # bot-protection page; not retried
    RATE_LIMITED = "rate_limited"  # 429/503 after all attempts
    HTTP_ERROR = "http_error"  # other 4xx/5xx
    UNREACHABLE = "unreachable"  # transport failure after the client's own retries
    DRIFT = "drift"  # large page, no item markup found: the parser needs updating


@dataclass(frozen=True)
class NewsItem:
    """One headline from one source, as the source published it."""

    isin: str
    source: str
    outlet: str
    published_at: datetime  # UTC, timezone-aware
    precision: Precision  # "minute" or "day"
    title: str
    url: str
    fetched_at: datetime
    outlet_url: str = ""  # the outlet's site; Google's ``url`` is a news.google.com redirect


@dataclass(frozen=True)
class Company:
    """What is known about a stock, to match headlines against."""

    isin: str
    names: tuple[str, ...]  # issuer names without legal form, e.g. "XTB", "Grupa Azoty"
    ticker: str | None = None  # the trading symbol, e.g. "XTB"
    own_domains: tuple[str, ...] = ()  # the company's own sites, e.g. ("xtb.com",)
    # words that put the name in a headline without being company news, e.g. a sponsored
    # event: ("ksw",) for XTB, whose name is on the KSW MMA galas
    noise_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """Whether a stored headline may count as an explanation for a company's move."""

    counts: bool
    reason: str  # "match", or why it was excluded or not matched


@dataclass
class PageResult:
    """One fetch: its outcome, the items kept, and a short reason for the log."""

    outcome: Outcome
    items: list[NewsItem] = field(default_factory=list)
    next_url: str | None = None
    detail: str = ""


# ---- text helpers -----------------------------------------------------------------------

_BLOCK_TAGS = re.compile(r"</?(?:p|div|li|br|h[1-6]|tr|td|span|a)\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)


def strip_tags(fragment: str) -> list[str]:
    """Visible lines of an HTML fragment, one per block element."""
    t = _SCRIPT.sub("", fragment)
    t = _BLOCK_TAGS.sub("\n", t)
    t = _TAGS.sub("", t)
    lines = (htmllib.unescape(line).strip() for line in t.split("\n"))
    return [line for line in lines if line]


_BR_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?::(\d{2}))?")


def _warsaw(day: str, hm: str, sec: str | None) -> datetime:
    y, mo, d = (int(x) for x in day.split("-"))
    h, mi = (int(x) for x in hm.split(":"))
    local = datetime.combine(datetime(y, mo, d).date(), dtime(h, mi, int(sec or 0)), tzinfo=WARSAW)
    return local.astimezone(UTC)


def _retry_after(value: str | None) -> float:
    if not value:
        return RETRY_AFTER_DEFAULT_S
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return RETRY_AFTER_DEFAULT_S
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


# ---- parsers ----------------------------------------------------------------------------


_BR_RECORD = re.compile(r'<div id="msg-(\d+)" class="record record-type-([A-Za-z_]+)"')
_BR_HEADER = re.compile(r'class="record-header">\s*<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BR_DATE = re.compile(r'class="record-date">\s*(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?::(\d{2}))?')
_BR_AUTHOR = re.compile(r'class="record-author"[^>]*>(.*?)</a>', re.S)
_BR_AVATAR = re.compile(r'class="record-avatar">\s*<img[^>]*\balt="([^"]*)"')


def parse_biznesradar(html: str, *, isin: str, fetched_at: datetime, base: str) -> PageResult:
    """BiznesRadar listing: one ``record`` block per item.

    ``record-type-NEWS`` items link out to the outlet and name it in ``record-author`` (the
    logo's alt text is the fallback). ``record-type-POST`` items are BiznesRadar's own pages,
    such as copies of ESPI reports, and carry no author.
    """
    items: list[NewsItem] = []
    seen: set[str] = set()
    starts = list(_BR_RECORD.finditer(html))
    for k, rec in enumerate(starts):
        stop = starts[k + 1].start() if k + 1 < len(starts) else rec.start() + 6000
        block = html[rec.start() : stop]
        head, date = _BR_HEADER.search(block), _BR_DATE.search(block)
        if head is None or date is None:
            continue
        title = " ".join(strip_tags(head.group(2)))
        url = urljoin(base, htmllib.unescape(head.group(1)))
        if not title or url in seen:
            continue
        seen.add(url)
        author, avatar = _BR_AUTHOR.search(block), _BR_AVATAR.search(block)
        outlet = (
            " ".join(strip_tags(author.group(1)))
            if author
            else htmllib.unescape(avatar.group(1)).strip()
            if avatar and avatar.group(1).strip()
            else "BiznesRadar"
        )
        items.append(
            NewsItem(
                isin=isin,
                source=BIZNESRADAR,
                outlet=outlet,
                published_at=_warsaw(date.group(1), date.group(2), date.group(3)),
                precision="minute",
                title=title,
                url=url,
                fetched_at=fetched_at,
            )
        )
    return _classify_page(items, html)


def parse_bankier(html: str, *, isin: str, fetched_at: datetime, base: str) -> PageResult:
    """Bankier listing: each item links the article; the time line sits just above the headline."""
    items: list[NewsItem] = []
    seen: set[str] = set()
    for a in re.finditer(
        r'<a\s[^>]*href="(/wiadomosc/[^"]+?-(\d+)\.html)"[^>]*>(.*?)</a>', html, re.S | re.I
    ):
        lines = strip_tags(a.group(3))
        t_idx = next((i for i, ln in enumerate(lines) if _BR_TIME.search(ln)), None)
        if t_idx is None:
            continue
        tm = _BR_TIME.search(lines[t_idx])
        assert tm is not None
        title = next(
            (ln for ln in lines[t_idx + 1 :] if len(ln) >= 15 and not _BR_TIME.search(ln)), None
        )
        if title is None:
            continue
        url = urljoin(base, htmllib.unescape(a.group(1)))
        if url in seen:
            continue
        seen.add(url)
        items.append(
            NewsItem(
                isin=isin,
                source=BANKIER,
                # the listing does not name the outlet; a "PAP" in the teaser is not one
                outlet="Bankier",
                published_at=_warsaw(tm.group(1), tm.group(2), None),
                precision="minute",
                title=title,
                url=url,
                fetched_at=fetched_at,
            )
        )
    return _classify_page(items, html)


# Google gives items it has no time for a shared whole-hour placeholder (08:00:00 GMT on
# 2026-11-23, for 11 of 27 XTB items). A whole-hour time that this many items in one feed
# share is read as "date only"; so is midnight GMT.
PLACEHOLDER_MIN_SHARE = 3


def _placeholder_times(pubs: Iterable[datetime]) -> set[tuple[int, int, int]]:
    whole = Counter((t.hour, 0, 0) for t in pubs if t.minute == 0 and t.second == 0)
    out = {hms for hms, n in whole.items() if n >= PLACEHOLDER_MIN_SHARE}
    out.add((0, 0, 0))
    return out


def parse_google_rss(xml_bytes: bytes, *, isin: str, fetched_at: datetime) -> PageResult:
    """Google News RSS: ``title``, ``link``, RFC 822 ``pubDate``, and ``source`` (outlet)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return PageResult(Outcome.DRIFT, detail=f"xml: {exc}")
    rows: list[tuple[str, str, datetime, str, str]] = []
    seen: set[str] = set()
    for node in root.iter("item"):
        raw_title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        src = node.find("source")
        outlet = (src.text or "").strip() if src is not None else ""
        outlet_url = (src.get("url") or "").strip() if src is not None else ""
        if not raw_title or not link or not pub:
            continue
        if outlet and raw_title.endswith(f" - {outlet}"):
            raw_title = raw_title[: -len(f" - {outlet}")]
        try:
            when = email.utils.parsedate_to_datetime(pub).astimezone(UTC)
        except (TypeError, ValueError):
            continue
        if link in seen:
            continue
        seen.add(link)
        rows.append((raw_title.strip(), link, when, outlet, outlet_url))
    placeholders = _placeholder_times(when for _t, _l, when, _o, _u in rows)
    items = [
        NewsItem(
            isin=isin,
            source=GOOGLE,
            outlet=outlet,
            published_at=when,
            precision="day" if (when.hour, when.minute, when.second) in placeholders else "minute",
            title=title,
            url=link,
            fetched_at=fetched_at,
            outlet_url=outlet_url,
        )
        for title, link, when, outlet, outlet_url in rows
    ]
    outcome = Outcome.OK if items else Outcome.EMPTY
    return PageResult(outcome, items)


def _classify_page(items: list[NewsItem], html: str) -> PageResult:
    """OK with items; DRIFT when a full page has time stamps but no item could be parsed."""
    if items:
        return PageResult(Outcome.OK, items)
    if len(html) >= DRIFT_MIN_BYTES and _BR_TIME.search(html):
        return PageResult(Outcome.DRIFT, [], detail="timestamps present, no item parsed")
    return PageResult(Outcome.EMPTY, [], detail="no items on page")


def isin_on_page(html: str, isin: str) -> bool:
    """True when the page's company block shows ``isin`` after an ``ISIN`` label.

    Table markup may sit between the two (``<th>ISIN:</th><td>PL…</td>``).
    """
    pattern = rf"ISIN:?\s*(?:<[^>]{{0,40}}>\s*){{0,4}}{re.escape(isin)}(?![A-Z0-9])"
    return re.search(pattern, html) is not None


_BANKIER_TITLE = re.compile(r"Wiadomości spółki - (.*?) \(([A-Z0-9]{1,16})\) - ")
# "Wiadomości GRUPA AZOTY (ATT)- BiznesRadar.pl"; the code is left out when it is the name
# ("Wiadomości XTB- BiznesRadar.pl"), and a name may hold a hyphen (MO-BRUK).
_BR_TITLE = re.compile(r"<title>\s*Wiadomości (.*?)(?: \(([A-Z0-9]{1,16})\))?\s*-\s*BiznesRadar")


def page_identity_ok(html: str, source: str, company: Company) -> bool:
    """Whether a listing page is about ``company``, checked the way each portal shows it.

    BiznesRadar prints the ISIN in its company block. Bankier prints no ISIN anywhere; its
    page title names the company and its ticker, ``Wiadomości spółki - XTB SA (XTB) - …``.
    """
    if source == BIZNESRADAR:
        return isin_on_page(html, company.isin)
    if source == BANKIER:
        m = _BANKIER_TITLE.search(html[:20000])
        return bool(m and company.ticker and m.group(2) == company.ticker)
    return False


# ---- matching and exclusions ------------------------------------------------------------

_MARKET_WRAP = re.compile(
    r"notowania gpw|wygrani i przegrani|analiza techniczna|sesja na gpw|podsumowanie sesji|"
    r"indeks wig|wig20 |gielda dzis|mapa gpw|dzien na gpw|\[br premium\]|w centrum uwagi"
)
# Sport: clubs carry sponsors' names ("Grupa Azoty ZAKSA ... wynik meczu").
_SPORT = re.compile(
    r"\bmecz\w*|kto wygral|plusliga|siatkar\w*|siatkowk\w*|ekstraklas\w*|koszykark\w*|"
    r"pilkark\w*|\bgala\b|\bklub\w*"
)
_PRICE_LINE = re.compile(
    r"kurs (akcji )?.{0,80}? (spada|rosnie|wzrasta|traci|zyskuje|drozeje|tanieje) "
    r"(o )?\d+([.,]\d+)? ?(proc|%)"
)


# A filing reprinted as a headline ("MIRBUD S.A. - Złożenie ...", "ENEA ELEKTROWNIA POŁANIEC SA
# (121/2026) ..."): filings are judged on the report path, from the filing itself, so the
# reprint never counts as news (and is often another issuer's filing).
_FILING_REPRINT = re.compile(
    r"^[^a-z]{0,3}[\w .,&'-]{2,80}? s\.? ?a\.?(?: w restrukturyzacji)? "
    r"(?:-|–|\(\d{1,4}/\d{4}\)|raport (?:okresowy|kwartalny|polroczny|roczny))"
)
# BiznesRadar's own copies of filings ("record-type-POST"), e.g. /a/171279,xtb-sa-raport-...
_BR_FILING_PATH = re.compile(r"^/a/\d+,")

# What makes a headline a cause: it names an event that moves a price. On normalised text
# (lower case, no diacritics). A headline that only names the company (a feature, an
# interview, a product, a sponsorship) or only describes the move ("akcje X traca 10 proc.")
# is not an explanation. Precision sample, 2026-11-24: such headlines were most of the
# wrong news causes.
EVENT = re.compile(
    r"\b("
    # results and guidance
    r"wynik\w*|zysk\w*|strat(?:a|y|e|om|ach)?\b|stracie|przychod\w*|ebitda|szacun\w*|szacuj\w*|"
    r"konsensus\w*|prognoz\w*|oczekiwan\w*|(?:i|ii|iii|iv) kw|kwartal\w*|polrocz\w*|"
    r"rdr|r/r|"
    # analysts
    r"rekomendac\w*|cen\w* docelow\w*|wycen\w*|kupuj|sprzedaj|redukuj|akumuluj|trzymaj|"
    r"neutralnie|przewaz\w*|niedowaz\w*|rating\w*|"
    # corporate actions and ownership
    r"dywidend\w*|skup\w* akcji|buyback|emisj\w*|abb|ksieg\w* popytu|sprzedaz\w* akcji|"
    r"pakiet\w* akcji|wezwani\w*|wykup\w*|delisting\w*|(?:opusc\w*|zejsc\w*|wycofan\w*) "
    r"(?:z )?gield\w*|przejec\w*|przejm\w*|akwizyc\w*|fuzj\w*|polaczeni\w*|podzial\w*|"
    r"split\w*|konwersj\w*|restrukturyz\w*|upadlos\w*|sanac\w*|uklad\w*|dopuszcz\w*|"
    # contracts and talks
    r"umow\w*|kontrakt\w*|zamowieni\w*|przetarg\w*|porozumieni\w*|list\w* intencyjn\w*|"
    r"rozmow(?:y|ach|om)|rozmawia\w*|negocjac\w*|"
    # regulators, courts, approvals
    r"knf|uokik|komisj\w* europejsk\w*|decyzj\w*|kar[aeyz]\b|kary|sad\w*|pozew\w*|pozw\w*|"
    r"ugod\w*|fda|ema|rejestrac\w*|zatwierdz\w*|licencj\w*|koncesj\w*|"
    # products and trials
    r"premier\w*|badani\w* klinicz\w*|wyniki badania|biorownowaz\w*|patent\w*"
    r")\b"
)


def _mention(title: str, term: str) -> bool:
    if len(term) <= 4:
        # short names collide with everyday words: only the upper-case form counts
        return (
            re.search(rf"(?<![A-Za-z0-9]){re.escape(term.upper())}(?![A-Za-z0-9])", title)
            is not None
        )
    n_title, n_term = normalize(title), normalize(term)
    return re.search(rf"(?<![a-z0-9]){re.escape(n_term)}(?![a-z0-9])", n_title) is not None


def verdict(
    item: NewsItem,
    company: Company,
    other_names: Sequence[str] = (),
    *,
    require_mention: bool = True,
    require_event: bool = True,
) -> Verdict:
    """Count the headline as an explanation, or say why not. Non-counting items are still stored.

    ``require_mention=False`` is for the portals' per-company pages (BiznesRadar, Bankier):
    the page is already about the company, so a headline need not name it. The exclusions
    (own page, noise, wraps, price lines, filing reprints, lists of companies) apply either
    way. ``require_event`` asks the headline to name a price-moving event (``EVENT``).
    """
    hosts = {urlparse(u).netloc.lower() for u in (item.url, item.outlet_url) if u}
    if any(h == d or h.endswith("." + d) for h in hosts for d in company.own_domains):
        return Verdict(False, "excluded:own_page")
    title_n = normalize(item.title)
    if any(
        re.search(rf"(?<![a-z0-9]){re.escape(normalize(t))}(?![a-z0-9])", title_n)
        for t in company.noise_terms
    ):
        return Verdict(False, "excluded:noise")
    if _MARKET_WRAP.search(title_n):
        return Verdict(False, "excluded:market_wrap")
    if _SPORT.search(title_n):
        return Verdict(False, "excluded:sport")
    if _PRICE_LINE.search(title_n):
        return Verdict(False, "excluded:price_line")
    if (
        _FILING_REPRINT.search(title_n)
        or normalize(item.outlet) == "ebi/espi"
        or (item.source == BIZNESRADAR and _BR_FILING_PATH.match(urlparse(item.url).path))
    ):
        return Verdict(False, "excluded:filing_reprint")
    terms = [*company.names]
    if company.ticker:
        terms.append(company.ticker)
    if require_mention and not any(_mention(item.title, t) for t in terms if t):
        return Verdict(False, "no_mention")
    others = [n for n in other_names if n and n not in terms and _mention(item.title, n)]
    if len(others) >= 2:  # the headline also names two other companies: a list, not an explanation
        return Verdict(False, "excluded:multi_company")
    if require_event and not EVENT.search(title_n):
        return Verdict(False, "no_event")
    return Verdict(True, "match")


# ---- fetching ---------------------------------------------------------------------------


class HostThrottle:
    """Worker limit per host, halved on each 429/503 (never below one)."""

    def __init__(self, workers: int) -> None:
        self._lock = threading.Lock()
        self._limit = max(1, workers)

    @property
    def limit(self) -> int:
        """Current worker count for the host."""
        with self._lock:
            return self._limit

    def halve(self) -> None:
        """Halve the worker count after a 429 or 503."""
        with self._lock:
            self._limit = max(1, self._limit // 2)


def fetch_page(
    client: PoliteClient,
    url: str,
    *,
    throttle: HostThrottle | None = None,
    sleep: Callable[[float], None] = time.sleep,
    use_cache: bool = True,
) -> tuple[Outcome, FetchResult | None, str]:
    """GET one page: (outcome, response or None, detail). Never bypasses a block page.

    429 and 503 wait for ``Retry-After`` (or 30 s) and halve the host's workers; a block
    page is returned as BLOCKED at once and not retried.
    """
    last: tuple[Outcome, FetchResult | None, str] = (Outcome.UNREACHABLE, None, "no attempt")
    for _attempt in range(MAX_ATTEMPTS):
        try:
            res = client.get(url, use_cache=use_cache)
        except SourceBlockedError as exc:
            return Outcome.BLOCKED, None, str(exc)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (429, 503):
                if throttle is not None:
                    throttle.halve()
                wait = _retry_after(exc.response.headers.get("retry-after"))
                last = (Outcome.RATE_LIMITED, None, f"HTTP {code}, waited {wait:.0f}s")
                sleep(wait)
                continue
            return Outcome.HTTP_ERROR, None, f"HTTP {code}"
        except httpx.TransportError as exc:
            # the client has already retried it (``PoliteClient.retry_pauses_s``)
            return Outcome.UNREACHABLE, None, type(exc).__name__
        return Outcome.OK, res, str(res.status)
    return last


def fetch_listing(
    client: PoliteClient,
    url: str,
    *,
    throttle: HostThrottle | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Outcome, str | None, str]:
    """GET one listing page: (outcome, html or None, detail)."""
    outcome, res, detail = fetch_page(client, url, throttle=throttle, sleep=sleep)
    return outcome, (res.text() if res is not None else None), detail


@dataclass(frozen=True)
class PortalSymbol:
    """A portal's own code for a company, checked against the company's page."""

    source: str
    symbol: str
    page_name: str  # the company's name as the portal prints it, e.g. "Grupa Azoty SA"


def resolve_symbol(
    client: PoliteClient,
    source: str,
    company: Company,
    guess: str,
    *,
    throttle: HostThrottle | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Outcome, PortalSymbol | None, str]:
    """The portal's symbol for ``company``, starting from ``guess`` (the GPW short name).

    BiznesRadar redirects a short name to its own code (``GRUPAAZOTY`` → ``/wiadomosci/ATT``)
    and its later pages only work under that code, so the final URL is read with the cache
    off. A symbol is accepted only when the page passes :func:`page_identity_ok`.
    """
    if source == BIZNESRADAR:
        url = BIZNESRADAR_URL.format(symbol=quote(guess), page=1)
    elif source == BANKIER:
        url = BANKIER_URL.format(symbol=quote(guess), page=1)
    else:
        raise ValueError(f"not a page source: {source}")
    outcome, res, detail = fetch_page(client, url, throttle=throttle, sleep=sleep, use_cache=False)
    if outcome is not Outcome.OK or res is None:
        return outcome, None, detail
    html = res.text()
    probe = Company(company.isin, company.names, guess, company.own_domains, company.noise_terms)
    if source == BIZNESRADAR:
        m = re.search(r"/wiadomosci/([^,/?#]+)", urlparse(res.url).path)
        symbol = m.group(1) if m else guess
        t = _BR_TITLE.search(html[:20000])
        name = htmllib.unescape(t.group(1)).strip() if t else ""
        ok = page_identity_ok(html, source, probe)
    else:
        symbol = guess
        t = _BANKIER_TITLE.search(html[:20000])
        name = htmllib.unescape(t.group(1)).strip() if t else ""
        ok = page_identity_ok(html, source, probe)
    if not ok:
        return Outcome.EMPTY, None, "page is not this company"
    return Outcome.OK, PortalSymbol(source, symbol, name), detail


def fetch_company(
    client: PoliteClient,
    source: str,
    company: Company,
    *,
    since: datetime,
    until: datetime,
    max_pages: int = 20,
    symbol: str | None = None,
    throttle: HostThrottle | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PageResult:
    """Headlines for one company from one source, pages walked until they are older than ``since``.

    The ISIN check runs on the first page: a portal page that does not show the company's ISIN
    is not that company's news, so the result is EMPTY rather than mismatched items.
    """
    sym = quote(symbol or company.ticker or "")
    if source == BIZNESRADAR:
        template, parse = BIZNESRADAR_URL, parse_biznesradar
    elif source == BANKIER:
        template, parse = BANKIER_URL, parse_bankier
    else:
        raise ValueError(f"not a page source: {source}")

    collected: list[NewsItem] = []
    seen: set[str] = set()
    pages = 0
    while pages < max_pages:
        # Page numbers are computed, not scraped: a "next" link can point back at this page.
        url = template.format(symbol=sym, page=pages + 1)
        outcome, html, detail = fetch_listing(client, url, throttle=throttle, sleep=sleep)
        if outcome is not Outcome.OK or html is None:
            return PageResult(outcome, collected, None, detail)
        probe = Company(
            company.isin,
            company.names,
            symbol or company.ticker,
            company.own_domains,
            company.noise_terms,
        )
        if pages == 0 and not page_identity_ok(html, source, probe):
            return PageResult(Outcome.EMPTY, [], None, "page is not this company")
        page = parse(html, isin=company.isin, fetched_at=now(), base=url)
        if page.outcome is Outcome.DRIFT:
            return PageResult(Outcome.DRIFT, collected, None, page.detail)
        pages += 1
        fresh = [i for i in page.items if i.url not in seen]
        if not fresh:  # past the last page, or the source is repeating itself
            break
        seen.update(i.url for i in fresh)
        collected.extend(fresh)
        oldest = min(i.published_at for i in fresh)
        if oldest < since:  # listings are newest first: everything further is older
            break
    kept = [i for i in collected if since <= i.published_at <= until]
    return PageResult(Outcome.OK if kept else Outcome.EMPTY, kept, None, f"{pages} pages")


def google_query_url(company: Company, *, since: datetime, until: datetime) -> str:
    """The Google News search for a company between two instants (day granularity)."""
    q = quote(f'"{company.names[0]}"' if company.names else company.ticker or "")
    after = since.astimezone(WARSAW).date().isoformat()
    # ``before:`` excludes its own day, so ask for the day after ``until``
    before = (until.astimezone(WARSAW).date() + timedelta(days=1)).isoformat()
    return GOOGLE_URL.format(query=q, after=after, before=before)


def fetch_google(
    client: PoliteClient,
    company: Company,
    *,
    since: datetime,
    until: datetime,
    throttle: HostThrottle | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PageResult:
    """Google News for a company. The feed has no pages: one request per window."""
    url = google_query_url(company, since=since, until=until)
    outcome, body, detail = fetch_listing(client, url, throttle=throttle, sleep=sleep)
    if outcome is not Outcome.OK or body is None:
        return PageResult(outcome, [], None, detail)
    page = parse_google_rss(body.encode("utf-8"), isin=company.isin, fetched_at=now())
    kept = [i for i in page.items if since <= i.published_at <= until]
    return PageResult(Outcome.OK if kept else Outcome.EMPTY, kept, None, detail)


def unique(items: Iterable[NewsItem]) -> list[NewsItem]:
    """One row per (source, url), keeping the earliest fetch."""
    best: dict[tuple[str, str], NewsItem] = {}
    for it in items:
        key = (it.source, it.url)
        if key not in best or it.fetched_at < best[key].fetched_at:
            best[key] = it
    return sorted(best.values(), key=lambda i: i.published_at)
