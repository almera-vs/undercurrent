"""Copies of ESPI attachments on financial portals: Parkiet, Bankier, BiznesRadar.

The MAR Art. 19 notification form is a PDF attached to the ESPI report. The exchange serves
attachments only behind bot protection, but these portals republish ESPI reports and host
their own copies of the files (see docs/SOURCES.md §9). Each portal is used the same way:

1. list recent report pages with their publication time and title (a sitemap or a listing),
2. match a report from the exchange listing by time (± ``MATCH_MINUTES``) and title,
3. read the matched page and return the PDF links on it.

All requests go through :class:`~undercurrent.fetch.PoliteClient`. Nothing here stores or
republishes the files; :mod:`undercurrent.forms` extracts facts from them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz
from selectolax.lexbor import LexborHTMLParser

from undercurrent.classify import normalize
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
MATCH_MINUTES = 10
TITLE_SCORE = 85
ISSUER_SCORE = 90


@dataclass(frozen=True)
class Candidate:
    """One report page on a portal."""

    portal: str
    url: str
    published_local: datetime  # naive, Europe/Warsaw
    title: str  # as much of the title as the portal shows (or its URL slug)
    issuer: str  # issuer name as the portal shows it (Parkiet: the URL slug)
    number: str | None = None  # report number "66/2026", when the portal shows it


@dataclass(frozen=True)
class ReportRef:
    """What a portal page is matched against: one report from the exchange listing."""

    published_local: datetime
    title: str
    issuer_names: tuple[str, ...]  # official name and the exchange's short name
    number: str | None = None


def slug(text: str | None) -> str:
    """``Informacja o transakcjach (art. 19)`` → ``informacja-o-transakcjach-art-19``."""
    return re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")


def title_score(report_title: str, candidate_title: str) -> float:
    """Similarity of two titles after slugging (0–100). Handles truncated slugs."""
    a, b = slug(report_title), slug(candidate_title)
    if not a or not b:
        return 0.0
    return float(max(fuzz.partial_ratio(a, b), fuzz.token_set_ratio(a, b)))


_LEGAL_FORM = re.compile(
    r"-(spolka-akcyjna|spolka-europejska|s-a|sa|se|asi|nv|n-v|plc|ag|inc|ltd|limited)(?=-|$)"
)


def _issuer_key(name: str) -> str:
    return _LEGAL_FORM.sub("", "-" + slug(name)).strip("-")


def issuer_score(names: Iterable[str], candidate_issuer: str) -> float:
    """Best similarity (0–100) between any of the report's issuer names and the portal's."""
    b = _issuer_key(candidate_issuer)
    best = 0.0
    for n in names:
        a = _issuer_key(n)
        if a and b:
            best = max(best, float(fuzz.partial_ratio(a, b)))
    return best


def best_match(ref: ReportRef, candidates: Iterable[Candidate]) -> Candidate | None:
    """The portal page for ``ref``, or ``None``.

    A page qualifies when it is published within ``MATCH_MINUTES``, names the same issuer
    and has a similar title; a report number shown by the portal must agree. The closest
    title wins.
    """
    window = timedelta(minutes=MATCH_MINUTES)
    scored = []
    for c in candidates:
        delta = abs(c.published_local - ref.published_local)
        if delta > window:
            continue
        if c.number and ref.number and c.number != ref.number:
            continue
        if issuer_score(ref.issuer_names, c.issuer) < ISSUER_SCORE:
            continue
        t = title_score(ref.title, c.title)
        if t >= TITLE_SCORE:
            scored.append((t, -delta.total_seconds(), c))
    if not scored:
        return None
    return max(scored, key=lambda s: (s[0], s[1]))[2]


def _pdf_links(html: str, base: str, pattern: str) -> list[str]:
    tree = LexborHTMLParser(html)
    out: list[str] = []
    for a in tree.css("a"):
        href = urljoin(base, a.attributes.get("href") or "")
        is_pdf = href.lower().split("?")[0].endswith(".pdf")
        if re.search(pattern, href) and is_pdf and href not in out:
            out.append(href)
    return out


# --------------------------------------------------------------------------- Parkiet


class Parkiet:
    """parkiet.com: monthly sitemaps list every ESPI page with its publication time."""

    name = "parkiet"
    base = "https://www.parkiet.com"

    def candidates(self, client: PoliteClient, days: list[date]) -> list[Candidate]:
        """Report pages published on ``days``."""
        months = sorted({d.replace(day=1) for d in days})
        out: list[Candidate] = []
        today = datetime.now(WARSAW).date()
        for m in months:
            url = f"{self.base}/sitemaps/sitemap.{m:%Y%m}.xml"
            current = (m.year, m.month) == (today.year, today.month)
            try:
                xml = client.get(url, max_age_s=3600 if current else 30 * 86400).text()
            except Exception as exc:  # a missing month is not fatal
                log.info("parkiet sitemap %s unavailable: %s", url, exc)
                continue
            out += self.parse_sitemap(xml)
        wanted = set(days)
        return [c for c in out if c.published_local.date() in wanted]

    def parse_sitemap(self, xml: str) -> list[Candidate]:
        """Candidates from one monthly sitemap."""
        out = []
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
            loc = re.search(r"<loc>([^<]+)</loc>", block)
            mod = re.search(r"<lastmod>([^<]+)</lastmod>", block)
            if not loc or not mod or "/komunikaty-espi/" not in loc.group(1):
                continue
            try:
                ts = datetime.fromisoformat(mod.group(1).strip())
            except ValueError:
                continue
            # Parkiet stores the Warsaw publication time as if it were UTC and then renders
            # it in local time (14:27 Warsaw → "16:27+02:00"). Its UTC reading is the
            # original Warsaw wall-clock time.
            local = ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo else ts
            rest = re.sub(r"^.*/art\d+-", "", loc.group(1).strip())
            out.append(Candidate(self.name, loc.group(1).strip(), local, rest, rest))
        return out

    def attachments(self, html: str, url: str) -> list[str]:
        """PDF links on a report page."""
        return _pdf_links(html, url, r"gremicdn\.pl/file/")


# --------------------------------------------------------------------------- Bankier


class Bankier:
    """bankier.pl: the company-announcements listing, newest first, 34 per page."""

    name = "bankier"
    base = "https://www.bankier.pl"
    max_pages = 60
    reach_days = 21  # how far back the listing is paged

    def candidates(self, client: PoliteClient, days: list[date]) -> list[Candidate]:
        """Report pages published on ``days``."""
        days = [
            d for d in days if d >= datetime.now(WARSAW).date() - timedelta(days=self.reach_days)
        ]
        if not days:
            return []
        oldest = min(days)
        out: list[Candidate] = []
        for page in range(1, self.max_pages + 1):
            url = f"{self.base}/gielda/wiadomosci/komunikaty-spolek"
            if page > 1:
                url += f"/{page}"
            try:
                html = client.get(url, max_age_s=1800).text()
            except Exception as exc:
                log.info("bankier listing page %d unavailable: %s", page, exc)
                break
            items = self.parse_listing(html)
            if not items:
                break
            out += items
            if min(i.published_local for i in items).date() < oldest:
                break
        wanted = set(days)
        return [c for c in out if c.published_local.date() in wanted]

    def parse_listing(self, html: str) -> list[Candidate]:
        """Candidates from one listing page."""
        tree = LexborHTMLParser(html)
        out = []
        for item in tree.css(".m-quotes-announcements-item"):
            d = item.css_first(".m-quotes-announcements-item__date")
            a = item.css_first("a.m-quotes-announcements-item__anchor")
            if d is None or a is None:
                continue
            try:
                ts = datetime.strptime(d.text(strip=True), "%d.%m.%Y %H:%M")
            except ValueError:
                continue
            text = a.text(strip=True)
            issuer, _, title = text.partition(":")
            href = urljoin(self.base, a.attributes.get("href") or "")
            out.append(Candidate(self.name, href, ts, title or issuer, issuer))
        return out

    def attachments(self, html: str, url: str) -> list[str]:
        """PDF links on a report page."""
        return _pdf_links(html, url, r"(bonnier|bankier)\.pl/static/att/")


# --------------------------------------------------------------------------- BiznesRadar


class BiznesRadar:
    """biznesradar.pl: the ESPI/EBI listing, newest first, about 30 per page."""

    name = "biznesradar"
    base = "https://www.biznesradar.pl"
    max_pages = 80
    reach_days = 21  # how far back the listing is paged

    def candidates(self, client: PoliteClient, days: list[date]) -> list[Candidate]:
        """Report pages published on ``days``."""
        days = [
            d for d in days if d >= datetime.now(WARSAW).date() - timedelta(days=self.reach_days)
        ]
        if not days:
            return []
        oldest = min(days)
        out: list[Candidate] = []
        for page in range(1, self.max_pages + 1):
            url = f"{self.base}/temat/76,espi-ebi,{page}"
            try:
                html = client.get(url, max_age_s=1800).text()
            except Exception as exc:
                log.info("biznesradar listing page %d unavailable: %s", page, exc)
                break
            items = self.parse_listing(html)
            if not items:
                break
            out += items
            if min(i.published_local for i in items).date() < oldest:
                break
        wanted = set(days)
        return [c for c in out if c.published_local.date() in wanted]

    def parse_listing(self, html: str) -> list[Candidate]:
        """Candidates from one listing page."""
        tree = LexborHTMLParser(html)
        out = []
        for card in tree.css(".article-card"):
            a = card.css_first("h3 a")
            t = card.css_first(".time")
            if a is None or t is None:
                continue
            try:
                ts = datetime.strptime(t.text(strip=True), "%Y-%m-%d, %H:%M")
            except ValueError:
                continue
            text = a.text(strip=True)
            m = re.match(r"^(?P<issuer>.*?)\s*\((?P<num>\d+/\d{4})\)\s*(?P<title>.*)$", text)
            href = urljoin(self.base, a.attributes.get("href") or "")
            if m:
                out.append(Candidate(self.name, href, ts, m["title"], m["issuer"], m["num"]))
            else:
                out.append(Candidate(self.name, href, ts, text, text))
        return out

    def attachments(self, html: str, url: str) -> list[str]:
        """PDF links on a report page."""
        return _pdf_links(html, url, r"biznesradar\.pl/storage/")


PORTALS: tuple[Parkiet | Bankier | BiznesRadar, ...] = (Parkiet(), Bankier(), BiznesRadar())
