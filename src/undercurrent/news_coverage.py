"""Can a big move's cause be found? The deploy gate for the news feature (Phase 1).

For every stock-day with ``|surprise| >= 2`` in the window (all liquidity tiers), causes are
looked for in this order, and the first one found is the move's cause:

1. **report**: an ESPI/EBI report (``events``) whose session is the move's session;
2. **news**: a headline from BiznesRadar, Bankier or Google News that passes
   ``news.verdict`` and whose session is the move's session. *Strong* when it has a time
   of day, *weak* when the source gives only a date;
3. **sector**: the stock's WIG sector index moved the same way by at least ``SECTOR_Z`` of
   its own σ of excess return over WIG, and that excess is at least ``SECTOR_SHARE`` of the
   stock's residual return. Always *weak*: it says the move was the sector's, not why;
4. **none**.

A timestamp's session: before 17:05 Warsaw it belongs to that day, otherwise to the next,
rolled forward to the next GPW session (the rule ``reactions`` uses for reports). News after
the close therefore counts for the next session, never for the move it follows. A date-only
headline belongs to its date's session.

``surprise`` is the residual against WIG and the bond index only (``market_state.FACTORS``),
so a sector move is still in it and can be a separate cause.

Gate (user's decision, 2026-11-23): coverage of at least 80 % of ``|z| >= 3`` moves on
liquid stocks, and precision of at least 90 % on a hand-check of a stratified random sample
of the causes found. Moves whose news has not been fetched yet are reported apart and never
counted as "none".

Pacing (user's decision, 2026-11-23): no pause between requests to BiznesRadar and Bankier
(``PAGE_INTERVAL_S``), and ``GOOGLE_INTERVAL_S`` between Google News queries, which bans
quickly. ``WORKERS`` per host (8, 8 and 2, as in the plan); 429 and 503 wait for
``Retry-After`` and halve that host's workers; any block page stops that host.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import queue
import random
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import polars as pl

from undercurrent import lake, news
from undercurrent.fetch import PoliteClient
from undercurrent.news import Company, NewsItem, Outcome
from undercurrent.news_common import (
    PAGE_SOURCES,
    CompanyInfo,
    NameIndex,
    close_at,
    frame_items,
    items_frame,
    own_hosts,
    plain_name,
    previous_session,
    report_rank,
    session_of,
)

log = logging.getLogger(__name__)

MIN_Z = 2.0
GATE_TIER = "liquid"
GATE_Z = 3.0
GATE_COVERAGE = 0.80
GATE_PRECISION = 0.90
SECTOR_Z = 2.0
SECTOR_SHARE = 0.5
SECTOR_SIGMA_WINDOW = 60
SECTOR_SIGMA_MIN = 20
# A sector explains a stock's move only when the sector moved, not just the stock inside its
# own index (KGHM is most of WIG-GORNIC): at least SECTOR_PEER_SHARE of its other members, and
# no fewer than SECTOR_PEERS_MIN of them, moved the same way by SECTOR_PEER_Z.
SECTOR_PEER_Z = 1.0
SECTOR_PEER_SHARE = 0.25
SECTOR_PEERS_MIN = 3
# ... and at least this many of them, so two subsidiaries of the same group (Grupa Azoty's in
# WIG-CHEMIA) or one coal miner beside KGHM do not make a sector move (round 2, 2026-11-24).
SECTOR_PEERS_SAME_MIN = 3
# a fetch that ended like this is not done: the next run tries it again
RETRY_OUTCOMES = frozenset(
    {str(Outcome.RATE_LIMITED), str(Outcome.UNREACHABLE), str(Outcome.BLOCKED)}
)
# a host whose last few jobs all failed to connect is treated as refusing us: stop it
MAX_UNREACHABLE_IN_A_ROW = 3
# workers per host, as in the plan; a 429 or 503 halves them
WORKERS: dict[str, int] = {"biznesradar": 8, "bankier": 8, "google": 2}
MAX_PAGES = 60
SECTOR_SEED = Path("seeds/sector_index.csv")


ATTR_SCHEMA: dict[str, Any] = {
    "isin": pl.String,
    "short_name": pl.String,
    "trade_date": pl.Date,
    "market": pl.String,
    "liquidity_tier": pl.String,
    "surprise": pl.Float64,
    "resid": pl.Float64,
    "band": pl.String,
    "cause": pl.String,
    "source": pl.String,
    "strength": pl.String,
    "kind": pl.String,
    "title": pl.String,
    "outlet": pl.String,
    "url": pl.String,
    "at": pl.Datetime("us", "UTC"),
    "google_queried": pl.Boolean,
    "missing_sources": pl.String,  # page sources never fetched for this company
}


# ---- sessions ---------------------------------------------------------------------------


def news_window(d: date) -> tuple[datetime, datetime]:
    """Headlines that can explain session ``d``: after the previous close, up to this close."""
    return close_at(previous_session(d)), close_at(d)


def z_band(z: float) -> str:
    """``|z| >= 3`` or ``2 <= |z| < 3``."""
    return "z3" if abs(z) >= GATE_Z else "z2"


# ---- lake inputs ------------------------------------------------------------------------


def load_moves(state: pl.DataFrame, since: date, until: date, min_z: float = MIN_Z) -> pl.DataFrame:
    """Stock-days with ``|surprise| >= min_z`` between ``since`` and ``until`` (inclusive)."""
    return (
        state.filter(
            pl.col("trade_date").is_between(since, until),
            pl.col("surprise").abs() >= min_z,
            pl.col("liquidity_tier").is_not_null(),
        )
        .select("isin", "trade_date", "market", "liquidity_tier", "surprise", "resid")
        .sort("trade_date", "isin")
    )


def report_sessions(events: pl.DataFrame) -> dict[tuple[str, date], list[dict[str, Any]]]:
    """Reports that can explain a move, by (ISIN, session); best rank, then earliest, first.

    The session is the one each report's ``known_at`` falls in. A frame without the
    classification columns counts every report (rank 0).
    """
    classified = "signal_type" in events.columns
    out: dict[tuple[str, date], list[dict[str, Any]]] = {}
    for r in events.filter(pl.col("isin").is_not_null()).iter_rows(named=True):
        known = r["known_at"]
        if known is None:
            continue
        rank = (
            report_rank(
                r.get("kind"),
                r.get("signal_type"),
                r.get("subtype"),
                r.get("title"),
                r.get("attachments_json"),
            )
            if classified
            else 0
        )
        if rank is None:
            continue
        known = known.replace(tzinfo=UTC) if known.tzinfo is None else known.astimezone(UTC)
        out.setdefault((r["isin"], session_of(known)), []).append(
            {
                "title": r.get("title") or "",
                "url": r.get("source_url") or "",
                "at": known,
                "rank": rank,
            }
        )
    for lst in out.values():
        lst.sort(key=lambda x: (x["rank"], x["at"]))
    return out


def sector_of(indicators: pl.DataFrame | None, seed: pl.DataFrame) -> dict[str, str]:
    """ISIN → WIG sector index, through the GPW sector code (latest row per ISIN)."""
    if indicators is None or indicators.height == 0:
        return {}
    latest = (
        indicators.filter(pl.col("sector_code").is_not_null())
        .sort("trade_date")
        .unique(subset=["isin"], keep="last")
        .select("isin", pl.col("sector_code").cast(pl.Int64))
    )
    j = latest.join(
        seed.select(pl.col("sector_code").cast(pl.Int64), "index_name"), on="sector_code"
    )
    return dict(zip(j["isin"], j["index_name"], strict=True))


def sector_moves(
    indices: pl.DataFrame, names: Iterable[str]
) -> dict[tuple[str, date], tuple[float, float]]:
    """(index, session) → (excess return over WIG, its z against the trailing σ).

    σ is the standard deviation of the index's excess over the previous ``SECTOR_SIGMA_WINDOW``
    sessions (at least ``SECTOR_SIGMA_MIN``), so a session never scales itself.
    """
    wanted = sorted(set(names))
    df = (
        indices.filter(pl.col("name").is_in([*wanted, "WIG"]))
        .select("name", "trade_date", "close")
        .unique(subset=["name", "trade_date"], keep="last")
        .sort("name", "trade_date")
        .with_columns(ret=pl.col("close") / pl.col("close").shift(1).over("name") - 1)
    )
    wig = df.filter(pl.col("name") == "WIG").select("trade_date", wig=pl.col("ret"))
    sec = (
        df.filter(pl.col("name") != "WIG")
        .join(wig, on="trade_date", how="inner")
        .with_columns(excess=pl.col("ret") - pl.col("wig"))
        .with_columns(
            sigma=pl.col("excess")
            .shift(1)
            .rolling_std(SECTOR_SIGMA_WINDOW, min_samples=SECTOR_SIGMA_MIN)
            .over("name")
        )
        .filter(pl.col("excess").is_not_null(), pl.col("sigma") > 0)
        .with_columns(z=pl.col("excess") / pl.col("sigma"))
    )
    return {
        (n, d): (e, z) for n, d, e, z in sec.select("name", "trade_date", "excess", "z").iter_rows()
    }


def sector_peers(
    state: pl.DataFrame, sector_index: dict[str, str], since: date, until: date
) -> dict[tuple[str, date], list[tuple[str, float]]]:
    """(index, session) → every member's (ISIN, surprise) that session, any liquidity tier."""
    df = state.filter(
        pl.col("trade_date").is_between(since, until),
        pl.col("surprise").is_not_null(),
        pl.col("surprise").is_not_nan(),
        pl.col("isin").is_in(list(sector_index)),
    ).select("isin", "trade_date", "surprise")
    out: dict[tuple[str, date], list[tuple[str, float]]] = {}
    for isin, day, z in df.iter_rows():
        out.setdefault((sector_index[isin], day), []).append((isin, z))
    return out


def sector_is_broad(peers: Sequence[tuple[str, float]], isin: str, surprise: float) -> bool:
    """Enough of the sector's other members moved the stock's way (``SECTOR_PEER_*``)."""
    others = [z for i, z in peers if i != isin]
    if len(others) < SECTOR_PEERS_MIN:
        return False
    sign = 1.0 if surprise > 0 else -1.0
    same = sum(1 for z in others if z * sign >= SECTOR_PEER_Z)
    return same >= SECTOR_PEERS_SAME_MIN and same / len(others) >= SECTOR_PEER_SHARE


def sector_explains(surprise: float, resid: float | None, excess: float, z: float) -> bool:
    """The sector moved the stock's way by ``SECTOR_Z`` and carries ``SECTOR_SHARE`` of it."""
    if resid is None or resid == 0 or math.isnan(resid):
        return False
    same_way = (surprise > 0) == (z > 0) == (excess > 0)
    return same_way and abs(z) >= SECTOR_Z and excess / resid >= SECTOR_SHARE


# ---- raw news store ---------------------------------------------------------------------


class Store:
    """Fetched headlines and what has been fetched, under ``root`` (a scratch lake).

    Raw items are kept per source and ISIN while collecting (``raw/{source}/{isin}.parquet``)
    and written to the bronze ``news`` table, partitioned by publication day, by
    :meth:`publish_bronze`. ``state/news_fetched`` records each (source, ISIN, window) for
    the page sources and each (ISIN, session) for Google, so a stopped run resumes.
    """

    FETCHED: ClassVar[dict[str, Any]] = {
        "source": pl.String,
        "isin": pl.String,
        "trade_date": pl.Date,  # Google: the move's session; page sources: null
        "since": pl.Datetime("us", "UTC"),
        "until": pl.Datetime("us", "UTC"),
        "outcome": pl.String,
        "detail": pl.String,
        "n_items": pl.Int64,
        "fetched_at": pl.Datetime("us", "UTC"),
    }
    SOURCES: ClassVar[dict[str, Any]] = {
        "source": pl.String,
        "available": pl.Boolean,
        "reason": pl.String,
        "workers": pl.Int64,
        "checked_at": pl.Datetime("us", "UTC"),
    }
    SYMBOLS: ClassVar[dict[str, Any]] = {
        "isin": pl.String,
        "source": pl.String,
        "symbol": pl.String,
        "page_name": pl.String,
        "outcome": pl.String,
        "checked_at": pl.Datetime("us", "UTC"),
    }

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        fetched = lake.read_state(root, "news_fetched")
        symbols = lake.read_state(root, "news_symbols")
        sources = lake.read_state(root, "news_sources")
        self._sources: pl.DataFrame = (
            sources if sources is not None else pl.DataFrame(schema=self.SOURCES)
        )
        self._fetched: pl.DataFrame = (
            fetched if fetched is not None else pl.DataFrame(schema=self.FETCHED)
        )
        self._symbols: pl.DataFrame = (
            symbols if symbols is not None else pl.DataFrame(schema=self.SYMBOLS)
        )

    def _raw(self, source: str, isin: str) -> Path:
        return self.root / "raw" / source / f"{isin}.parquet"

    def add_items(self, source: str, isin: str, items: Sequence[NewsItem]) -> None:
        """Keep ``items`` for (source, ISIN), one row per item id."""
        if not items:
            return
        with self._lock:
            path = self._raw(source, isin)
            new = items_frame(items)
            if path.exists():
                new = pl.concat([pl.read_parquet(path), new])
            new = new.unique("item_id", keep="first", maintain_order=True)
            lake.write_frame(new.sort("published_at"), path)

    def items(self, isin: str) -> list[NewsItem]:
        """Every stored item for ``isin``, all sources."""
        frames = [
            pl.read_parquet(p)
            for s in (*PAGE_SOURCES, news.GOOGLE)
            if (p := self._raw(s, isin)).exists()
        ]
        return frame_items(pl.concat(frames)) if frames else []

    def mark(self, row: dict[str, Any]) -> None:
        """Record one fetch (outcome included) and save the state."""
        with self._lock:
            self._fetched = pl.concat([self._fetched, pl.DataFrame([row], schema=self.FETCHED)])
            lake.write_state(self.root, "news_fetched", self._fetched)

    def fetched(self) -> pl.DataFrame:
        """All recorded fetches."""
        with self._lock:
            return self._fetched.clone()

    def symbol(self, isin: str, source: str) -> dict[str, Any] | None:
        """The stored symbol check for (ISIN, source), if any."""
        with self._lock:
            hit = self._symbols.filter(pl.col("isin") == isin, pl.col("source") == source)
        return hit.row(-1, named=True) if hit.height else None

    def set_symbol(self, row: dict[str, Any]) -> None:
        """Store one symbol check and save the state."""
        with self._lock:
            keep = self._symbols.filter(
                ~((pl.col("isin") == row["isin"]) & (pl.col("source") == row["source"]))
            )
            self._symbols = pl.concat([keep, pl.DataFrame([row], schema=self.SYMBOLS)])
            lake.write_state(self.root, "news_symbols", self._symbols)

    def set_source(self, source: str, res: PoolResult, at: datetime) -> None:
        """Record whether ``source`` was available on its last run, and save the state."""
        row = {
            "source": source,
            "available": not res.stopped,
            "reason": res.reason,
            "workers": res.limit,
            "checked_at": at,
        }
        with self._lock:
            keep = self._sources.filter(pl.col("source") != source)
            self._sources = pl.concat([keep, pl.DataFrame([row], schema=self.SOURCES)])
            lake.write_state(self.root, "news_sources", self._sources)

    def sources(self) -> pl.DataFrame:
        """Each source's availability on its last run."""
        with self._lock:
            return self._sources.clone()

    def unavailable(self) -> set[str]:
        """Sources whose last run stopped: blocked or not answering."""
        with self._lock:
            return set(self._sources.filter(~pl.col("available"))["source"].to_list())

    def publish_bronze(self) -> int:
        """Write every raw item to bronze ``news``, partitioned by Warsaw publication day."""
        paths = sorted((self.root / "raw").glob("*/*.parquet"))
        if not paths:
            return 0
        allrows = pl.concat([pl.read_parquet(p) for p in paths]).unique("item_id", keep="first")
        allrows = allrows.with_columns(
            day=pl.col("published_at").dt.convert_time_zone("Europe/Warsaw").dt.date()
        )
        n = 0
        for (day,), part in allrows.group_by("day"):
            assert isinstance(day, date)
            if lake.append_partition(
                self.root, "bronze", "news", day, part.drop("day"), key="item_id"
            ):
                n += part.height
        return n


# ---- collection -------------------------------------------------------------------------


@dataclass
class Plan:
    """What to fetch: companies with moves, and the moves themselves."""

    companies: dict[str, CompanyInfo]
    moves: pl.DataFrame
    since: datetime
    until: datetime
    order: list[str] = field(default_factory=list)  # ISINs, gate population first


def plan(companies: dict[str, CompanyInfo], moves: pl.DataFrame) -> Plan:
    """Companies with moves, gate population (liquid, |z| >= 3) first, then by move count."""
    first, last = moves["trade_date"].min(), moves["trade_date"].max()
    assert isinstance(first, date) and isinstance(last, date)
    since, _ = news_window(first)
    until = close_at(last) + timedelta(hours=2)
    gate = set(
        moves.filter(pl.col("liquidity_tier") == GATE_TIER, pl.col("surprise").abs() >= GATE_Z)[
            "isin"
        ].to_list()
    )
    counts = moves.group_by("isin").len().sort("len", descending=True)
    order = [i for i in counts["isin"] if i in gate] + [i for i in counts["isin"] if i not in gate]
    order = [i for i in order if i in companies]
    return Plan(companies, moves, since, until, order)


def _page_job(
    store: Store,
    source: str,
    p: Plan,
    client: PoliteClient,
    isin: str,
    throttle: news.HostThrottle,
    now: Callable[[], datetime],
) -> Outcome:
    """Resolve the portal symbol if needed, then walk the company's listing pages."""
    info = p.companies[isin]
    sym = store.symbol(isin, source)
    if sym is None:
        outcome, found, detail = news.resolve_symbol(
            client, source, info.company(), info.short_name, throttle=throttle
        )
        if outcome is Outcome.BLOCKED:
            log.warning("%s blocked: %s", source, detail)
            return outcome
        if outcome in (Outcome.RATE_LIMITED, Outcome.UNREACHABLE, Outcome.DRIFT):
            log.warning("%s symbol for %s: %s (%s); skipped", source, isin, outcome, detail)
            return outcome
        store.set_symbol(
            {
                "isin": isin,
                "source": source,
                "symbol": found.symbol if found else None,
                "page_name": found.page_name if found else None,
                "outcome": str(outcome) if outcome is not Outcome.OK else "ok",
                "checked_at": now(),
            }
        )
        sym = store.symbol(isin, source)
    assert sym is not None
    if not sym["symbol"]:
        store.mark(
            _mark(source, isin, None, p.since, p.until, sym["outcome"], "no symbol", 0, now())
        )
        return Outcome.EMPTY
    res = news.fetch_company(
        client,
        source,
        info.company(),
        since=p.since,
        until=p.until,
        max_pages=MAX_PAGES,
        symbol=sym["symbol"],
        throttle=throttle,
        now=now,
    )
    store.add_items(source, isin, res.items)
    store.mark(
        _mark(
            source,
            isin,
            None,
            p.since,
            p.until,
            str(res.outcome),
            res.detail,
            len(res.items),
            now(),
        )
    )
    if res.outcome is Outcome.BLOCKED:
        log.warning("%s blocked at %s: %s", source, isin, res.detail)
    return res.outcome


@dataclass(frozen=True)
class PoolResult:
    """How a host's pool ended."""

    limit: int  # workers allowed at the end (halved by each 429 or 503)
    stopped: bool  # True when the host was blocked or stopped answering
    reason: str = ""


def run_pool(
    name: str,
    jobs: Sequence[Any],
    work: Callable[[PoliteClient, Any, news.HostThrottle], Outcome],
    *,
    workers: int,
    make_client: Callable[[], PoliteClient],
    stop: threading.Event,
) -> PoolResult:
    """Run ``jobs`` on up to ``workers`` threads for one host, each with its own client.

    A 429 or 503 halves the host's worker limit (``news.HostThrottle``); a worker above the
    limit finishes its current job and leaves, so no job is lost. A block page, or
    ``MAX_UNREACHABLE_IN_A_ROW`` jobs in a row that could not connect, stop the host: no
    worker takes another job, and those jobs are retried on the next run. Returns the final
    worker limit and whether the host stopped, and why.
    """
    q: queue.SimpleQueue[Any] = queue.SimpleQueue()
    for j in jobs:
        q.put(j)
    throttle = news.HostThrottle(workers)
    blocked = threading.Event()
    why: list[str] = []
    done = [0]
    in_a_row = [0]
    lock = threading.Lock()

    def run(k: int) -> None:
        with make_client() as client:
            while not (stop.is_set() or blocked.is_set()) and k < throttle.limit:
                try:
                    job = q.get_nowait()
                except queue.Empty:
                    return
                outcome = work(client, job, throttle)
                if outcome is Outcome.BLOCKED:
                    why.append("block page")
                    blocked.set()
                with lock:
                    done[0] += 1
                    in_a_row[0] = in_a_row[0] + 1 if outcome is Outcome.UNREACHABLE else 0
                    if in_a_row[0] >= MAX_UNREACHABLE_IN_A_ROW and not blocked.is_set():
                        log.warning("%s: %d jobs in a row could not connect", name, in_a_row[0])
                        why.append(f"{in_a_row[0]} jobs in a row could not connect")
                        blocked.set()
                    if done[0] % 25 == 0:
                        log.info(
                            "%s: %d/%d done, %d workers", name, done[0], len(jobs), throttle.limit
                        )

    threads = [threading.Thread(target=run, args=(k,), name=f"{name}-{k}") for k in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if blocked.is_set():
        log.warning("%s stopped: %s", name, why[0] if why else "blocked")
    return PoolResult(throttle.limit, blocked.is_set(), why[0] if why else "")


def _mark(
    source: str,
    isin: str,
    day: date | None,
    since: datetime,
    until: datetime,
    outcome: str,
    detail: str,
    n: int,
    at: datetime,
) -> dict[str, Any]:
    return {
        "source": source,
        "isin": isin,
        "trade_date": day,
        "since": since,
        "until": until,
        "outcome": outcome,
        "detail": detail,
        "n_items": n,
        "fetched_at": at,
    }


def google_todo(
    p: Plan, attributed: pl.DataFrame, fetched: pl.DataFrame, min_z: float
) -> list[tuple[str, date]]:
    """Moves the reports and the pages left unexplained, |z| >= ``min_z``, not yet queried."""
    done = {
        (i, d)
        for i, d, o in fetched.filter(pl.col("source") == news.GOOGLE)
        .select("isin", "trade_date", "outcome")
        .iter_rows()
        if o not in RETRY_OUTCOMES
    }
    rows = attributed.filter(
        pl.col("cause").is_in(["none", "sector"]),
        pl.col("surprise").abs() >= min_z,
        pl.col("isin").is_in(list(p.companies)),
    )
    gate = rows.filter(pl.col("liquidity_tier") == GATE_TIER, pl.col("surprise").abs() >= GATE_Z)
    rest = rows.join(gate.select("isin", "trade_date"), on=["isin", "trade_date"], how="anti")
    ordered = pl.concat([gate, rest]).select("isin", "trade_date").iter_rows()
    return [(i, d) for i, d in ordered if (i, d) not in done]


def _google_job(
    store: Store,
    p: Plan,
    client: PoliteClient,
    job: tuple[str, date],
    throttle: news.HostThrottle,
    now: Callable[[], datetime],
) -> Outcome:
    """One Google News query: the move's news window."""
    isin, day = job
    since, until = news_window(day)
    co = p.companies[isin].company()
    res = news.fetch_google(client, co, since=since, until=until, throttle=throttle, now=now)
    store.add_items(news.GOOGLE, isin, res.items)
    store.mark(
        _mark(
            news.GOOGLE,
            isin,
            day,
            since,
            until,
            str(res.outcome),
            res.detail,
            len(res.items),
            now(),
        )
    )
    if res.outcome is Outcome.BLOCKED:
        log.warning("google blocked: %s", res.detail)
    return res.outcome


def collect(
    store: Store,
    p: Plan,
    *,
    attribute_now: Callable[[], pl.DataFrame],
    google_min_z: float,
    make_client: Callable[[str], PoliteClient],
    workers: dict[str, int] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    stop: threading.Event | None = None,
) -> None:
    """Fetch the page sources for every planned company, then Google for what is left.

    BiznesRadar and Bankier run at the same time, each with its own pool of ``WORKERS``;
    Google follows, because it only asks about moves the pages left unexplained. A source
    that is blocked or stops answering is recorded as unavailable and the run carries on
    without it; the next run tries it again.
    """
    stop = stop or threading.Event()
    n = {**WORKERS, **(workers or {})}
    fetched = store.fetched()

    def pages(source: str) -> None:
        done = {
            i
            for i, o in fetched.filter(pl.col("source") == source, pl.col("trade_date").is_null())
            .select("isin", "outcome")
            .iter_rows()
            if o not in RETRY_OUTCOMES
        }
        todo = [i for i in p.order if i not in done]
        log.info("%s: %d companies to fetch", source, len(todo))
        res = run_pool(
            source,
            todo,
            lambda c, isin, th: _page_job(store, source, p, c, isin, th, now),
            workers=n[source],
            make_client=lambda: make_client(source),
            stop=stop,
        )
        store.set_source(source, res, now())

    hosts = [threading.Thread(target=pages, args=(s,), name=f"news-{s}") for s in PAGE_SOURCES]
    for t in hosts:
        t.start()
    for t in hosts:
        t.join()
    todo = google_todo(p, attribute_now(), store.fetched(), google_min_z)
    log.info("google: %d moves to query", len(todo))
    res = run_pool(
        news.GOOGLE,
        todo,
        lambda c, job, th: _google_job(store, p, c, job, th, now),
        workers=n[news.GOOGLE],
        make_client=lambda: make_client(news.GOOGLE),
        stop=stop,
    )
    store.set_source(news.GOOGLE, res, now())


# ---- attribution ------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """Everything attribution reads, already loaded."""

    moves: pl.DataFrame
    companies: dict[str, CompanyInfo]
    reports: dict[tuple[str, date], list[dict[str, Any]]]
    sector_index: dict[str, str]
    sectors: dict[tuple[str, date], tuple[float, float]]
    peers: dict[tuple[str, date], list[tuple[str, float]]] = field(default_factory=dict)


def news_by_session(
    items: Sequence[NewsItem],
    company: Company,
    names: NameIndex,
) -> dict[date, list[tuple[NewsItem, str]]]:
    """Counting headlines by session, with their verdict reason; best first in each session."""
    out: dict[date, list[tuple[NewsItem, str]]] = {}
    for it in items:
        # every source must name the company: the portals' per-company pages also carry
        # features and market notes that are not about it (smoke run, 2026-11-23)
        v = news.verdict(it, company, names.others(it.title, company.isin))
        if not v.counts:
            continue
        out.setdefault(session_of(it.published_at, it.precision), []).append((it, v.reason))
    rank = {news.BANKIER: 0, news.BIZNESRADAR: 1, news.GOOGLE: 2}
    for lst in out.values():
        lst.sort(
            key=lambda x: (x[0].precision != "minute", rank.get(x[0].source, 9), x[0].published_at)
        )
    return out


def attribute(
    inp: Inputs, store: Store, names: NameIndex, google_min_z: float | None = None
) -> pl.DataFrame:
    """Each move's cause (report, news, sector or none), or ``pending`` while unfetched.

    With ``google_min_z``, a move of at least that size with no cause yet and no Google query
    yet is also pending: Google may still explain it.

    A source the last run found unavailable never makes a move pending: the move is
    measured with the sources that answered, and ``missing_sources`` says which were not
    there for that company, so the report can show how much of the result is partial.
    """
    fetched = store.fetched()
    page_done = {
        (s, i)
        for s, i, o in fetched.filter(pl.col("trade_date").is_null())
        .select("source", "isin", "outcome")
        .iter_rows()
        if o not in RETRY_OUTCOMES
    }
    google_done = {
        (i, d)
        for i, d, o in fetched.filter(pl.col("source") == news.GOOGLE)
        .select("isin", "trade_date", "outcome")
        .iter_rows()
        if o not in RETRY_OUTCOMES
    }
    down = store.unavailable()
    rows: list[dict[str, Any]] = []
    cache: dict[str, dict[date, list[tuple[NewsItem, str]]]] = {}
    for m in inp.moves.iter_rows(named=True):
        isin, day, z = m["isin"], m["trade_date"], m["surprise"]
        info = inp.companies.get(isin)
        row: dict[str, Any] = {
            "isin": isin,
            "short_name": info.short_name if info else None,
            "trade_date": day,
            "market": m["market"],
            "liquidity_tier": m["liquidity_tier"],
            "surprise": z,
            "resid": m["resid"],
            "band": z_band(z),
            "cause": "none",
            "source": None,
            "strength": None,
            "kind": None,
            "title": None,
            "outlet": None,
            "url": None,
            "at": None,
            "google_queried": (isin, day) in google_done,
        }
        reps = inp.reports.get((isin, day))
        if reps:
            r = reps[0]
            row.update(
                cause="report",
                source="report",
                strength="strong",
                kind="report",
                title=r["title"],
                url=r["url"],
                at=r["at"],
            )
            rows.append(row)
            continue
        if info is not None:
            if isin not in cache:
                items = store.items(isin)
                company = info.company(own_hosts(items, info))
                cache[isin] = news_by_session(items, company, names)
            hits = cache[isin].get(day)
            if hits:
                it, _reason = hits[0]
                strong = it.precision == "minute"
                row.update(
                    cause="news",
                    source=it.source,
                    strength="strong" if strong else "weak",
                    kind=f"news_{it.precision}",
                    title=it.title,
                    outlet=it.outlet,
                    url=it.url,
                    at=it.published_at,
                )
                rows.append(row)
                continue
        idx = inp.sector_index.get(isin)
        sec = inp.sectors.get((idx, day)) if idx else None
        if (
            sec is not None
            and sector_explains(z, m["resid"], *sec)
            and sector_is_broad(inp.peers.get((idx or "", day), ()), isin, z)
        ):
            excess, sz = sec
            row.update(
                cause="sector",
                source="sector",
                strength="weak",
                kind="sector",
                title=f"{idx} {sz:+.1f}σ (excess {excess:+.2%})",
            )
        missing = [s for s in PAGE_SOURCES if (s, isin) not in page_done]
        row["missing_sources"] = ",".join(missing)
        # a source that is down does not hold the move back: it is measured without it
        pages_done = not [s for s in missing if s not in down]
        awaiting_google = (
            google_min_z is not None
            and news.GOOGLE not in down
            and row["cause"] == "none"
            and abs(z) >= google_min_z
            and not row["google_queried"]
        )
        if not pages_done or awaiting_google:
            row["cause"] = "pending"
        rows.append(row)
    return pl.DataFrame(rows, schema=ATTR_SCHEMA)


# ---- report -----------------------------------------------------------------------------


def coverage_table(attr: pl.DataFrame, by: Sequence[str]) -> pl.DataFrame:
    """Moves, causes found and coverage (pending moves left out of the share).

    ``partial`` counts measured moves that lacked a page source (it was unavailable).
    """
    if "missing_sources" not in attr.columns:
        attr = attr.with_columns(missing_sources=pl.lit(""))
    return (
        attr.group_by(*by)
        .agg(
            moves=pl.len(),
            pending=(pl.col("cause") == "pending").sum(),
            report=(pl.col("cause") == "report").sum(),
            news_strong=((pl.col("cause") == "news") & (pl.col("strength") == "strong")).sum(),
            news_weak=((pl.col("cause") == "news") & (pl.col("strength") == "weak")).sum(),
            sector=(pl.col("cause") == "sector").sum(),
            none=(pl.col("cause") == "none").sum(),
            partial=(
                (pl.col("cause") != "pending") & (pl.col("missing_sources").fill_null("") != "")
            ).sum(),
        )
        .with_columns(
            measured=pl.col("moves") - pl.col("pending"),
        )
        .with_columns(
            coverage=(pl.col("measured") - pl.col("none")) / pl.col("measured"),
            coverage_strong=(pl.col("report") + pl.col("news_strong")) / pl.col("measured"),
        )
        .sort(*by)
    )


def gate_rows(attr: pl.DataFrame) -> pl.DataFrame:
    """The gate population: liquid stocks, ``|z| >= 3``."""
    return attr.filter(pl.col("liquidity_tier") == GATE_TIER, pl.col("surprise").abs() >= GATE_Z)


def sample_for_check(attr: pl.DataFrame, per_class: int, seed: int) -> pl.DataFrame:
    """A random sample of the gate population's causes, ``per_class`` per kind of cause.

    Kinds: ``report``, ``news_minute``, ``news_day``, ``sector``. Each kind's weight in the
    population is kept so the precision can be weighted back (``score``).
    """
    found = gate_rows(attr).filter(pl.col("cause").is_in(["report", "news", "sector"]))
    rng = random.Random(seed)
    parts: list[pl.DataFrame] = []
    total = found.height
    for (kind,), g in found.group_by("kind", maintain_order=True):
        idx = list(range(g.height))
        rng.shuffle(idx)
        take = g[sorted(idx[:per_class])]
        parts.append(
            take.with_columns(class_share=pl.lit(g.height / total), class_n=pl.lit(g.height))
        )
        log.info("sample %s: %d of %d", kind, take.height, g.height)
    if not parts:
        return pl.DataFrame()
    out = pl.concat(parts, how="diagonal_relaxed")
    return out.select(
        "kind",
        "class_share",
        "class_n",
        "isin",
        "short_name",
        "trade_date",
        "surprise",
        "source",
        "outlet",
        "at",
        "title",
        "url",
    ).with_columns(correct=pl.lit(None, dtype=pl.String), note=pl.lit(None, dtype=pl.String))


def has_labels(path: Path, label: str) -> bool:
    """Whether the sheet at ``path`` has anything in its ``label`` column."""
    if not path.exists():
        return False
    df = pl.read_csv(path, infer_schema_length=0)
    if label not in df.columns:
        return False
    return df.filter(pl.col(label).str.strip_chars().str.len_chars() > 0).height > 0


def write_sheet(
    df: pl.DataFrame,
    path: Path,
    label: str,
    *,
    bom: bool = False,
    now: Callable[[], datetime] = datetime.now,
) -> Path:
    """Write a hand-check sheet, never over one that has labels. Returns the path written.

    A labelled sheet at ``path`` is someone's work (a report run on 2026-11-24 overwrote the
    gate's labelled sample): the new sheet goes next to it, with the time in its name.
    """
    if has_labels(path, label):
        new = path.with_name(f"{path.stem}-{now():%Y%m%d-%H%M%S}{path.suffix}")
        log.warning("%s has labels; the new sheet is %s", path, new.name)
        path = new
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(path, include_bom=bom)
    return path


@dataclass(frozen=True)
class Precision:
    """Weighted precision of a labelled sample, with a normal 95 % interval."""

    value: float
    low: float
    high: float
    by_kind: dict[str, tuple[int, int, float]]  # kind -> (correct, labelled, share)


def score(sample: pl.DataFrame) -> Precision:
    """Precision from a labelled sample (``correct`` = y/n), weighted by each kind's share."""
    lab = sample.filter(pl.col("correct").is_not_null()).with_columns(
        ok=pl.col("correct")
        .str.to_lowercase()
        .str.strip_chars()
        .is_in(["y", "yes", "1", "true", "t"])
    )
    est = var = 0.0
    by_kind: dict[str, tuple[int, int, float]] = {}
    for (kind,), g in lab.group_by("kind"):
        n = g.height
        k = int(g["ok"].sum())
        w = float(g["class_share"][0])
        p = k / n
        est += w * p
        # finite-population corrected variance of the stratum mean
        big_n = int(g["class_n"][0])
        fpc = (big_n - n) / (big_n - 1) if big_n > 1 else 0.0
        var += w * w * p * (1 - p) / n * fpc
        by_kind[str(kind)] = (k, n, w)
    half = 1.96 * math.sqrt(var)
    return Precision(est, max(0.0, est - half), min(1.0, est + half), by_kind)


def report_markdown(
    attr: pl.DataFrame,
    since: date,
    until: date,
    google_min_z: float,
    fetches: pl.DataFrame,
    sources: pl.DataFrame | None = None,
) -> str:
    """The coverage report: gate first, then the breakdowns."""
    g = coverage_table(gate_rows(attr).with_columns(pop=pl.lit("liquid, |z|>=3")), ["pop"])
    lines = [
        f"# News coverage, {since} to {until}",
        "",
        "Cause order: report, then news (BiznesRadar, Bankier, Google News), then sector.",
        f"Google News is queried only for moves with |z| >= {google_min_z:g} that the reports "
        "and pages left unexplained.",
        "",
        "## Sources",
        "",
        _sources_md(sources),
        "",
        "## Gate: liquid stocks, |z| >= 3",
        "",
        _md(g.drop("pop")),
        "",
    ]
    if g.height:
        r = g.row(0, named=True)
        cov = r["coverage"]
        lines.append(
            f"Coverage **{cov:.1%}** of {r['measured']} measured moves "
            f"(target {GATE_COVERAGE:.0%}; {r['pending']} still pending; "
            f"{r['partial']} measured without an unavailable source, so a lower bound). "
            f"Strong causes only (report or timed headline): {r['coverage_strong']:.1%}."
        )
        lines.append("")
    lines += [
        "## By tier and size",
        "",
        _md(coverage_table(attr, ["liquidity_tier", "band"])),
        "",
        "## By source of the cause, |z| >= 3",
        "",
        _md(
            attr.filter(pl.col("band") == "z3", pl.col("cause") != "pending")
            .group_by("cause", "source", "strength")
            .len()
            .sort("len", descending=True)
        ),
        "",
        "## Fetch outcomes",
        "",
        _md(fetches),
        "",
    ]
    return "\n".join(lines)


def _sources_md(sources: pl.DataFrame | None) -> str:
    """Which sources answered on their last run, and why the others did not."""
    if sources is None or sources.height == 0:
        return "_(no collection run recorded)_"
    lines = []
    for r in sources.sort("source").iter_rows(named=True):
        if r["available"]:
            lines.append(f"- **{r['source']}**: available ({r['workers']} workers at the end)")
        else:
            lines.append(
                f"- **{r['source']}**: unavailable on {r['checked_at']:%Y-%m-%d %H:%M} UTC "
                f"({r['reason']}); moves were measured without it"
            )
    return "\n".join(lines)


def _md(df: pl.DataFrame) -> str:
    if df.height == 0:
        return "_(none)_"
    cols = df.columns

    def fmt(v: object) -> str:
        if isinstance(v, float):
            return f"{v:.3f}" if abs(v) < 10 else f"{v:.1f}"
        return "" if v is None else str(v)

    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.iter_rows()]
    return "\n".join([head, sep, *body])


def fetch_summary(store: Store) -> pl.DataFrame:
    """Counts of fetch outcomes per source."""
    f = store.fetched()
    if f.height == 0:
        return f
    return (
        f.group_by("source", "outcome")
        .agg(n=pl.len(), items=pl.col("n_items").sum())
        .sort("source", "outcome")
    )


# ---- loading ----------------------------------------------------------------------------


def load_companies(duckdb_file: Path, store: Store) -> dict[str, CompanyInfo]:
    """Short names (latest GPW or NewConnect price row), issuer names and portal names."""
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
    out: dict[str, CompanyInfo] = {}
    for isin, name in short.iter_rows():
        portal = tuple(
            n
            for s in PAGE_SOURCES
            if (row := store.symbol(isin, s)) and (n := row.get("page_name"))
        )
        out[isin] = CompanyInfo(isin, name, plain_name(issuers.get(isin)), portal)
    return out


def load_events(duckdb_file: Path) -> pl.DataFrame:
    """Every ESPI/EBI report with an ISIN, for the report cause.

    From ``reports``, not ``events``: ``events`` holds only the classified signal reports
    (about a third). ``report_rank`` then keeps the reports that can move a price.
    """
    import duckdb

    con = duckdb.connect(str(duckdb_file), read_only=True)
    try:
        df: pl.DataFrame = con.sql(
            "select report_id, listed_isin as isin, title, source_url, known_at, kind,"
            " signal_type, subtype, attachments_json"
            " from reports where listed_isin is not null"
        ).pl()
        return df
    finally:
        con.close()


def load_inputs(
    work_dir: Path,
    data_dir: Path,
    duckdb_file: Path,
    store: Store,
    *,
    since: date,
    until: date,
    indicators_file: Path | None = None,
) -> Inputs:
    """Moves, companies, reports and sector moves for the window."""
    state = pl.read_parquet(work_dir / "gold" / "market_state.parquet")
    moves = load_moves(state, since, until)
    companies = load_companies(duckdb_file, store)
    reports = report_sessions(load_events(duckdb_file))
    indicators = (
        pl.read_parquet(indicators_file)
        if indicators_file is not None
        else lake.read_table(data_dir, "bronze", "indicators_gpw")
    )
    seed = pl.read_csv(SECTOR_SEED)
    sector_index = sector_of(indicators, seed)
    indices = lake.read_table(data_dir, "bronze", "indices_gpw")
    sectors = sector_moves(indices, sector_index.values()) if indices is not None else {}
    peers = sector_peers(state, sector_index, since, until)
    log.info(
        "%d moves, %d companies, %d with a sector index",
        moves.height,
        len(companies),
        len(sector_index),
    )
    return Inputs(moves, companies, reports, sector_index, sectors, peers)


def default_window(work_dir: Path) -> tuple[date, date]:
    """The last 12 months of the market state."""
    last = (
        pl.scan_parquet(work_dir / "gold" / "market_state.parquet")
        .select(pl.col("trade_date").max())
        .collect()
        .item()
    )
    assert isinstance(last, date)
    return last - timedelta(days=365), last


class CollectorBusy(RuntimeError):
    """Another collector is already writing to this store."""


@contextlib.contextmanager
def collect_lock(root: Path) -> Iterator[None]:
    """Hold ``root/collect.lock`` while collecting: two collectors would overwrite each other.

    The store saves its whole state from memory, so a second process writing the same files
    loses the first one's records. A stale lock (its process gone) has to be removed by hand.
    """
    path = root / "collect.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        owner = path.read_text(encoding="utf-8").strip() if path.exists() else "?"
        raise CollectorBusy(
            f"{path} exists (process {owner}): another collector is running, or a stale lock "
            "is left over; delete it once no collector runs"
        ) from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)
