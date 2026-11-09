"""Command-line entry point: ``undercurrent run | backfill | probe``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
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

    ck = sub.add_parser("check-lake", help="fail if the Parquet lake exceeds a size budget")
    ck.add_argument("--max-mb", type=float, default=900.0)

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
    """Nightly pipeline (filled in milestone by milestone)."""
    from undercurrent import pipeline

    return pipeline.run_day(
        args.date,
        data_dir=args.data_dir,
        publish=not args.no_publish,
        wait_for_nc=not args.no_wait_nc,
    )


def cmd_backfill(args: argparse.Namespace) -> int:
    """Historical backfill over ``[start, end]``."""
    from undercurrent import pipeline

    if args.end < args.start:
        log.error("--end %s is before --start %s", args.end, args.start)
        return 2
    return pipeline.backfill(args.start, args.end, data_dir=args.data_dir)


def cmd_check_lake(args: argparse.Namespace) -> int:
    """Size check for the ``data`` branch (keep it well under 1 GB)."""
    from undercurrent.lake import lake_size_bytes

    mb = lake_size_bytes(args.data_dir) / 1e6
    print(f"lake size: {mb:.1f} MB (budget {args.max_mb:.0f} MB)")
    return 0 if mb <= args.max_mb else 1


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
        "backfill": cmd_backfill,
        "probe": cmd_probe,
        "check-lake": cmd_check_lake,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
