"""Command-line entry point: ``undercurrent run | backfill | probe``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
log = logging.getLogger("undercurrent")


def _parse_date(value: str) -> date:
    """Accept ``YYYY-MM-DD`` or the literal ``today`` (Warsaw local date)."""
    if value == "today":
        return datetime.now(WARSAW).date()
    return date.fromisoformat(value)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="undercurrent", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="root of the Parquet lake (checked out from the `data` branch)",
    )
    p.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".work"),
        help="scratch dir for rebuilt silver/gold outputs (never committed)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the nightly pipeline for one trading day")
    run.add_argument("--date", type=_parse_date, default=_parse_date("today"))
    run.add_argument("--no-publish", action="store_true", help="skip site + Telegram")
    run.add_argument(
        "--no-wait-nc", action="store_true", help="don't poll for the NewConnect price file"
    )

    bf = sub.add_parser("backfill", help="ingest + parse a historical date range")
    bf.add_argument("--start", type=_parse_date, required=True)
    bf.add_argument("--end", type=_parse_date, required=True)

    fm = sub.add_parser("forms", help="collect insider notification forms from portal copies")
    fm.add_argument("--start", type=_parse_date, default=None)
    fm.add_argument("--end", type=_parse_date, default=None)
    fm.add_argument(
        "--catch-up-minutes",
        type=float,
        default=0.0,
        help="also scan older months, newest first, for this many minutes",
    )

    sub.add_parser("nightly-date", help="print the trading day a run started now belongs to")

    ck = sub.add_parser("check-lake", help="fail if the Parquet lake exceeds a size budget")
    ck.add_argument("--max-mb", type=float, default=900.0)

    tr = sub.add_parser("transform", help="rebuild silver/gold from bronze (parse + dbt)")
    tr.add_argument("--as-of", default=None, help="point-in-time cut-off (ISO timestamp)")
    tr.add_argument("--no-dbt", action="store_true")

    pb = sub.add_parser("publish", help="render the site and send the Telegram digest")
    pb.add_argument("--date", type=_parse_date, default=_parse_date("today"))
    pb.add_argument("--out", type=Path, default=Path("_site"))
    pb.add_argument("--no-send", action="store_true", help="render only, no Telegram")

    al = sub.add_parser("alert", help="send a Telegram alert (job failure / stale data)")
    al.add_argument("message", nargs="?", default=None)
    al.add_argument("--if-stale", action="store_true", help="only alert if data is stale")

    cp = sub.add_parser("compact", help="merge daily bronze partitions of past months")
    cp.add_argument(
        "--keep-months",
        type=int,
        default=2,
        help="leave the current and previous N-1 months as daily partitions",
    )

    pk = sub.add_parser("pick", help="compute the stock of the week (needs a built work dir)")
    pk.add_argument("--date", type=_parse_date, default=_parse_date("today"))

    xl = sub.add_parser("xtb-links", help="verify links to company pages on xtb.com")
    xl.add_argument("--days", type=int, default=45, help="stocks with events in this many days")
    xl.add_argument("--max", type=int, default=400, help="at most this many stocks per run")

    pr = sub.add_parser("probe", help="check reachability of every official source")
    pr.add_argument("--date", type=_parse_date, default=date(2026, 11, 6))
    pr.add_argument("--geru-id", type=int, default=500131)
    pr.add_argument(
        "--attachment",
        default=None,
        help="relative attachment path from a report page, to test PDF access",
    )
    pr.add_argument("--json", type=Path, default=None, help="also write results as JSON")
    return p


def cmd_run(args: argparse.Namespace) -> int:
    """Nightly pipeline: ingest, transform, study, publish."""
    from undercurrent import pipeline

    return pipeline.run_day(
        args.date,
        data_dir=args.data_dir,
        work_dir=args.work_dir,
        publish=not args.no_publish,
        wait_for_nc=not args.no_wait_nc,
    )


def cmd_nightly_date(args: argparse.Namespace) -> int:
    """Print the trading day for a nightly run that starts now (late schedules included)."""
    from undercurrent import pipeline

    print(pipeline.nightly_date(datetime.now(WARSAW)).isoformat())
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """Historical backfill over ``[start, end]``."""
    from undercurrent import pipeline

    if args.end < args.start:
        log.error("--end %s is before --start %s", args.end, args.start)
        return 2
    return pipeline.backfill(args.start, args.end, data_dir=args.data_dir)


def cmd_forms(args: argparse.Namespace) -> int:
    """Collect forms for ``[start, end]`` and/or catch up on older months."""
    from undercurrent import forms
    from undercurrent.fetch import PoliteClient

    with PoliteClient() as client:
        if args.start and args.end:
            days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
            st = forms.collect_forms(client, args.data_dir, days)
            log.info("insider forms %s..%s: %s", args.start, args.end, st.as_dict())
        if args.catch_up_minutes > 0:
            forms.catch_up(client, args.data_dir, budget_s=args.catch_up_minutes * 60)
    return 0


def cmd_transform(args: argparse.Namespace) -> int:
    """Rebuild silver and gold."""
    from undercurrent import transform

    transform.build(args.data_dir, args.work_dir, as_of=args.as_of, dbt=not args.no_dbt)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Render site + digest."""
    from undercurrent import publish

    publish.publish(args.work_dir, args.data_dir, args.date, out=args.out, send=not args.no_send)
    return 0


def cmd_alert(args: argparse.Namespace) -> int:
    """Send an alert, or check staleness first with ``--if-stale``."""
    from undercurrent import publish

    msg = args.message
    if args.if_stale:
        reason = publish.is_stale(args.data_dir)
        if reason is None:
            return 0
        msg = f"Data is stale: {reason}."
    publish.send_alert(msg or "The nightly job failed. See the Actions log.")
    return 0


def cmd_compact(args: argparse.Namespace) -> int:
    """Compact every bronze table's months older than ``--keep-months``."""
    from undercurrent import lake

    today = datetime.now(WARSAW).date().replace(day=1)
    cutoff = today
    for _ in range(args.keep_months - 1):
        cutoff = (cutoff - timedelta(days=1)).replace(day=1)
    bronze = args.data_dir / "bronze"
    if not bronze.exists():
        return 0
    for table in sorted(p.name for p in bronze.iterdir() if p.is_dir()):
        months = sorted({d.name[5:12] for d in (bronze / table).glob("date=*")})
        for m in months:
            month = date.fromisoformat(f"{m}-01")
            if month < cutoff:
                lake.compact_month(args.data_dir, "bronze", table, month)
    return 0


def cmd_check_lake(args: argparse.Namespace) -> int:
    """Size check for the ``data`` branch (keep it well under 1 GB)."""
    from undercurrent.lake import lake_size_bytes

    mb = lake_size_bytes(args.data_dir) / 1e6
    print(f"lake size: {mb:.1f} MB (budget {args.max_mb:.0f} MB)")
    return 0 if mb <= args.max_mb else 1


def cmd_pick(args: argparse.Namespace) -> int:
    """Stock of the week from the work dir's gold tables; appends the live record."""
    from undercurrent import pick, transform

    pick.run_pick(transform.duckdb_path(args.work_dir), args.work_dir, args.data_dir, args.date)
    return 0


def cmd_xtb_links(args: argparse.Namespace) -> int:
    """Verify XTB links for the stocks of recent events (needs a built work dir)."""
    from datetime import UTC, datetime, timedelta

    from undercurrent import transform, xtb
    from undercurrent.fetch import PoliteClient

    now = datetime.now(UTC)
    companies = xtb.recent_companies(
        transform.duckdb_path(args.work_dir), now - timedelta(days=args.days)
    )
    with PoliteClient() as client:
        xtb.refresh(client, args.data_dir, companies, now=now, max_checks=args.max)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Probe sources and print a Markdown table (and to the Actions step summary)."""
    from undercurrent.probe import run_probe, to_json, to_markdown

    results = run_probe(args.date, args.geru_id, args.attachment)
    md = to_markdown(results)
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Source probe\n\n" + md + "\n")
    if args.json:
        args.json.write_text(to_json(results), encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to a sub-command."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        "run": cmd_run,
        "nightly-date": cmd_nightly_date,
        "backfill": cmd_backfill,
        "probe": cmd_probe,
        "check-lake": cmd_check_lake,
        "transform": cmd_transform,
        "publish": cmd_publish,
        "alert": cmd_alert,
        "compact": cmd_compact,
        "forms": cmd_forms,
        "xtb-links": cmd_xtb_links,
        "pick": cmd_pick,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
