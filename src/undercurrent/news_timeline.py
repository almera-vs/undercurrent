"""What was published about a stock around its move: filings and headlines, newest first.

A terminal-style news timeline for the session's big movers (beyond ±``reactions.BIG`` σ,
liquid or tradable). It makes **no claim about cause**: it lists what was published about
the company from the previous session's close to the time of the run, each item with its
time, its source and a link, and marks what came after the close. A stock with nothing in
the window says so, which is itself worth knowing.

Why a timeline and not a cause: measured on a year of liquid stocks' 3σ moves, a cause
could be named for only 48 % of them, and the named causes were right 81–88 % of the time
(2026-11-24; docs/DECISIONS.md, News); both gates (80 % / 90 %) failed.

Sources, as in ``news``: Bankier and BiznesRadar company pages (the first page only: the
window is one session), Google News only for a mover the pages said nothing about, and the
exchange filings already in the lake. A source that is down is skipped for the night and
named in the run notes; the timeline is built from the others.

Lake: bronze ``news`` (by publication day, keyed by ``item_id``) and state ``news_symbols``
(each portal's code for a company).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from undercurrent import gpw_calendar, lake, news, reactions
from undercurrent import news_common as common
from undercurrent.classify import normalize
from undercurrent.fetch import PoliteClient
from undercurrent.news import WARSAW, Company, NewsItem, Outcome

log = logging.getLogger(__name__)

TABLE = "news"
SYMBOLS = "news_symbols"
MOVER_Z = reactions.BIG
MOVER_TIERS = reactions.TIERS
MAX_MOVERS = 40  # a night with more is a market-wide day: the biggest are enough
MAX_ITEMS = 15  # per mover; which go first beyond it: ``timeline``
LOOKBACK_DAYS = 3  # bronze partitions read around the session (weekends, late items)
DOWN_AFTER = 3  # a source that fails this many companies in a row is skipped for the night
DOWN_OUTCOMES = frozenset({Outcome.BLOCKED, Outcome.UNREACHABLE, Outcome.RATE_LIMITED})
RECHECK_DAYS = 7  # a portal symbol lookup that failed is tried again after this long


@dataclass(frozen=True)
class Mover:
    """A stock that moved beyond ±``MOVER_Z`` σ against the market in the session."""

    isin: str
    z: float
    resid: float | None  # the move against the market, as a return
    tier: str


@dataclass
class CollectResult:
    """What one night's collection fetched, and which sources were down."""

    items: int = 0
    down: dict[str, str] = field(default_factory=dict)  # source → reason
    outcomes: dict[str, int] = field(default_factory=dict)  # "source:outcome" → count


# ---- movers -----------------------------------------------------------------------------


def movers(state: pl.DataFrame, day: date, limit: int = MAX_MOVERS) -> list[Mover]:
    """The session's big movers from the market state, biggest first."""
    df = (
        state.filter(
            pl.col("trade_date") == day,
            pl.col("liquidity_tier").is_in(list(MOVER_TIERS)),
            pl.col("surprise").is_not_null(),
            pl.col("surprise").is_not_nan(),
            pl.col("surprise").abs() >= MOVER_Z,
        )
        .with_columns(a=pl.col("surprise").abs())
        .sort("a", "isin", descending=[True, False])
        .head(limit)
    )
    return [
        Mover(r["isin"], float(r["surprise"]), r.get("resid"), r["liquidity_tier"])
        for r in df.iter_rows(named=True)
    ]


def window(day: date) -> tuple[datetime, datetime]:
    """From the previous session's close to this session's close (UTC)."""
    return common.close_at(common.previous_session(day)), common.close_at(day)


def next_open(day: date) -> datetime:
    """09:00 Warsaw of the session after ``day``: where the timeline's "after the close" ends."""
    d = day + timedelta(days=1)
    while not gpw_calendar.is_session(d):
        d += timedelta(days=1)
    return datetime.combine(d, time(9, 0), tzinfo=WARSAW).astimezone(UTC)


# ---- companies --------------------------------------------------------------------------


def companies(duckdb_file: Path, symbols: pl.DataFrame | None) -> dict[str, common.CompanyInfo]:
    """Every listed company's names, with the portal names already resolved."""
    import duckdb

    con = duckdb.connect(str(duckdb_file), read_only=True)
    try:
        short = con.sql(
            "select isin, arg_max(name, trade_date) as name from ("
            " select isin, name, trade_date from bronze_prices_gpw"
            " union all select isin, name, trade_date from bronze_prices_nc"
            ") where name is not null group by isin"
        ).pl()
        issuer = con.sql(
            "select listed_isin as isin, arg_max(issuer_name, published_at) as issuer"
            " from reports where listed_isin is not null group by listed_isin"
        ).pl()
    finally:
        con.close()
    issuers = dict(zip(issuer["isin"], issuer["issuer"], strict=True))
    portal: dict[str, list[str]] = {}
    if symbols is not None:
        for r in symbols.filter(pl.col("page_name").is_not_null()).iter_rows(named=True):
            portal.setdefault(r["isin"], []).append(r["page_name"])
    return {
        isin: common.CompanyInfo(
            isin, name, common.plain_name(issuers.get(isin)), tuple(portal.get(isin, ()))
        )
        for isin, name in short.iter_rows()
    }


# ---- collection -------------------------------------------------------------------------


class _Symbols:
    """``state/news_symbols`` in the lake: each portal's code for a company."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.df = lake.read_state(data_dir, SYMBOLS)
        self.dirty = False

    def get(self, isin: str, source: str) -> dict[str, Any] | None:
        if self.df is None:
            return None
        hit = self.df.filter((pl.col("isin") == isin) & (pl.col("source") == source))
        return hit.row(0, named=True) if hit.height else None

    def set(self, row: dict[str, Any]) -> None:
        new = pl.DataFrame([row], schema=_SYMBOL_SCHEMA)
        old = self.df
        if old is not None:
            old = old.filter(
                ~((pl.col("isin") == row["isin"]) & (pl.col("source") == row["source"]))
            )
            new = pl.concat([old.select(list(_SYMBOL_SCHEMA)), new])
        self.df = new
        self.dirty = True

    def save(self) -> None:
        if self.dirty and self.df is not None:
            lake.write_state(self.data_dir, SYMBOLS, self.df)


_SYMBOL_SCHEMA: dict[str, Any] = {
    "isin": pl.String,
    "source": pl.String,
    "symbol": pl.String,
    "page_name": pl.String,
    "outcome": pl.String,
    "checked_at": pl.Datetime("us", "UTC"),
}


def _look_up(
    client: PoliteClient,
    source: str,
    info: common.CompanyInfo,
    symbols: _Symbols,
    now: Callable[[], datetime],
) -> news.PageResult | None:
    """Find the portal's symbol for the company and store it; a result only when it is down."""
    outcome, found, detail = news.resolve_symbol(
        client, source, info.company(), info.short_name, throttle=None
    )
    if outcome in DOWN_OUTCOMES or outcome is Outcome.DRIFT:
        return news.PageResult(outcome, [], None, detail)
    old = symbols.get(info.isin, source)
    if outcome is not Outcome.OK and old is not None and old["outcome"] == "ok":
        return None  # a failed lookup does not replace a symbol that worked
    symbols.set(
        {
            "isin": info.isin,
            "source": source,
            "symbol": found.symbol if found else None,
            "page_name": found.page_name if found else None,
            "outcome": "ok" if outcome is Outcome.OK else str(outcome),
            "checked_at": now(),
        }
    )
    return None


def _due(sym: dict[str, Any] | None, at: datetime) -> bool:
    """Whether a company's symbol is to be looked up: never was, or failed long enough ago."""
    if sym is None:
        return True
    return bool(sym["outcome"] != "ok" and sym["checked_at"] <= at - timedelta(days=RECHECK_DAYS))


def _page_items(
    client: PoliteClient,
    source: str,
    info: common.CompanyInfo,
    symbols: _Symbols,
    since: datetime,
    until: datetime,
    now: Callable[[], datetime],
) -> news.PageResult:
    """The first page of a company's news on one portal.

    The portal's symbol is looked up once and stored. A lookup that failed (an HTTP error,
    or no page for the company) is tried again after ``RECHECK_DAYS``; a stored symbol whose
    page fails or shows another company is looked up again at once, since portals move
    companies to new codes.
    """
    sym = symbols.get(info.isin, source)
    fresh = False
    if _due(sym, now()):
        down = _look_up(client, source, info, symbols, now)
        if down is not None:
            return down
        sym, fresh = symbols.get(info.isin, source), True
    for attempt in range(2):
        if not sym or not sym["symbol"]:
            return news.PageResult(Outcome.EMPTY, [], None, "no symbol")
        page = news.fetch_company(
            client,
            source,
            info.company(),
            since=since,
            until=until,
            max_pages=1,
            symbol=sym["symbol"],
            now=now,
        )
        moved = page.outcome is Outcome.HTTP_ERROR or page.detail == news.NOT_THIS_COMPANY
        if fresh or attempt or not moved:
            return page
        down = _look_up(client, source, info, symbols, now)
        if down is not None:
            return down
        old, sym = sym["symbol"], symbols.get(info.isin, source)
        if sym and sym["symbol"] == old:
            return page  # same code: the page itself is failing tonight
    return page


def collect(
    data_dir: Path,
    day: date,
    moved: Sequence[Mover],
    infos: dict[str, common.CompanyInfo],
    names: common.NameIndex,
    *,
    make_client: Callable[[str], PoliteClient],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CollectResult:
    """Fetch the movers' news since the previous close into bronze ``news``.

    Pages first; Google only for a mover whose pages had no headline about it in the window.
    A source that fails ``DOWN_AFTER`` companies in a row is left out for the rest of the
    night and named in ``down``; the others carry on.
    """
    since, _ = window(day)
    until = now()
    res = CollectResult()
    symbols = _Symbols(data_dir)
    found: dict[str, list[NewsItem]] = {m.isin: [] for m in moved}
    clients = {s: make_client(s) for s in (*common.PAGE_SOURCES, news.GOOGLE)}
    try:
        for source in common.PAGE_SOURCES:
            in_a_row = 0
            for m in moved:
                info = infos.get(m.isin)
                if info is None:
                    continue
                page = _page_items(clients[source], source, info, symbols, since, until, now)
                key = f"{source}:{page.outcome}"
                res.outcomes[key] = res.outcomes.get(key, 0) + 1
                found[m.isin].extend(page.items)
                in_a_row = in_a_row + 1 if page.outcome in DOWN_OUTCOMES else 0
                if page.outcome is Outcome.BLOCKED or in_a_row >= DOWN_AFTER:
                    res.down[source] = f"{page.outcome}: {page.detail}"[:200]
                    log.warning("%s left out tonight: %s", source, res.down[source])
                    break
        in_a_row = 0
        for m in moved:
            info = infos.get(m.isin)
            if info is None or news.GOOGLE in res.down:
                continue
            company = info.company()
            if any(about_company(it, company, names) for it in found[m.isin]):
                continue  # the pages already said something about it
            page = news.fetch_google(
                clients[news.GOOGLE], company, since=since, until=until, now=now
            )
            key = f"{news.GOOGLE}:{page.outcome}"
            res.outcomes[key] = res.outcomes.get(key, 0) + 1
            found[m.isin].extend(page.items)
            in_a_row = in_a_row + 1 if page.outcome in DOWN_OUTCOMES else 0
            if page.outcome is Outcome.BLOCKED or in_a_row >= DOWN_AFTER:
                res.down[news.GOOGLE] = f"{page.outcome}: {page.detail}"[:200]
                log.warning("google left out tonight: %s", res.down[news.GOOGLE])
    finally:
        for c in clients.values():
            c.close()
        symbols.save()
    items = news.unique(it for lst in found.values() for it in lst)
    res.items = len(items)
    if items:
        df = common.items_frame(items).with_columns(
            day=pl.col("published_at").dt.convert_time_zone(str(WARSAW)).dt.date()
        )
        for (d,), part in df.group_by("day"):
            lake.append_partition(data_dir, "bronze", TABLE, d, part.drop("day"), key="item_id")
    return res


def about_company(item: NewsItem, company: Company, names: common.NameIndex) -> bool:
    """The headline is about the company (``news.verdict`` without the event test)."""
    v = news.verdict(item, company, names.others(item.title, company.isin), require_event=False)
    return v.counts


# ---- timeline ---------------------------------------------------------------------------


def stored_items(data_dir: Path, day: date) -> pl.DataFrame:
    """Bronze ``news`` around the session: the window can reach back over a weekend."""
    parts = []
    for k in range(-LOOKBACK_DAYS, 2):
        df = lake.read_partition(data_dir, "bronze", TABLE, day + timedelta(days=k))
        if df is not None and df.height:
            parts.append(df.select(list(common.ITEM_SCHEMA)))
    if not parts:
        return pl.DataFrame(schema=common.ITEM_SCHEMA)
    return pl.concat(parts).unique(subset=["item_id"], keep="last")


def filings(duckdb_file: Path, isins: Iterable[str], since: datetime) -> pl.DataFrame:
    """The movers' ESPI/EBI filings known since ``since``."""
    import duckdb

    con = duckdb.connect(str(duckdb_file), read_only=True)
    try:
        df: pl.DataFrame = con.sql(
            "select report_id, listed_isin as isin, system, kind, signal_type, subtype, title,"
            " subject, attachments_json, source_url, known_at"
            " from reports where listed_isin in (select unnest($1)) and known_at >= $2",
            params=[list(isins), since],
        ).pl()
        return df
    finally:
        con.close()


_SPACE = re.compile(r"[^a-z0-9]+")


def _story_key(title: str) -> str:
    """Near-duplicate key: the same story on two portals has the same leading words."""
    return " ".join(_SPACE.sub(" ", normalize(title)).split()[:10])


# One outlet re-titles a story as it develops ("Gwarancja do 550 mln USD", then "... mln
# dol."; "Kurs lekko w górę", then "Kurs się waha"). Two of its headlines are one story when
# they share this share of their words, or the shorter one's words nearly all sit in the
# longer one ("Kwartalne wyniki X bez niespodzianek" in "... bez dużych niespodzianek").
RETITLE_SHARE = 0.6
RETITLE_INSIDE = 0.8
RETITLE_MIN_WORDS = 4


def _words(title: str) -> frozenset[str]:
    return frozenset(_SPACE.sub(" ", normalize(title)).split())


def _retitled(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether two headlines of one outlet are the same story re-titled."""
    shared = len(a & b)
    shorter = min(len(a), len(b))
    if shorter < RETITLE_MIN_WORDS:
        return False
    return shared / len(a | b) >= RETITLE_SHARE or shared / shorter >= RETITLE_INSIDE


def _extends(new: frozenset[str], old: frozenset[str]) -> bool:
    """Whether a re-titled headline keeps the old one's words and adds to them."""
    return len(new) > len(old) and len(new & old) / len(old) >= RETITLE_INSIDE


def timeline(
    mover: Mover,
    info: common.CompanyInfo,
    items: Sequence[NewsItem],
    reports: pl.DataFrame,
    names: common.NameIndex,
    day: date,
    *,
    google_hosts: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """The mover's filings and headlines from the previous close on, newest first.

    Each entry: ``id``, ``kind`` (filing / headline), ``at`` (UTC), ``precision`` (minute /
    day), ``source`` (ESPI, EBI or the outlet), ``title``, ``url``, ``after_close``,
    ``routine`` (a filing that does not move prices, per ``report_rank``) and ``event`` (a
    filing that can move prices, or a headline naming a price-moving event,
    ``news.names_event``).

    A headline is left out when it is not about the company, when it repeats a story
    already listed (on another portal, or re-titled by the same outlet), and, from Google
    News, when it names no event.

    Beyond ``MAX_ITEMS`` the items least likely to bear on the move go first: routine
    filings, then what came after the close, then the latest headlines of the session. The
    session's own filings stay, as do its earliest headlines, which came before most of the
    move.
    """
    since, close = window(day)
    end = next_open(day)
    company = info.company(google_hosts)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in reports.filter(pl.col("isin") == mover.isin).sort("known_at").iter_rows(named=True):
        at = r["known_at"]
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        if at <= since or at > end:
            continue
        title = r["title"] or r["subject"] or kind_title(r["kind"])
        rank = common.report_rank(
            r["kind"], r["signal_type"], r["subtype"], r["title"], r["attachments_json"]
        )
        out.append(
            {
                "id": f"report:{r['report_id']}",
                "kind": "filing",
                "at": at,
                "precision": "minute",
                "source": r["system"] or "ESPI",
                "title": title,
                "url": r["source_url"],
                "after_close": at > close,
                "routine": rank is None,
                "event": rank is not None,
            }
        )
        seen.add(_story_key(title))
    rank_src = {news.BANKIER: 0, news.BIZNESRADAR: 1, news.GOOGLE: 2}
    heads = sorted(
        (i for i in items if i.isin == mover.isin),
        key=lambda i: (i.precision != "minute", rank_src.get(i.source, 9), i.published_at),
    )
    by_outlet: dict[str, list[tuple[frozenset[str], int]]] = {}  # words, index in ``out``
    for it in heads:
        if it.precision == "minute" and not since < it.published_at <= end:
            continue
        if it.precision == "day" and common.session_of(it.published_at, "day") != day:
            continue
        if not about_company(it, company, names):
            continue
        # Google is asked only about stocks the portals said nothing about, where most of
        # what names the company is something else: a namesake (the Dutch Action for
        # ACTION), a sponsorship, a webinar. Only its headlines that name an event stay.
        if it.source == news.GOOGLE and not news.names_event(it.title):
            continue
        key = _story_key(it.title)
        if key in seen:
            continue
        seen.add(key)
        entry = {
            "id": common.item_id(it.source, it.url),
            "kind": "headline",
            "at": it.published_at,
            "precision": it.precision,
            "source": it.outlet or it.source,
            "title": it.title,
            "url": it.url,
            "after_close": it.precision == "minute" and it.published_at > close,
            "routine": False,
            "event": news.names_event(it.title),
        }
        words = _words(it.title)
        versions = by_outlet.setdefault(normalize(it.outlet or it.source), [])
        k = next((k for k, (w, _i) in enumerate(versions) if _retitled(words, w)), None)
        if k is None:
            versions.append((words, len(out)))
            out.append(entry)
        elif _extends(words, versions[k][0]):
            # a later version that adds to the first ("Akcje Żabki ostro w górę", then
            # "... Właściciel 7-Eleven chce kupić akcje") replaces it, with its own time
            versions[k] = (words, versions[k][1])
            out[versions[k][1]] = entry
        # otherwise a re-wording: the outlet's first version stays, the one before most of
        # the move
    kept = sorted(out, key=_keep_order)[:MAX_ITEMS]
    return sorted(kept, key=lambda e: e["at"], reverse=True)


def _keep_order(e: dict[str, Any]) -> tuple[int, datetime]:
    """Which items a full timeline keeps first: see ``timeline``."""
    if e["routine"]:
        tier = 3
    elif e["after_close"]:
        tier = 2
    else:
        tier = 0 if e["kind"] == "filing" else 1
    return tier, e["at"]


def kind_title(kind: str | None) -> str:
    """A title for a filing the exchange left untitled: its kind (``Raport kwartalny``)."""
    k = (kind or "").strip()
    return f"Raport {k.lower()}" if k else "(bez tytułu)"


def local_time(at: datetime, precision: str) -> tuple[str, str]:
    """(``HH:MM`` or ``""`` for a date-only item, ``DD.MM``) in Warsaw time."""
    loc = at.astimezone(WARSAW)
    return ("" if precision != "minute" else f"{loc:%H:%M}", f"{loc:%d.%m}")


def build(
    data_dir: Path,
    duckdb_file: Path,
    state: pl.DataFrame,
    day: date,
    *,
    infos: dict[str, common.CompanyInfo] | None = None,
) -> list[dict[str, Any]]:
    """Every mover of ``day`` with its timeline, for the site and the digest."""
    moved = movers(state, day)
    if not moved:
        return []
    infos = infos or companies(duckdb_file, lake.read_state(data_dir, SYMBOLS))
    names = common.NameIndex({i: c.company() for i, c in infos.items()})
    since, _ = window(day)
    items = common.frame_items(stored_items(data_dir, day))
    reps = filings(duckdb_file, [m.isin for m in moved], since)
    out = []
    for m in moved:
        info = infos.get(m.isin)
        if info is None:
            continue
        mine = [i for i in items if i.isin == m.isin]
        tl = timeline(m, info, mine, reps, names, day, google_hosts=common.own_hosts(mine, info))
        for e in tl:
            e["time"], e["date"] = local_time(e["at"], e["precision"])
        out.append(
            {
                "isin": m.isin,
                "ticker": info.short_name,
                "name": info.issuer_name or info.short_name,
                "z": m.z,
                "move": m.resid,
                "tier": m.tier,
                "timeline": tl,
            }
        )
    return out


# ---- the nightly step -------------------------------------------------------------------


def news_client(source: str) -> PoliteClient:
    """One client per source: no pause on the portals' pages, Google spaced out."""
    interval = common.GOOGLE_INTERVAL_S if source == news.GOOGLE else common.PAGE_INTERVAL_S
    return PoliteClient(
        min_interval_s=interval, max_jitter_s=common.JITTER_S, timeout_s=20.0, retry_pauses_s=(5.0,)
    )


def run(
    data_dir: Path,
    work_dir: Path,
    day: date,
    *,
    make_client: Callable[[str], PoliteClient] = news_client,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    """Collect the session's movers' news into bronze ``news``. Returns run notes."""
    from undercurrent import transform

    notes: list[str] = []
    path = work_dir / "gold" / "market_state.parquet"
    if not path.exists():
        return notes
    state = pl.read_parquet(
        path, columns=["isin", "trade_date", "surprise", "resid", "liquidity_tier"]
    )
    moved = movers(state, day)
    if not moved:
        return notes
    duck = transform.duckdb_path(work_dir)
    infos = companies(duck, lake.read_state(data_dir, SYMBOLS))
    names = common.NameIndex({i: c.company() for i, c in infos.items()})
    got = collect(data_dir, day, moved, infos, names, make_client=make_client, now=now)
    notes += [f"news: {s} down tonight" for s in sorted(got.down)]
    log.info("news: %d items for %d movers (%s)", got.items, len(moved), got.outcomes)
    return notes
