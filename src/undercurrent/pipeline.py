"""Pipeline orchestration: ingest → parse → dbt → study → publish.

Plain function calls, no orchestrator (see ``docs/SCALING.md`` for why). The commit of
new Parquet partitions to the ``data`` branch is a workflow step, not Python.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time as time_mod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from undercurrent import gpw_calendar, ingest, lake
from undercurrent.fetch import PoliteClient

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")

# Calendar days of reports re-listed every night (covers weekends and late Friday reports).
REPORT_LOOKBACK_DAYS = 4
# Weekdays of prices checked for gaps every night.
PRICE_LOOKBACK_SESSIONS = 10
# Stop waiting for the NewConnect file at this Warsaw local time (SLO is 20:30).
NC_WAIT_UNTIL_LOCAL = time(20, 5)
# Run note on an exchange holiday, followed by its name (gpw_calendar). Not a problem.
CLOSED_NOTE = "GPW closed: "
NC_POLL_EVERY_S = 300
# Never wait longer than this for the NewConnect file, whatever the clock says: a run started
# hours early or late must not sit until the job times out.
NC_MAX_WAIT_S = 100 * 60
# Time the nightly run may spend, after publishing, on insider forms of older months.
FORMS_CATCH_UP_S = 20 * 60
# Calendar days of insider reports checked for forms every night (how far the listing
# portals reach). Reports that already have forms cost no requests.
FORMS_WINDOW_DAYS = 21
# The site keeps 30 sessions of digests; their stocks get verified XTB links.
XTB_LOOKBACK_DAYS = 45
# Backfill yields to the nightly job inside this UTC window on weekdays.
NIGHTLY_WINDOW_UTC = (time(17, 30), time(19, 30))

RUNS_SCHEMA: dict[str, Any] = {
    "run_date": pl.Date,
    "started_at": pl.Datetime("us", "UTC"),
    "finished_at": pl.Datetime("us", "UTC"),
    "status": pl.String,
    "reports_listed": pl.Int64,
    "reports_new": pl.Int64,
    "reports_failed": pl.Int64,
    "prices_gpw_rows": pl.Int64,
    "prices_nc_rows": pl.Int64,
    "nc_pending": pl.Boolean,
    "notes": pl.String,
}


@dataclass
class RunResult:
    """What one nightly run did; becomes a row of ``state/runs``."""

    run_date: date
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    reports_listed: int = 0
    reports_new: int = 0
    reports_failed: int = 0
    prices_gpw_rows: int = 0
    prices_nc_rows: int = 0
    nc_pending: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        """Notes that make the run degraded (an exchange holiday is not one)."""
        return [n for n in self.notes if not n.startswith(CLOSED_NOTE)]

    def row(self) -> dict[str, Any]:
        """Row for the runs table."""
        return {
            "run_date": self.run_date,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "reports_listed": self.reports_listed,
            "reports_new": self.reports_new,
            "reports_failed": self.reports_failed,
            "prices_gpw_rows": self.prices_gpw_rows,
            "prices_nc_rows": self.prices_nc_rows,
            "nc_pending": self.nc_pending,
            "notes": "; ".join(self.notes) or None,
        }


def _record_run(root: Path, result: RunResult) -> None:
    """Upsert this run's row (keyed by run_date + started_at) into ``state/runs``."""
    new = pl.DataFrame([result.row()], schema=RUNS_SCHEMA)
    old = lake.read_state(root, "runs")
    if old is not None:
        old = old.filter(
            ~((pl.col("run_date") == result.run_date) & (pl.col("started_at") == result.started_at))
        )
    df = new if old is None else pl.concat([old, new], how="diagonal_relaxed")
    lake.write_state(root, "runs", df.sort("started_at"))


def _ingest_report_days(client: PoliteClient, root: Path, days: list[date], res: RunResult) -> None:
    for d in days:
        st = ingest.ingest_reports(client, root, d)
        res.reports_listed += st.listed
        res.reports_new += st.written
        res.reports_failed += st.failed + st.blocked
        log.info("reports %s: %s", d, json.dumps(st.as_dict(), ensure_ascii=False))


def _collect_forms(client: PoliteClient, root: Path, days: list[date]) -> None:
    """Notification forms for recent insider reports. Never fails the run."""
    from undercurrent import forms

    try:
        st = forms.collect_forms(client, root, days)
        log.info("insider forms: %s", json.dumps(st.as_dict(), ensure_ascii=False))
    except Exception:
        log.exception("insider forms collection failed; continuing without")


def _catch_up_forms(client: PoliteClient, root: Path, budget_s: float) -> None:
    from undercurrent import forms

    try:
        done = forms.catch_up(client, root, budget_s=budget_s, skip_recent_days=FORMS_WINDOW_DAYS)
        log.info("insider forms catch-up: %d months", len(done))
    except Exception:
        log.exception("insider forms catch-up failed; continuing without")


def _recent_sessions(day: date, n: int) -> list[date]:
    """The ``n`` GPW session days up to and including ``day``, oldest first."""
    weekdays = ingest.recent_weekdays(day, n + 15)
    return [d for d in weekdays if gpw_calendar.is_session(d)][-n:]


def _fill_price_gaps(client: PoliteClient, root: Path, day: date, res: RunResult) -> None:
    for d in _recent_sessions(day, PRICE_LOOKBACK_SESSIONS):
        try:
            res.prices_gpw_rows += sum(ingest.ingest_gpw_prices(client, root, d).values())
        except ingest.NoSessionError:
            if d == day:
                res.notes.append(f"no GPW session data for {d}")
        if d == day:
            continue
        with contextlib.suppress(ingest.NoSessionError):
            res.prices_nc_rows += ingest.ingest_nc_prices(client, root, d)


def _wait_for_nc(
    client: PoliteClient,
    root: Path,
    day: date,
    res: RunResult,
    *,
    wait_until: datetime | None,
    sleep: Callable[[float], None],
    clock: Callable[[], datetime],
) -> None:
    while True:
        try:
            res.prices_nc_rows += ingest.ingest_nc_prices(client, root, day)
            return
        except ingest.NoSessionError:
            if wait_until is None or clock() + timedelta(seconds=NC_POLL_EVERY_S) > wait_until:
                res.nc_pending = True
                # Not waiting is the normal nightly case (the digest goes out first and the
                # next run fills the day in), so it is flagged but not a degraded run.
                if wait_until is not None:
                    res.notes.append(f"NewConnect prices for {day} pending")
                return
            log.info("NewConnect file for %s not out yet; retrying in %ds", day, NC_POLL_EVERY_S)
            sleep(NC_POLL_EVERY_S)


def snapshot_gold(data_dir: Path, stats_path: Path, day: date) -> None:
    """Keep an immutable daily copy of signal_stats on the data branch (small: KBs/day)."""
    df = pl.read_parquet(stats_path)
    lake.append_partition(data_dir, "gold", "signal_stats", day, df)


def run_day(
    day: date,
    *,
    data_dir: Path,
    work_dir: Path = Path(".work"),
    publish: bool = True,
    wait_for_nc: bool = True,
    run_transform: bool = True,
    site_dir: Path = Path("_site"),
    digest_dir: Path = Path("digests"),
    client: PoliteClient | None = None,
    sleep: Callable[[float], None] = time_mod.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    forms_catch_up_s: float = FORMS_CATCH_UP_S,
) -> int:
    """Run the nightly pipeline for ``day``. Returns a process exit code."""
    res = RunResult(run_date=day, started_at=clock())
    own_client = client is None
    client = client or PoliteClient()
    # Recorded as "running" straight away: if the job is killed (a timeout, a cancelled
    # workflow) the row stays that way and the pipeline page shows a run that did not finish.
    _record_run(data_dir, res)
    # An exchange holiday: reports are still collected (they go into the next session's
    # digest), but there are no prices to wait for and no digest to send.
    closed = gpw_calendar.closed_reason(day)
    if closed:
        res.notes.append(f"{CLOSED_NOTE}{closed}")
        log.info("%s: GPW closed (%s)", day, closed)
    try:
        days = [day - timedelta(days=i) for i in range(REPORT_LOOKBACK_DAYS - 1, -1, -1)]
        this_month = day.replace(day=1)
        prev_month = (this_month - timedelta(days=1)).replace(day=1)
        retry = [d for d in ingest.failed_days(data_dir, [prev_month, this_month]) if d not in days]
        _ingest_report_days(client, data_dir, retry + days, res)
        forms_days = [day - timedelta(days=i) for i in range(FORMS_WINDOW_DAYS - 1, -1, -1)]
        _collect_forms(client, data_dir, sorted(set(retry + forms_days)))
        _fill_price_gaps(client, data_dir, day, res)
        wait_until = None
        if wait_for_nc:
            wait_until = datetime.combine(day, NC_WAIT_UNTIL_LOCAL, tzinfo=WARSAW).astimezone(UTC)
            wait_until = min(wait_until, res.started_at + timedelta(seconds=NC_MAX_WAIT_S))
        if not closed:
            _wait_for_nc(
                client, data_dir, day, res, wait_until=wait_until, sleep=sleep, clock=clock
            )
        if run_transform:
            from undercurrent import study, transform

            transform.build(data_dir, work_dir)
            stats_path = study.run_study(transform.duckdb_path(work_dir), work_dir, day)
            snapshot_gold(data_dir, stats_path, day)
            _run_pick(work_dir, data_dir, transform.duckdb_path(work_dir), day)
            _refresh_xtb_links(client, data_dir, transform.duckdb_path(work_dir), clock())
        res.status = "ok" if not res.problems else "degraded"
        if publish and run_transform:
            from undercurrent import publish as pub

            # record the run first so the site and digest show it
            res.finished_at = clock()
            _record_run(data_dir, res)
            no_session = any(n.startswith("no GPW session") for n in res.notes)
            pub.publish(
                work_dir,
                data_dir,
                day,
                out=site_dir,
                digest_dir=digest_dir,
                send=not no_session and not closed,
                digest=not closed,
            )
            if no_session:
                pub.send_alert(
                    f"No GPW session data for {day} (holiday, or the archive is late). "
                    "No digest tonight."
                )
        elif not publish:
            log.info("publish disabled")
        if forms_catch_up_s > 0:
            _catch_up_forms(client, data_dir, forms_catch_up_s)
        return 0
    except Exception as exc:
        res.status = "failed"
        res.notes.append(f"{type(exc).__name__}: {exc}")
        log.exception("run %s failed", day)
        return 1
    finally:
        if res.finished_at is None or res.status == "failed":
            res.finished_at = clock()
        _record_run(data_dir, res)
        if own_client:
            client.close()
        log.info("run summary: %s", json.dumps(res.row(), default=str, ensure_ascii=False))


def _run_pick(work_dir: Path, data_dir: Path, duckdb_file: Path, day: date) -> None:
    """Stock of the week (``pick.py``). Optional: a failure is only logged."""
    from undercurrent import pick

    try:
        pick.run_pick(duckdb_file, work_dir, data_dir, day)
    except Exception as exc:  # the digest must not wait on the pick
        log.warning("stock of the week not computed: %s: %s", type(exc).__name__, exc)


def _refresh_xtb_links(
    client: PoliteClient, data_dir: Path, duckdb_file: Path, now: datetime
) -> None:
    """Verify XTB links for recent events' stocks. Optional: a failure is only logged."""
    from undercurrent import xtb

    try:
        since = now - timedelta(days=XTB_LOOKBACK_DAYS)
        top = duckdb_file.parent / "gold" / "pick_scores.parquet"
        scores = pl.read_parquet(top) if top.exists() else pl.DataFrame()
        also = scores.head(10)["isin"].to_list() if "isin" in scores.columns else []
        xtb.refresh(client, data_dir, xtb.recent_companies(duckdb_file, since, also), now=now)
    except Exception as exc:  # the links are a convenience; the digest must not wait on them
        log.warning("xtb links not refreshed: %s: %s", type(exc).__name__, exc)


def nightly_date(now: datetime) -> date:
    """Trading day a nightly run started at ``now`` belongs to.

    GitHub can deliver a schedule hours late. A run that starts before the 17:05 close belongs
    to the last session that has finished, not to the new day; weekends and exchange
    holidays (``gpw_calendar``) are skipped.
    """
    local = now.astimezone(WARSAW)
    day = local.date()
    if local.time() < time(17, 5):
        day -= timedelta(days=1)
    return gpw_calendar.last_session(day)


def _yield_to_nightly(clock: Callable[[], datetime], sleep: Callable[[float], None]) -> None:
    now = clock()
    start, end = NIGHTLY_WINDOW_UTC
    if now.weekday() < 5 and start <= now.time() < end:
        resume = datetime.combine(now.date(), end, tzinfo=UTC)
        wait = (resume - now).total_seconds()
        log.info("inside nightly window; backfill pauses %.0fs", wait)
        sleep(wait)


def backfill(
    start: date,
    end: date,
    *,
    data_dir: Path,
    client: PoliteClient | None = None,
    sleep: Callable[[float], None] = time_mod.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Backfill every day in ``[start, end]``: reports daily, prices on weekdays.

    A day that fails (the source is down, a file is malformed) does not stop the range: the
    remaining days run, the failed days are tried once more at the end, and the exit code is
    1 only if some still fail. Every day is idempotent, so a re-run fetches what is missing.
    """
    own_client = client is None
    client = client or PoliteClient()
    failures = 0

    def one_day(d: date) -> int:
        st = ingest.ingest_reports(client, data_dir, d)
        prices = 0
        if d.weekday() < 5:
            try:
                prices += sum(ingest.ingest_gpw_prices(client, data_dir, d).values())
            except ingest.NoSessionError:
                log.info("%s: no GPW session (holiday)", d)
            try:
                prices += ingest.ingest_nc_prices(client, data_dir, d)
            except ingest.NoSessionError:
                log.info("%s: no NewConnect file", d)
        log.info(
            "backfill %s: listed=%d new=%d failed=%d price_rows=%d",
            d,
            st.listed,
            st.written,
            st.failed + st.blocked,
            prices,
        )
        return st.failed + st.blocked

    def attempt(days: list[date]) -> list[date]:
        nonlocal failures
        broken: list[date] = []
        for d in days:
            _yield_to_nightly(clock, sleep)
            try:
                failures += one_day(d)
            except Exception:
                log.exception("backfill %s failed; continuing with the next day", d)
                broken.append(d)
        return broken

    try:
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        broken = attempt(days)
        if broken:
            log.info("retrying %d failed day(s): %s", len(broken), ", ".join(map(str, broken)))
            broken = attempt(broken)
    finally:
        if own_client:
            client.close()
    log.info("backfill %s..%s done; %d report failures queued for retry", start, end, failures)
    if broken:
        log.error("days still failing: %s", ", ".join(map(str, broken)))
        return 1
    return 0
