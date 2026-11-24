"""What the news timeline and the coverage study share: names, sessions, reports, rows.

* **Names**: a company's names as headlines print them (``CompanyInfo``), and every
  company's names in one pattern to find others named in a headline (``NameIndex``).
* **Sessions**: a timestamp belongs to the GPW session it can move. Before 17:05 Warsaw it
  is that day's; later it is the next session's. A date-only headline is its date's.
* **Reports**: which ESPI/EBI reports can move a price at all (``report_rank``).
* **Rows**: headlines as rows of ``ITEM_SCHEMA``, keyed by ``item_id``.

Pacing (user's decision, 2026-11-23): no pause between requests to BiznesRadar and Bankier
(``PAGE_INTERVAL_S``), and ``GOOGLE_INTERVAL_S`` between Google News queries, which bans
quickly.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import polars as pl

from undercurrent import gpw_calendar, news
from undercurrent.classify import normalize
from undercurrent.news import WARSAW, Company, NewsItem

CLOSE_LOCAL = time(17, 5)  # the session's close, Warsaw time (as in reactions and valuation)

PAGE_SOURCES = (news.BIZNESRADAR, news.BANKIER)
PAGE_INTERVAL_S = 0.0
GOOGLE_INTERVAL_S = 3.0
JITTER_S = 0.5

# Names that put a company in headlines without being company news: sponsorships, and the
# exchange itself for the exchange's operator ("spadki na GPW" is market news, not GPW's).
NOISE_TERMS: dict[str, tuple[str, ...]] = {
    "PLXTRDM00011": ("KSW",),
    "PLGPW0000017": ("na GPW", "z GPW", "do GPW", "spółki z GPW", "spółek z GPW"),
}

# Reports that can explain a move, by rank (lower first); the rest (governance notices, AGM
# paperwork, board CVs, MAR notices, holder thresholds) never do. Precision sample,
# 2026-11-24: the first report of the session, whatever it was, was a quarter of the wrong
# report causes.
PERIODIC_KINDS = frozenset({"Kwartalny", "Półroczny", "Roczny", "Okresowy"})
# Notices that name an event word but are paperwork: bond listings, statute changes, AGMs.
_ROUTINE = re.compile(
    r"obligac\w*|euroobligac\w*|statut\w*|zgromadzeni\w*|rejestrac\w*|programu? emisji|"
    r"dopuszczeni\w* do obrotu"
)
_ESEF = re.compile(r"\.xbri|\.xhtml|annual|roczn", re.I)

_LEGAL = re.compile(
    r"[\s,]+(spółka akcyjna|s\.?\s?a\.?|se|plc|ltd\.?|n\.?v\.?|ab|asi|w restrukturyzacji)\s*$",
    re.I,
)

ITEM_SCHEMA: dict[str, Any] = {
    "item_id": pl.String,
    "isin": pl.String,
    "source": pl.String,
    "outlet": pl.String,
    "outlet_url": pl.String,
    "published_at": pl.Datetime("us", "UTC"),
    "precision": pl.String,
    "title": pl.String,
    "url": pl.String,
    "fetched_at": pl.Datetime("us", "UTC"),
}


# ---- names ------------------------------------------------------------------------------


def plain_name(name: str | None) -> str:
    """``Grupa Azoty S.A.`` → ``Grupa Azoty``: legal forms dropped, case kept."""
    t = re.sub(r"\s+", " ", (name or "").replace("\xa0", " ")).strip()
    for _ in range(2):
        t = _LEGAL.sub("", t).strip(" ,.-")
    return t


@dataclass(frozen=True)
class CompanyInfo:
    """What the lake knows about a stock, to look its news up with."""

    isin: str
    short_name: str  # GPW short name, e.g. GRUPAAZOTY: the portals' first symbol guess
    issuer_name: str = ""  # from its reports, legal form dropped
    portal_names: tuple[str, ...] = ()  # names the portals print, legal form dropped

    def company(self, google_hosts: Iterable[str] = ()) -> Company:
        """The matching profile for ``news.verdict``."""
        names: list[str] = []
        for n in (self.issuer_name, *self.portal_names):
            p = plain_name(n)
            if p and normalize(p) not in {normalize(x) for x in names}:
                names.append(p)
        if not names:
            names.append(self.short_name)
        return Company(
            isin=self.isin,
            names=tuple(names),
            ticker=self.short_name,
            own_domains=tuple(sorted(set(google_hosts))),
            noise_terms=NOISE_TERMS.get(self.isin, ()),
        )


class NameIndex:
    """Every company's names in one pattern, to find other companies named in a headline.

    Names of five characters or more match case-insensitively; GPW short names of three or
    four (PGE, LPP, PZU) only in upper case, as ``news.verdict`` matches them.
    """

    def __init__(self, companies: dict[str, Company]) -> None:
        long_owner: dict[str, tuple[str, str]] = {}
        short_owner: dict[str, tuple[str, str]] = {}
        for isin, co in companies.items():
            for n in co.names:
                key = normalize(n)
                if len(key) >= 5:
                    long_owner.setdefault(key, (isin, n))
            for n in (*co.names, co.ticker or ""):
                if 3 <= len(n) <= 4 and n.isupper():
                    short_owner.setdefault(n, (isin, n))
        self._long, self._short = long_owner, short_owner
        self._rx_long = _alternation(long_owner, r"(?<![a-z0-9])(", r")(?![a-z0-9])")
        self._rx_short = _alternation(short_owner, r"(?<![A-Za-z0-9])(", r")(?![A-Za-z0-9])")

    def others(self, title: str, isin: str) -> list[str]:
        """One name per company other than ``isin`` that ``title`` mentions."""
        found: dict[str, str] = {}
        if self._rx_long is not None:
            for m in self._rx_long.findall(normalize(title)):
                owner, name = self._long[m]
                found.setdefault(owner, name)
        if self._rx_short is not None:
            for m in self._rx_short.findall(title):
                owner, name = self._short[m]
                found.setdefault(owner, name)
        found.pop(isin, None)
        return sorted(found.values())


def _alternation(keys: dict[str, Any], head: str, tail: str) -> re.Pattern[str] | None:
    alts = sorted(keys, key=len, reverse=True)
    return re.compile(head + "|".join(map(re.escape, alts)) + tail) if alts else None


def own_hosts(items: Sequence[NewsItem], info: CompanyInfo) -> set[str]:
    """Google outlets that are the company itself: the outlet's name is the company's."""
    names = {
        normalize(plain_name(n))
        for n in (info.issuer_name, info.short_name, *info.portal_names)
        if n
    }
    names.discard("")
    out: set[str] = set()
    for it in items:
        if it.source != news.GOOGLE or not it.outlet_url:
            continue
        outlet = normalize(re.sub(r"\.(com|pl|eu)$", "", it.outlet.strip(), flags=re.I))
        if outlet in names:
            host = re.sub(r"^www\.", "", it.outlet_url.split("//")[-1].split("/")[0].lower())
            out.add(host)
    return out


# ---- sessions ---------------------------------------------------------------------------


def session_of(ts: datetime, precision: str = "minute") -> date:
    """The GPW session a timestamp belongs to (17:05 Warsaw cut, rolled to a session)."""
    local = ts.astimezone(WARSAW)
    if precision == "day":
        d = local.date()
    else:
        d = local.date() if local.time() < CLOSE_LOCAL else local.date() + timedelta(days=1)
    while not gpw_calendar.is_session(d):
        d += timedelta(days=1)
    return d


def previous_session(d: date) -> date:
    """The GPW session before ``d``."""
    p = d - timedelta(days=1)
    while not gpw_calendar.is_session(p):
        p -= timedelta(days=1)
    return p


def close_at(d: date) -> datetime:
    """The session's close as a UTC instant."""
    return datetime.combine(d, CLOSE_LOCAL, tzinfo=WARSAW).astimezone(UTC)


# ---- reports ----------------------------------------------------------------------------


def report_rank(
    kind: str | None,
    signal_type: str | None,
    subtype: str | None,
    title: str | None,
    attachments: str | None = None,
) -> int | None:
    """How likely a report is to move the price, or ``None`` if it never does.

    0 results or inside information, 1 financing, 2 another notice whose title names an
    event (``news.EVENT``).
    """
    if kind in PERIODIC_KINDS or signal_type == "periodic":
        return 0
    if not title and attachments and _ESEF.search(attachments):
        return 0  # an annual report filed as a current report, title left empty
    if signal_type == "inside_info":
        return 1 if subtype == "financing" else 0
    if title and news.EVENT.search(normalize(title)) and not _ROUTINE.search(normalize(title)):
        return 2
    return None


# ---- rows -------------------------------------------------------------------------------


def item_id(source: str, url: str) -> str:
    """Stable key of a headline: its source and link."""
    return hashlib.sha256(f"{source}|{url}".encode()).hexdigest()[:32]


def items_frame(items: Sequence[NewsItem]) -> pl.DataFrame:
    """News items as rows of ``ITEM_SCHEMA``."""
    return pl.DataFrame(
        [
            {
                "item_id": item_id(i.source, i.url),
                "isin": i.isin,
                "source": i.source,
                "outlet": i.outlet,
                "outlet_url": i.outlet_url,
                "published_at": i.published_at,
                "precision": i.precision,
                "title": i.title,
                "url": i.url,
                "fetched_at": i.fetched_at,
            }
            for i in items
        ],
        schema=ITEM_SCHEMA,
    )


def frame_items(df: pl.DataFrame) -> list[NewsItem]:
    """Rows of ``ITEM_SCHEMA`` back as news items."""
    return [
        NewsItem(
            isin=r["isin"],
            source=r["source"],
            outlet=r["outlet"] or "",
            published_at=r["published_at"],
            precision=r["precision"],
            title=r["title"],
            url=r["url"],
            fetched_at=r["fetched_at"],
            outlet_url=r["outlet_url"] or "",
        )
        for r in df.iter_rows(named=True)
    ]
