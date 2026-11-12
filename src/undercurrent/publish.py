"""Render the static site (Jinja2 + inline SVG) and send the Telegram digest.

The site is plain HTML/CSS with a few lines of vanilla JS for the report-type filter.
No framework, no build step. Telegram is a plain HTTPS POST to the Bot API; the token is
read from the environment and never logged.
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import httpx
import polars as pl
from jinja2 import Environment, FileSystemLoader, select_autoescape

from undercurrent import i18n, lake, pages, xtb

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
SITE_DIR = Path(__file__).resolve().parents[2] / "site"
DEFAULT_SITE_URL = "https://almera-vs.github.io/undercurrent/"
HORIZON_DEFAULT = 20
VERDICT_ORDER = {
    "holds_up": 0,
    "unconfirmed": 1,
    "eaten_by_costs": 2,
    "untested": 3,
    "noise": 4,
    "too_few": 5,
}
VERDICT_LABEL = i18n.VERDICT_LABELS["en"]
# "Worth a look": the signal type has moved prices significantly in the past. The Today page
# shows them in this order, one section per verdict.
WORTH_VERDICTS = ("holds_up", "unconfirmed", "eaten_by_costs", "untested")
# Reports of the same company listed on its event pages.
COMPANY_HISTORY = 8
# Sessions kept in the archive (each with its digest's events and their pages).
ARCHIVE_SESSIONS = 30
# Some exchange pages carry the legal basis with Polish letters dropped; repair for display.
_BASIS_REPAIRS = (
    ("Rozporzdzenia", "Rozporządzenia"),
    ("penice obowizki zarzdcze", "pełniące obowiązki zarządcze"),
)


def repair_basis(text: str | None) -> str | None:
    """Restore Polish letters the source page dropped from a legal basis."""
    if text:
        for bad, good in _BASIS_REPAIRS:
            text = text.replace(bad, good)
    return text


def _tick_label(v: float, step: float, lang: str) -> str:
    """Axis label: whole percent when the grid allows, no sign on zero."""
    digits = 0 if step >= 0.01 else 1 if step >= 0.001 else 2
    if round(v * 100, digits) == 0:
        return "0%"
    return i18n.pct(v, lang, digits=digits)


def label_for(key: str, lang: str = "en") -> str:
    """Human label for a signal key (or a bare ``other`` / ``periodic`` type)."""
    labels = i18n.SIGNAL_LABELS[lang]
    return labels.get(key, key.replace("_", " ").replace(":", " · "))


# --------------------------------------------------------------------------- formatting
# English defaults; the site binds the language through the Jinja filters.
pct = i18n.pct
num = i18n.num
short_pln = i18n.short_pln


def tone(x: float | None) -> str:
    """CSS class: blue for up, orange for down."""
    if x is None or (isinstance(x, float) and math.isnan(x)) or x == 0:
        return "flat"
    return "up" if x > 0 else "down"


# --------------------------------------------------------------------------- SVG chart


# The "bad case" tile: the outcome that only this share of past events did worse than.
TAIL_SHARE = 0.05
MIN_EVENTS_FOR_TAIL = 20


def tail_outcome(cars: pl.DataFrame, horizon: int, *, short: bool) -> float | None:
    """Excess return that only 1 past event in 20 did worse than; for a short, the rise.

    A percentile, not the single worst event: the extreme of a sample is one stock's story
    and grows with the sample. One event per stock and day; None with too few events.
    """
    col = (
        cars.unique(subset=["isin", "event_date"], keep="first", maintain_order=True)
        .get_column(f"car_{horizon}")
        .drop_nulls()
    )
    if col.len() < MIN_EVENTS_FOR_TAIL:
        return None
    q = col.quantile(1 - TAIL_SHARE if short else TAIL_SHARE, interpolation="linear")
    return None if q is None else float(q)


def car_chart_svg(
    path: list[dict[str, Any]],
    width: int = 900,
    height: int = 380,
    lang: str = "en",
    entry: int | None = 1,
    horizon: int | None = None,
) -> str:
    """Average CAR path [-5, +20] with bootstrap band as inline SVG.

    ``entry`` is the entry session relative to day 0 (marked with a labelled line); ``None``
    hides it. With ``horizon``, the end of the holding period the statistics use is marked
    too, when it falls inside the chart.
    """
    _ = i18n.tr(lang)
    if not path:
        return f'<p class="muted">{_("Not enough past events for a chart yet.")}</p>'
    pad_l, pad_r, pad_t, pad_b = 96, 24, 44, 44
    days = [int(p["day"]) for p in path]
    vals = [float(v) for p in path for v in (p["lo"], p["hi"], p["mean_car"])]
    lo_v, hi_v = min([*vals, 0.0]), max([*vals, 0.0])
    span = (hi_v - lo_v) or 0.01
    lo_v, hi_v = lo_v - span * 0.08, hi_v + span * 0.12

    def x(d: float) -> float:
        return float(pad_l + (d - days[0]) / (days[-1] - days[0]) * (width - pad_l - pad_r))

    def y(v: float) -> float:
        return float(pad_t + (hi_v - v) / (hi_v - lo_v) * (height - pad_t - pad_b))

    lx, ly = x(path[-1]["day"]), y(path[-1]["mean_car"])

    band = " ".join(f"{x(p['day']):.1f},{y(p['hi']):.1f}" for p in path)
    band += " " + " ".join(f"{x(p['day']):.1f},{y(p['lo']):.1f}" for p in reversed(path))
    line = " ".join(f"{x(p['day']):.1f},{y(p['mean_car']):.1f}" for p in path)
    step = 10 ** math.floor(math.log10(span / 3)) if span > 0 else 0.01
    ticks = []
    t = math.floor(lo_v / step) * step
    while t <= hi_v:
        if t >= lo_v:
            ticks.append(t)
        t += step
    ticks = ticks[:: max(1, len(ticks) // 6)]
    last = path[-1]
    end_tone = "up" if last["mean_car"] >= 0 else "down"
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_("Average excess return path, day -5 to +20")}">',
        *(
            f'<line class="grid" x1="{pad_l}" x2="{width - pad_r}" '
            f'y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y(v) + 4:.1f}" text-anchor="end">'
            f"{_tick_label(v, step, lang)}</text>"
            for v in ticks
        ),
        f'<line class="zero" x1="{pad_l}" x2="{width - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>',
        f'<line class="event" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="{pad_t}" y2="{height - pad_b}"/>',
        f'<text class="tick lbl" x="{x(0) - 6:.1f}" y="{pad_t - 12}" text-anchor="end">'
        f"{_('publication')}</text>",
        f'<line class="entry" x1="{x(entry):.1f}" x2="{x(entry):.1f}" '
        f'y1="{pad_t}" y2="{height - pad_b}"/>'
        f'<text class="tick lbl lbl-entry" x="{x(entry) + 6:.1f}" y="{pad_t - 12}">'
        f"{_('entry')}</text>"
        if entry is not None and entry in days
        else "",
        f'<line class="horizon" x1="{x(entry + horizon):.1f}" x2="{x(entry + horizon):.1f}" '
        f'y1="{pad_t}" y2="{height - pad_b}"/>'
        f'<text class="tick lbl" x="{x(entry + horizon) - 6:.1f}" y="{pad_t - 12}" '
        f'text-anchor="end">{_("end of {h} sessions", h=horizon)}</text>'
        if entry is not None and horizon is not None and entry + horizon in days
        else "",
        f'<polygon class="band band-{end_tone}" points="{band}"/>',
        f'<polyline class="line line-{end_tone}" points="{line}"/>',
        f'<circle class="dot-{end_tone}" cx="{lx:.1f}" cy="{ly:.1f}" r="5"/>',
        f'<text class="tick strong" x="{lx - 8:.1f}" y="{ly - 12:.1f}" text-anchor="end">'
        f"{pct(last['mean_car'], lang)}</text>",
        *(
            f'<text class="tick" x="{x(d):.1f}" y="{height - 10}" text-anchor="middle">'
            f"{_('day 0') if d == 0 else f'{d:+d}'}</text>"
            for d in (-5, 0, 5, 10, 15, 20)
            if d in days
        ),
        "</svg>",
    ]
    return "".join(parts)


# --------------------------------------------------------------------------- context


@dataclass
class SiteContext:
    """Everything the templates and the digest need."""

    day: date
    generated_at: datetime
    run: dict[str, Any]
    counts: dict[str, Any]
    worth: list[dict[str, Any]]
    feed: list[dict[str, Any]]
    stats: dict[int, list[dict[str, Any]]]
    summary: dict[str, int]
    events: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    coverage: dict[str, Any]
    health: str
    site_url: str
    health_bits: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    # earlier digests, newest first: {"day": date, "events": [...]}
    archive: list[dict[str, Any]] = field(default_factory=list)
    # today's events worth a look: one section per verdict, one group per signal type
    tiers: list[dict[str, Any]] = field(default_factory=list)
    # how the archive's events have done since entry, per verdict
    record: list[dict[str, Any]] = field(default_factory=list)
    # stock of the week (pick.py): None until the pick has been computed
    pick: dict[str, Any] | None = None


def headline_horizon(key: str, by_key_h: dict[tuple[str, int], dict[str, Any]]) -> int:
    """Horizon whose numbers back a signal's verdict, for cards, event pages and the digest.

    For a walk-forward verdict it is the holding period of the latest rule; for a signal
    that is significant but not yet tested, the horizon with the lowest q; otherwise 20.
    """
    from undercurrent.study import HORIZONS

    rows = {h: by_key_h.get((key, h)) for h in HORIZONS}
    any_row = next((r for r in rows.values() if r), None)
    if any_row is None:
        return HORIZON_DEFAULT
    rule = any_row.get("oos_last_rule")
    if any_row["verdict"] in ("holds_up", "unconfirmed", "eaten_by_costs") and rule:
        h = int(rule.split()[-1].rstrip("d"))
        if h in rows:
            return h
    if any_row["verdict"] == "untested":
        qs = [(r["q_value"], h) for h, r in rows.items() if r and r["q_value"] is not None]
        if qs:
            return min(qs)[1]
    return HORIZON_DEFAULT


def within_slo(run: dict[str, Any]) -> bool | None:
    """Digest delivered by 20:30 Warsaw on the run date; None on an exchange holiday.

    A failed run, or one that found no GPW session data on a session day (it sends an alert,
    not a digest), does not count, whatever time it finished. A holiday has no digest due.
    """
    from undercurrent.pipeline import CLOSED_NOTE

    if CLOSED_NOTE in (run.get("notes") or ""):
        return None
    fin = run.get("finished_at")
    if fin is None or run.get("status") == "failed":
        return False
    if "no GPW session" in (run.get("notes") or ""):
        return False
    local = fin.astimezone(WARSAW)
    return bool(local.date() == run["run_date"] and local.strftime("%H:%M") <= "20:30")


def health_text(bits: list[tuple[str, dict[str, Any]]], lang: str) -> str:
    """One-line run health summary, e.g. ``Run ok · history since 2015-03-02``."""
    _ = i18n.tr(lang)
    out = []
    for tmpl, kw in bits:
        kw = {k: (_(v) if isinstance(v, str) and k in ("s", "h") else v) for k, v in kw.items()}
        out.append(_(tmpl, **kw))
    text = " · ".join(out)
    return text[:1].upper() + text[1:]


_FACT_SIDE = {
    "en": {
        "buy": "bought",
        "sell": "sold",
        "pledge": "pledged",
        "mixed": "traded",
        "gift": "gave away",
        "incentive": "was granted",
    },
}


def _fact_line(r: dict[str, Any], lang: str = "en") -> str:
    """One line of fact for a card / digest, built only from parsed fields or the title."""
    if r["signal_type"] != "insider_tx":
        title = r.get("title") or r.get("subject")
        if title:
            return str(title)
        # periodic reports often have no title on the exchange page: name the report kind
        kind = (r.get("kind") or "").strip()
        if kind:
            return f"Raport {kind.lower()}"
        return "(bez tytułu)" if lang == "pl" else "(no title)"
    role = r.get("insider_role") or ""
    side = r.get("insider_side") or ""
    vol = r.get("insider_volume")
    price = r.get("insider_avg_price")
    value = r.get("insider_value")
    if lang == "pl":
        who = i18n.ROLE_LABELS["pl"].get(role, "insider")
        what = i18n.SIDE_LABELS["pl"].get(side)
        if what and vol:
            s = f"{what.capitalize()} ({who}): {num(vol, 'pl')} akcji"
            if price:
                s += f" po {num(price, 'pl')} zł"
            if value:
                s += f" ({short_pln(value, 'pl')} zł)"
            return s + "."
        if what:
            return f"{what.capitalize()} akcji ({who}); wolumen i cena nie są podane."
        return f"Powiadomienie z art. 19 MAR ({who}); szczegóły transakcji nie są podane."
    who = i18n.ROLE_LABELS["en"].get(role, "insider")
    who = who[:1].upper() + who[1:]
    verb = _FACT_SIDE["en"].get(side)
    if verb and vol:
        s = f"{who} {verb} {num(vol)} shares"
        if price:
            s += f" at {price:.2f} PLN"
        if value:
            s += f" ({short_pln(value)} PLN)"
        return s + "."
    if verb:
        return f"{who} {verb} shares; volume and price are not stated."
    return f"Art. 19 MAR notification ({who}); trade details are not stated."


def gather(work: Path, data_dir: Path, day: date, site_url: str = DEFAULT_SITE_URL) -> SiteContext:
    """Collect all site/digest data for ``day`` from the work dir and the lake."""
    from undercurrent.study import HORIZONS, digest_window, entry_offset, signal_key
    from undercurrent.transform import duckdb_path

    con = duckdb.connect(str(duckdb_path(work)))
    try:
        reports = con.sql(
            "select report_id, site, system, kind, issuer_name, ticker, short_name, isin, title, "
            "subject, legal_basis, signal_type, subtype, insider_person, insider_role, "
            "insider_side, insider_volume, insider_avg_price, insider_value, "
            "insider_trade_date, insider_venue, insider_parse_notes, insider_source, "
            "insider_form_url, published_at_local, body_text, market, "
            "known_at, source_url, report_number from reports"
        ).pl()
        session_days = [r[0] for r in con.sql("select trade_date from sessions").fetchall()]
    finally:
        con.close()
    stats_df = pl.read_parquet(work / "gold" / "signal_stats.parquet")
    paths_df = pl.read_parquet(work / "gold" / "car_paths.parquet")
    ev_cars = pl.read_parquet(work / "gold" / "event_cars.parquet")

    reports = reports.with_columns(
        pl.col("published_at_local").str.slice(0, 10).str.to_date().alias("pub_date"),
        pl.col("published_at_local").str.slice(11, 5).alias("pub_time"),
    )
    # The same window the study assumes: after the previous session's cut-off, up to today's.
    start, end = digest_window(day, sorted(session_days))
    known = pl.col("known_at").cast(pl.Datetime("us", "UTC"))
    today = reports.filter((known > start) & (known <= end)).sort(
        "published_at_local", descending=True
    )

    stats_rows = stats_df.to_dicts()
    by_h: dict[int, list[dict[str, Any]]] = {h: [] for h in HORIZONS}
    for r in stats_rows:
        r["label"] = label_for(r["signal_key"])
        r["basis"] = i18n.BASIS_LABELS["en"].get(r["signal_key"].split(":")[0], "")
        by_h[int(r["horizon"])].append(r)
    for h, rows in by_h.items():
        rows.sort(
            key=lambda r: (
                VERDICT_ORDER.get(r["verdict"], 9),
                r["q_value"] if r["q_value"] is not None else 2,
                -(r["n"] or 0),
            )
        )
        by_h[h] = rows
    stat20 = {r["signal_key"]: r for r in by_h.get(HORIZON_DEFAULT, [])}
    by_key_h = {(r["signal_key"], int(r["horizon"])): r for r in stats_rows}
    summary = {
        "tested": len(stat20),
        **{v: sum(1 for r in stat20.values() if r["verdict"] == v) for v in VERDICT_ORDER},
    }

    # calendar for entry markers: known sessions plus the coming weekdays
    cal = sorted(session_days)
    nxt = cal[-1] if cal else day - timedelta(days=1)
    while len(cal) < len(session_days) + 10:
        nxt += timedelta(days=1)
        if nxt.weekday() < 5:
            cal.append(nxt)

    def day0_for(known_at: datetime) -> date | None:
        local = known_at.astimezone(WARSAW).replace(tzinfo=None)
        return next((s for s in cal if local < datetime(s.year, s.month, s.day, 17, 5)), None)

    def entry_for(known_at: datetime) -> int | None:
        day0 = day0_for(known_at)
        off = entry_offset(known_at, day0, cal) if day0 else -1
        return off if off >= 0 else None

    def make_item(r: dict[str, Any], digest: date) -> dict[str, Any]:
        """One report as the templates see it; signal events also get their statistics."""
        r["legal_basis"] = repair_basis(r["legal_basis"])
        key = signal_key(r["signal_type"], r["subtype"], r["insider_side"])
        label_key = key if r["signal_type"] not in ("other", "periodic") else r["signal_type"]
        item = {
            **r,
            "signal_key": key,
            "label_key": label_key,
            "label": label_for(label_key),
            "fact": _fact_line(r),
            "ticker": r["ticker"] or r["short_name"] or "—",
            # reports from an earlier calendar day (evening, weekend) show their date too
            "pub_day": "" if r["pub_date"] == digest else f"{r['pub_date']:%d.%m}",
            "filter": {
                "insider_tx": "insider",
                "inside_info": "inside",
                "holder_threshold": "holdings",
            }.get(r["signal_type"], "other"),
            "slug": r["report_id"].replace(":", "-"),
            "is_event": r["signal_type"] in ("insider_tx", "holder_threshold", "inside_info"),
        }
        if item["is_event"]:
            h = headline_horizon(key, by_key_h)
            item["stat"] = by_key_h.get((key, h))
            item["horizon"] = h
            item["entry"] = entry_for(r["known_at"])
            d0 = day0_for(r["known_at"])
            item["day0"] = d0
            item["entry_date"] = (
                cal[cal.index(d0) + item["entry"]]
                if d0 is not None and item["entry"] is not None
                else None
            )
        return item

    feed, worth, events = [], [], []
    for r in today.iter_rows(named=True):
        item = make_item(r, day)
        feed.append(item)
        if item["is_event"]:
            events.append(item)
            st = item["stat"]
            if st is not None and st["verdict"] in WORTH_VERDICTS:
                worth.append(item)

    # archive: the signal events of each earlier digest, newest first
    archive: list[dict[str, Any]] = []
    signal_reports = reports.filter(
        pl.col("signal_type").is_in(["insider_tx", "holder_threshold", "inside_info"])
    )
    # recent sessions only: nothing from the far side of a hole in the lake
    recent = day - timedelta(days=2 * ARCHIVE_SESSIONS)
    earlier = [s for s in sorted(session_days) if recent <= s < day][-ARCHIVE_SESSIONS:]
    for s in reversed(earlier):
        a_start, a_end = digest_window(s, sorted(session_days))
        rows = signal_reports.filter((known > a_start) & (known <= a_end)).sort(
            "published_at_local", descending=True
        )
        items = [make_item(r, s) for r in rows.iter_rows(named=True)]
        if items:
            archive.append({"day": s, "events": items})
            events.extend(items)

    def rank(i: dict[str, Any]) -> tuple[Any, ...]:
        st = i["stat"]
        strength = (
            abs(st["oos_mean_net"] or 0)
            if st["verdict"] == "holds_up"
            else abs(st["mean_car"] or 0)
        )
        return (VERDICT_ORDER.get(st["verdict"], 9), -strength, i["report_id"])

    worth.sort(key=rank)

    # detail pages: path chart + closest past events of the same signal
    for e in events:
        p = paths_df.filter(pl.col("signal_key") == e["signal_key"]).sort("day").to_dicts()
        e["path"] = p
        e["chart"] = car_chart_svg(p, entry=e["entry"], horizon=e["horizon"])
        e["path_n"] = p[0]["n"] if p else 0
        past = (
            ev_cars.filter(
                (pl.col("signal_key") == e["signal_key"])
                & (pl.col("event_date") < e["pub_date"])
                & pl.col(f"car_{e['horizon']}").is_not_null()
            )
            .sort(["event_date", "report_id"], descending=True)
            .unique(subset=["isin", "event_date"], keep="first", maintain_order=True)
            .head(6)
            .join(
                reports.select(
                    "report_id", "ticker", "short_name", "insider_role", "insider_value"
                ),
                on="report_id",
                how="left",
            )
        )
        e["past"] = past.to_dicts()
        # a bad outcome for the rule shown: a fall, or a rise for a short
        st = e["stat"]
        e["tail_short"] = bool(
            st
            and st["verdict"] in ("holds_up", "unconfirmed", "eaten_by_costs")
            and (st.get("oos_last_rule") or "").startswith("short")
        )
        e["tail"] = None
        if st is not None and st.get("mean_car") is not None:
            e["tail"] = tail_outcome(
                ev_cars.filter(pl.col("signal_key") == e["signal_key"]),
                e["horizon"],
                short=e["tail_short"],
            )

    _add_company_context(work, data_dir, day, reports, events, sorted(session_days))
    tiers = _tiers(worth)
    record = _track_record([i for d in archive for i in d["events"]])
    pick_ctx = _pick_context(work, data_dir, day, reports, by_key_h, events)

    runs_df = lake.read_state(data_dir, "runs")
    runs = runs_df.sort("started_at", descending=True).to_dicts() if runs_df is not None else []
    # one row per run date: the latest run is the one that counts (reruns replace earlier ones)
    latest: dict[Any, dict[str, Any]] = {}
    for r in runs:
        latest.setdefault(r["run_date"], r)
    runs_by_day = sorted(latest.values(), key=lambda r: r["run_date"], reverse=True)
    for r in runs_by_day:
        r["reruns"] = sum(1 for x in runs if x["run_date"] == r["run_date"]) - 1
        r["within_slo"] = within_slo(r)
    run = next((r for r in runs if r["run_date"] == day), runs[0] if runs else {})
    if run:
        # a rerun only adds what earlier runs of the day missed: show the day's totals
        same = [x for x in runs if x["run_date"] == run["run_date"]]
        totals = ("reports_new", "prices_gpw_rows", "prices_nc_rows")
        run = {**run, **{k: sum(x.get(k) or 0 for x in same) for k in totals}}
    failed = 0
    for m in {day.replace(day=1), (day.replace(day=1) - timedelta(days=1)).replace(day=1)}:
        f = lake.read_state(data_dir, f"failed_reports/month={m:%Y-%m}")
        if f is not None:
            failed += f.height
    stuck = 0
    for m in {day.replace(day=1)}:
        f = lake.read_state(data_dir, f"failed_reports/month={m:%Y-%m}")
        if f is not None:
            stuck += f.filter(pl.col("attempts") >= 5).height

    sessions = ev_cars["event_date"] if ev_cars.height else pl.Series([], dtype=pl.Date)
    coverage = {
        "first_report": reports["pub_date"].min(),
        "last_report": reports["pub_date"].max(),
        "events_studied": ev_cars.height,
        "first_event": sessions.min() if len(sessions) else None,
    }
    counts = {
        "reports": today.height,
        "espi": today.filter(pl.col("system") == "ESPI").height,
        "ebi": today.filter(pl.col("system") == "EBI").height,
        "classified": today.filter(pl.col("signal_type").is_not_null()).height,
        "queued": failed,
        "insider": today.filter(pl.col("signal_type") == "insider_tx").height,
        "insider_buy": today.filter(pl.col("insider_side") == "buy").height,
        "insider_sell": today.filter(pl.col("insider_side") == "sell").height,
        "holder": today.filter(pl.col("signal_type") == "holder_threshold").height,
        "inside": today.filter(pl.col("signal_type") == "inside_info").height,
        "other": today.filter(pl.col("signal_type").is_in(["other", "periodic"])).height,
    }
    status = run.get("status", "unknown")
    health_bits: list[tuple[str, dict[str, Any]]] = [("Run {s}", {"s": status})]
    from undercurrent.pipeline import CLOSED_NOTE

    for note in (run.get("notes") or "").split("; "):
        if note.startswith(CLOSED_NOTE):
            health_bits.append(("exchange closed ({h})", {"h": note[len(CLOSED_NOTE) :]}))
    if run.get("nc_pending"):
        health_bits.append(("NewConnect prices pending", {}))
    if stuck:
        health_bits.append(("{n} reports failed 5+ times", {"n": stuck}))
    if failed and not stuck:
        health_bits.append(("{n} queued for retry", {"n": failed}))
    health_bits.append(("history since {d}", {"d": coverage["first_event"] or "—"}))
    # the pipeline table shows each day's totals too, not the last rerun's leftovers
    totals = ("reports_new", "prices_gpw_rows", "prices_nc_rows")
    for r in runs_by_day:
        same = [x for x in runs if x["run_date"] == r["run_date"]]
        r.update({k: sum(x.get(k) or 0 for x in same) for k in totals})
    return SiteContext(
        day=day,
        generated_at=datetime.now(UTC),
        run=run,
        counts=counts,
        worth=worth,
        feed=feed,
        stats=by_h,
        summary=summary,
        events=events,
        runs=runs_by_day[:60],
        coverage=coverage,
        health=health_text(health_bits, "en"),
        health_bits=health_bits,
        site_url=site_url,
        archive=archive,
        tiers=tiers,
        record=record,
        pick=pick_ctx,
    )


PICK_REPORT_DAYS = 90  # the pick page lists the company's reports of this many days


def _series(work: Path, isin: str, day: date, sessions: int) -> dict[str, Any]:
    """The stock's and WIG's daily returns over its last ``sessions`` sessions up to ``day``."""
    from undercurrent.transform import duckdb_path

    con = duckdb.connect(str(duckdb_path(work)))
    try:
        rows = con.execute(
            "select b.trade_date, a.adj_ret, b.wig_ret from benchmark_returns b "
            "left join adjusted_prices a on a.trade_date = b.trade_date and a.isin = ? "
            "where b.trade_date <= ? and b.wig_ret is not null order by b.trade_date",
            [isin, day],
        ).fetchall()
        last = con.execute(
            "select close, trade_date from adjusted_prices where isin = ? and trade_date <= ? "
            "order by trade_date desc limit 1",
            [isin, day],
        ).fetchone()
    finally:
        con.close()
    dates = [r[0] for r in rows]
    stock = [0.0 if r[1] is None else float(r[1]) for r in rows]
    wig = [float(r[2]) for r in rows]
    return {
        "dates": dates[-sessions:],
        "all_stock": stock,
        "all_wig": wig,
        "stock": stock[-sessions:],
        "wig": wig[-sessions:],
        "last_close": None if last is None else float(last[0]),
        "last_date": None if last is None else last[1],
    }


def _spark_series(work: Path, isins: list[str], day: date, sessions: int) -> dict[str, list[float]]:
    """Each stock's compounded return over its last ``sessions`` sessions, for sparklines."""
    from undercurrent.transform import duckdb_path

    if not isins:
        return {}
    con = duckdb.connect(str(duckdb_path(work)))
    try:
        rows = con.execute(
            "select isin, trade_date, adj_ret from adjusted_prices where list_contains(?, isin) "
            "and trade_date <= ? order by isin, trade_date",
            [isins, day],
        ).fetchall()
    finally:
        con.close()
    by: dict[str, list[float]] = {}
    for isin, _, r in rows:
        by.setdefault(isin, []).append(0.0 if r is None else float(r))
    return {k: _cum(v[-sessions:]) for k, v in by.items()}


def _cum(rets: list[float]) -> list[float]:
    out, acc = [], 1.0
    for r in rets:
        acc *= 1 + (r if math.isfinite(r) else 0.0)
        out.append(acc - 1)
    return out


def _pick_context(
    work: Path,
    data_dir: Path,
    day: date,
    reports: pl.DataFrame,
    by_key_h: dict[tuple[str, int], dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Everything the stock-of-the-week page shows, from ``gold/pick_*.parquet``."""
    gold = work / "gold"
    if not (gold / "pick_summary.parquet").exists():
        return None
    summary = pl.read_parquet(gold / "pick_summary.parquet").row(0, named=True)
    scores = pl.read_parquet(gold / "pick_scores.parquet")
    model = pl.read_parquet(gold / "pick_model.parquet").to_dicts()
    bt = pl.read_parquet(gold / "pick_backtest.parquet")
    # the basket's live record is the live record (pick.py, "The basket is the decision")
    live_path = gold / "pick_basket_live.parquet"
    live = (
        pl.read_parquet(live_path)
        if live_path.exists()
        else pl.DataFrame(schema={"week": pl.Date, "net": pl.Float64})
    )
    names = {
        r["isin"]: r["issuer_name"]
        for r in reports.filter(pl.col("isin").is_not_null())
        .sort("published_at_local")
        .select("isin", "issuer_name")
        .iter_rows(named=True)
    }
    ctx: dict[str, Any] = {"summary": summary, "model": model, "ready": scores.height > 0}
    for m in model:
        m["kind"] = pages.FEATURE_KINDS.get(m["feature"], "count")
    ctx["live"] = live.sort("week", descending=True).to_dicts()
    live_total = live["net"].drop_nans().drop_nulls()
    ctx["live_total"] = float((1 + live_total).product() - 1) if live_total.len() else None
    # The decision layer's basket (basket.py), as the pick wrote it.
    bpath = gold / "pick_basket.parquet"
    brows = pl.read_parquet(bpath).to_dicts() if bpath.exists() else []
    top_w = max((r["weight"] for r in brows), default=0.0) or 1.0
    for r in brows:
        r["name"] = names.get(r["isin"], r["ticker"])
        r["bar"] = round(r["weight"] / top_w * 100)
    invested = sum(r["weight"] for r in brows)
    blive_path = gold / "pick_basket_live.parquet"
    blive = (
        pl.read_parquet(blive_path)["net"].drop_nans().drop_nulls() if blive_path.exists() else None
    )
    from undercurrent import basket

    ctx["basket"] = {
        "rows": brows,
        "invested": invested,
        "wig": max(0.0, 1.0 - invested),
        "max_names": basket.K,
        "live_total": float((1 + blive).product() - 1)
        if blive is not None and blive.len()
        else None,
        "live_weeks": blive.len() if blive is not None else 0,
    }
    bpath_bt = gold / "pick_basket_backtest.parquet"
    bbt = pl.read_parquet(bpath_bt) if bpath_bt.exists() else pl.DataFrame()
    by_day = dict(zip(bbt["day"], bbt["net"], strict=True)) if bbt.height else {}
    bt_basket = [by_day.get(d, 0.0) for d in bt["day"].to_list()] if bt.height else []
    if bt.height:
        dates = bt["day"].to_list()
        ctx["backtest_chart"] = {
            lang: pages.lines_svg(
                [
                    (
                        "l-pick",
                        i18n.tr(lang)("The basket, after costs"),
                        _cum(bt_basket),
                    ),
                    (
                        "l-top",
                        i18n.tr(lang)("The single pick, after costs"),
                        _cum(bt["net"].to_list()),
                    ),
                    ("l-all", i18n.tr(lang)("All liquid stocks"), _cum(bt["basket"].to_list())),
                ],
                dates,
                lang,
                i18n.tr(lang)("Out-of-sample result against WIG"),
            )
            for lang in i18n.LANGS
        }
    if not ctx["ready"]:
        return ctx
    top = scores.head(10).to_dicts()
    sparks = _spark_series(work, [r["isin"] for r in top], day, 60)
    best = max((abs(r["expected"]) for r in top), default=0.0) or 1.0
    for r in top:
        r["name"] = names.get(r["isin"], r["ticker"])
        r["spark"] = pages.spark_svg(sparks.get(r["isin"], []))
        r["bar"] = round(abs(r["expected"]) / best * 100)
    ctx["top"] = top
    # The stock of the week is the pick's decision, not the page's: the basket's largest
    # position (or the top score, flagged, when nothing beats its cost).
    isin = summary.get("headline_isin") or top[0]["isin"]
    ctx["is_live"] = bool(summary.get("headline_live"))
    ctx["held_since"] = summary.get("headline_since")
    row = scores.filter(pl.col("isin") == isin)
    pick_row = row.row(0, named=True) if row.height else None
    ctx["pick"] = {
        "isin": isin,
        "ticker": pick_row["ticker"] if pick_row else summary.get("headline_ticker"),
        "weight": summary.get("headline_weight"),
        "source": summary.get("headline_source"),
        "name": names.get(isin, isin),
        "row": pick_row,
        "rank": pick_row["rank"] if pick_row else None,
        "universe": scores.height,
        "xtb_url": xtb.read_links(data_dir).get(isin),
        "logo": xtb.read_logos(data_dir).get(isin),
    }
    ctx["pick"]["initials"], ctx["pick"]["hue"] = pages.monogram(ctx["pick"]["ticker"])
    # the adopted model's own features (pick_model.parquet), not a list of the page's own
    features = [m["feature"] for m in model if f"c_{m['feature']}" in scores.columns]
    if pick_row is not None:
        contrib = sorted(
            ((f, pick_row[f"c_{f}"], pick_row[f"f_{f}"]) for f in features),
            key=lambda t: -abs(t[1]),
        )
        weights = {m["feature"]: m["weight"] for m in model}
        from scipy.stats import norm

        ctx["pick"]["why"] = [
            {
                "feature": f,
                "contribution": c,
                "value": v,
                # share of liquid stocks with a lower value (the feature is a rank-normal score)
                "above": round(float(norm.cdf(c / weights[f])) * 100) if weights.get(f) else None,
            }
            for f, c, v in contrib[:6]
            if c
        ]
        biggest = max((abs(w["contribution"]) for w in ctx["pick"]["why"]), default=0.0) or 1.0
        for w in ctx["pick"]["why"]:
            w["bar"] = round(abs(w["contribution"]) / biggest * 100)
    s = _series(work, isin, day, 252)
    ctx["pick"]["last_close"], ctx["pick"]["last_date"] = s["last_close"], s["last_date"]
    if s["dates"]:
        from undercurrent.study import signal_key as _key

        marks = []
        first = s["dates"][0]
        for r in reports.filter(
            (pl.col("isin") == isin)
            & (pl.col("pub_date") >= first)
            & pl.col("signal_type").is_in(["insider_tx", "holder_threshold", "inside_info"])
        ).iter_rows(named=True):
            marks.append((r["pub_date"], _key(r["signal_type"], r["subtype"], r["insider_side"])))
        ctx["pick"]["chart"] = {
            lang: pages.spotlight_svg(
                s["dates"],
                _cum(s["stock"]),
                _cum(s["wig"]),
                [(d, label_for(k, lang)) for d, k in marks],
                ctx["pick"]["ticker"] or isin,
                lang,
            )
            for lang in i18n.LANGS
        }
        rets = {}
        for label, n in (("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)):
            st, wg = s["all_stock"][-n:], s["all_wig"][-n:]
            if len(st) == n:
                rets[label] = (_cum(st)[-1] - _cum(wg)[-1], _cum(st)[-1])
        ctx["pick"]["returns"] = rets
    # The forecast's own band (pick.py), not a figure the page works out for itself.
    lo = (pick_row or {}).get("band_lo")
    ctx["pick"]["bad_case"] = float(lo) if lo is not None and math.isfinite(lo) else None
    with_pages = {e["report_id"] for e in events}
    since = day - timedelta(days=PICK_REPORT_DAYS)
    recent = reports.filter(
        (pl.col("isin") == isin) & pl.col("pub_date").is_between(since, day)
    ).sort("known_at", descending=True)
    from undercurrent.study import signal_key

    items = []
    for r in recent.iter_rows(named=True):
        is_event = r["signal_type"] in ("insider_tx", "holder_threshold", "inside_info")
        key = signal_key(r["signal_type"], r["subtype"], r["insider_side"])
        h = headline_horizon(key, by_key_h)
        items.append(
            {
                **r,
                "signal_key": key,
                "label_key": key if is_event else r["signal_type"],
                "is_event": is_event,
                "stat": by_key_h.get((key, h)) if is_event else None,
                "horizon": h,
                "slug": r["report_id"].replace(":", "-"),
                "has_page": r["report_id"] in with_pages,
            }
        )
    ctx["pick"]["reports"] = items
    return ctx


def _tiers(worth: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Today's events worth a look as sections by verdict, groups by signal type.

    Every event of one signal type shares the same history, so a group shows it once and
    lists the companies under it. ``worth`` is already ranked; the order is kept.
    """
    out: list[dict[str, Any]] = []
    for v in WORTH_VERDICTS:
        groups: dict[str, dict[str, Any]] = {}
        for e in worth:
            if e["stat"]["verdict"] != v:
                continue
            g = groups.setdefault(
                e["signal_key"],
                {"key": e["signal_key"], "stat": e["stat"], "horizon": e["horizon"], "events": []},
            )
            g["events"].append(e)
        if groups:
            n = sum(len(g["events"]) for g in groups.values())
            out.append({"verdict": v, "groups": list(groups.values()), "n": n})
    return out


def _add_company_context(
    work: Path,
    data_dir: Path,
    day: date,
    reports: pl.DataFrame,
    events: list[dict[str, Any]],
    sessions: list[date],
) -> None:
    """Report text, the stock's price context, its other reports and an XTB link, per event."""
    from undercurrent.transform import duckdb_path

    isins = sorted({e["isin"] for e in events if e.get("isin")})
    hist: dict[str, pages.PriceHistory] = {}
    wig: dict[date, float] = {}
    if isins:
        first = min(e["pub_date"] for e in events) - timedelta(days=200)
        con = duckdb.connect(str(duckdb_path(work)))
        try:
            px = con.execute(
                "select isin, trade_date, close, adj_close, adj_ret, coalesce(value_pln, 0) "
                "from adjusted_prices where list_contains(?, isin) and trade_date >= ? "
                "and trade_date <= ? order by isin, trade_date",
                [isins, first, day],
            ).fetchall()
            wig = {
                d: float(r)
                for d, r in con.execute(
                    "select trade_date, wig_ret from benchmark_returns "
                    "where wig_ret is not null and trade_date >= ? and trade_date <= ?",
                    [first, day],
                ).fetchall()
            }
        finally:
            con.close()
        for isin, d, close, adj, ret, val in px:
            h = hist.setdefault(isin, pages.PriceHistory([], [], [], [], []))
            h.dates.append(d)
            h.close.append(float(close))
            h.adj_close.append(float(adj))
            h.ret.append(None if ret is None else float(ret))
            h.turnover.append(float(val))
    state = _state_rows(work, isins, day)
    forecasts = _forecast_rows(work, day)
    links = xtb.read_links(data_dir)
    with_pages = {e["report_id"] for e in events}
    by_isin = {
        k[0]: v
        for k, v in reports.filter(pl.col("isin").is_not_null())
        .sort("published_at_local", descending=True)
        .partition_by("isin", as_dict=True)
        .items()
    }
    for e in events:
        text = pages.clean_report_text(e.get("body_text"))
        e["full_text"] = text
        e["lead"] = pages.report_lead(text)
        e["amounts"] = pages.money_amounts(text)
        e["xtb_url"] = links.get(e.get("isin") or "")
        isin = e.get("isin") or ""
        e["company"] = _company(
            e, hist.get(isin), wig, sessions, day, state.get(isin), forecasts.get(isin)
        )
        same = by_isin.get(e.get("isin"))
        e["history"] = []
        if same is not None:
            rows = same.filter(
                (pl.col("report_id") != e["report_id"]) & (pl.col("pub_date") <= day)
            ).head(COMPANY_HISTORY)
            for r in rows.iter_rows(named=True):
                e["history"].append(
                    {
                        **r,
                        "slug": r["report_id"].replace(":", "-"),
                        "has_page": r["report_id"] in with_pages,
                    }
                )


def _state_rows(work: Path, isins: list[str], day: date) -> dict[str, pl.DataFrame]:
    """The market state's rows for these stocks up to ``day``, per stock (none without it)."""
    path = work / "gold" / "market_state.parquet"
    if not isins or not path.exists():
        return {}
    cols = ["isin", "trade_date", "surprise", "sigma_20", "cost", "liquidity_tier"]
    cols += ["median_close", "median_turnover"]
    df = (
        pl.scan_parquet(path)
        .select(cols)
        .filter(pl.col("isin").is_in(isins) & (pl.col("trade_date") <= day))
        .sort(["isin", "trade_date"])
        .collect()
    )
    return {str(k[0]): v for k, v in df.partition_by("isin", as_dict=True).items()}


def _forecast_rows(work: Path, day: date) -> dict[str, dict[str, Any]]:
    """The forecast per stock (``pick_scores``), if it is this run's (as of ``day``)."""
    gold = work / "gold"
    if not (gold / "pick_scores.parquet").exists() or not (gold / "pick_summary.parquet").exists():
        return {}
    summary = pl.read_parquet(gold / "pick_summary.parquet").row(0, named=True)
    from undercurrent.study import as_of_for

    if summary.get("as_of") != as_of_for(day):  # a work dir left over from another day
        return {}
    scores = pl.read_parquet(gold / "pick_scores.parquet")
    n = scores.height
    return {r["isin"]: {**r, "universe": n} for r in scores.iter_rows(named=True)}


def _finite(x: Any) -> float | None:
    return float(x) if x is not None and math.isfinite(x) else None


def _company(
    e: dict[str, Any],
    h: pages.PriceHistory | None,
    wig: dict[date, float],
    sessions: list[date],
    day: date,
    st: pl.DataFrame | None = None,
    fc: dict[str, Any] | None = None,
) -> pages.CompanyContext:
    """Price, reaction, liquidity and result since entry of the stock of one event.

    Liquidity, the day-0 surprise, σ, cost and the forecast come from the shared layers
    (the market state and the pick) when they exist; the page computes none of them.
    """
    c = pages.CompanyContext()
    if h is None or not h.dates:
        return c
    c.last_close, c.last_date = h.close[-1], h.dates[-1]
    d0 = e.get("day0")
    ref = d0 if d0 is not None and d0 <= day else e["pub_date"]
    before = st.filter(pl.col("trade_date") < ref) if st is not None else None
    if before is not None and before.height and before["liquidity_tier"][-1] is not None:
        last = before.row(-1, named=True)
        c.median_close = _finite(last["median_close"])
        c.median_turnover = _finite(last["median_turnover"])
        c.tier = last["liquidity_tier"]
        c.buyable = c.tier != "thin"
    else:
        c.median_close, c.median_turnover, c.buyable = pages.liquidity(h, ref)
    if c.buyable is None:  # no history before the event (a hole in the lake): use the latest
        c.median_close, c.median_turnover, c.buyable = pages.liquidity(
            h, h.dates[-1] + timedelta(days=1)
        )
    if st is not None and st.height:
        if d0 is not None:
            on = st.filter(pl.col("trade_date") == d0)
            c.surprise = _finite(on["surprise"][0]) if on.height else None
        latest = st.row(-1, named=True)
        c.sigma_20, c.cost = _finite(latest["sigma_20"]), _finite(latest["cost"])
    c.forecast = fc
    if d0 is not None and d0 in h.dates and d0 in wig:
        c.day0_ret, c.day0_wig = h.ret[h.dates.index(d0)], wig[d0]
    entry = e.get("entry_date")
    if entry is not None and entry <= day:
        c.since_entry, c.since_sessions = pages.realised_excess(
            h, wig, sessions, entry, e["horizon"]
        )
    c.spark = {lang: pages.sparkline_svg(h, day, d0, lang) for lang in i18n.LANGS}
    return c


def _track_record(archived: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How the archive's events have done since entry so far, per verdict of their type.

    One event per stock, signal and day 0. Most windows are still open: an early read on a
    small sample, and the page says so.
    """
    seen: set[tuple[Any, ...]] = set()
    by_v: dict[str, list[float]] = {}
    for e in archived:
        st = e.get("stat")
        c = e.get("company")
        if not st or c is None or c.since_entry is None:
            continue
        k = (e.get("isin"), e["signal_key"], e.get("day0"))
        if k in seen:
            continue
        seen.add(k)
        by_v.setdefault(st["verdict"], []).append(c.since_entry)
    out = []
    for v in (*WORTH_VERDICTS, "noise", "too_few"):
        xs = by_v.get(v)
        if xs:
            out.append(
                {
                    "verdict": v,
                    "n": len(xs),
                    "mean": sum(xs) / len(xs),
                    "better": sum(1 for x in xs if x > 0) / len(xs),
                }
            )
    return out


# --------------------------------------------------------------------------- site


def _env(lang: str = "en") -> Environment:
    env = Environment(
        loader=FileSystemLoader(SITE_DIR / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    _ = i18n.tr(lang)

    def lookup(table: dict[str, dict[str, str]]) -> Any:
        return lambda k: table[lang].get(k or "", k or "—") if k else "—"

    env.filters |= {
        "pct": lambda x: pct(x, lang),
        "share": lambda x: pct(x, lang, sign=False),
        "num": lambda x: num(x, lang),
        "short_pln": lambda x: short_pln(x, lang),
        "tone": tone,
        "label": lambda k: label_for(k, lang),
        "basis": lambda k: i18n.BASIS_LABELS[lang].get((k or "").split(":")[0], ""),
        "verdict": lookup(i18n.VERDICT_LABELS),
        "role": lookup(i18n.ROLE_LABELS),
        "side": lookup(i18n.SIDE_LABELS),
        "venue": lambda k: i18n.VENUE_LABELS[lang].get(k, k) if k else "",
        "fact": lambda r: _fact_line(r, lang),
        "chart": lambda p, entry=1, horizon=None: car_chart_svg(
            p, lang=lang, entry=entry, horizon=horizon
        ),
        "summary": lambda stat, h: pages.plain_summary(stat, h, lang),
        "company": pages.company_name,
        "featval": lambda f, v: pages.feature_value(f, v, lang),
        "feature": lambda f: i18n.FEATURE_LABELS[lang].get(f, f),
        "pickverdict": lambda v: i18n.PICK_VERDICTS[lang].get(v or "", v or ""),
        "pickexplain": lambda v: i18n.PICK_EXPLAIN[lang].get(v or "", ""),
        "period": lambda h: pages.horizon_words(h, lang),
        "explain": lambda v: i18n.VERDICT_EXPLAIN[lang].get(v or "", ""),
        "tier": lambda v: i18n.TIER_TITLES[lang].get(v or "", ""),
        "dm": lambda d: f"{d:%d.%m}" if d else "",
        "longdate": lambda d: i18n.long_date(d, lang),
        "health": lambda bits: health_text(bits, lang),
        "decimal": lambda x, d=2: (
            "—"
            if not isinstance(x, int | float) or (isinstance(x, float) and math.isnan(x))
            else i18n.group_digits(f"{x:.{d}f}", lang)
        ),
        "pct2": lambda x: i18n.pct(x, lang, digits=2),
        "qval": lambda q: (
            "—"
            if not isinstance(q, int | float) or (isinstance(q, float) and math.isnan(q))
            else ("< " + i18n.group_digits("0.01", lang))
            if q < 0.01
            else i18n.group_digits(f"{q:.2f}", lang)
        ),
    }
    env.globals |= {
        "tz": WARSAW,
        "_": _,
        "lang": lang,
        "plural": lambda n, key: i18n.plural(lang, n, key),
        "ring": lambda rank, universe: pages.ring_svg(rank, universe, lang),
    }
    return env


def _render_lang(ctx: SiteContext, out: Path, lang: str) -> list[Path]:
    """Render every page of one language. Polish lives at the root, others in ``<lang>/``."""
    default = lang == i18n.DEFAULT_LANG
    base = out if default else out / lang
    (base / "event").mkdir(parents=True, exist_ok=True)
    env = _env(lang)
    up = "" if default else "../"  # from a top-level page of this language to the site root
    written: list[Path] = []

    def write(name: str, template: str, depth: int = 0, **kw: Any) -> None:
        to_lang_root = "../" * depth
        to_site_root = to_lang_root + up
        other = f"{to_site_root}en/{name}" if default else f"{to_site_root}{name}"
        text = env.get_template(template).render(
            ctx=ctx,
            root=to_lang_root,
            assets=to_site_root,
            other_lang_url=other,
            other_lang="en" if default else i18n.DEFAULT_LANG,
            **kw,
        )
        (base / name).write_text(text, encoding="utf-8")
        written.append(base / name)

    write("index.html", "today.html", page="today")
    for h in ctx.stats:
        write(f"signals-{h}d.html", "signals.html", page="signals", horizon=h)
    shutil.copy(base / f"signals-{HORIZON_DEFAULT}d.html", base / "signals.html")
    write("pipeline.html", "pipeline.html", page="pipeline")
    write("archive.html", "archive.html", page="archive")
    write("glossary.html", "glossary.html", page="glossary")
    write("pick.html", "pick.html", page="pick")
    for e in ctx.events:
        write(f"event/{e['slug']}.html", "event.html", depth=1, page="event", e=e)
    return written


def render_site(ctx: SiteContext, out: Path) -> list[Path]:
    """Write the static site into ``out``: Polish at the root, English under ``en/``."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE_DIR / "static", out / "static")
    written = [p for lang in i18n.LANGS for p in _render_lang(ctx, out, lang)]
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return written


# --------------------------------------------------------------------------- telegram


def digest_message(ctx: SiteContext, max_items: int = 5) -> tuple[str, dict[str, Any]]:
    """Telegram HTML message + inline keyboard for the nightly digest."""
    e = html.escape
    d = ctx.day
    lines = [
        f"<b>GPW digest · {d:%a} {d.day} {d:%b}</b>",
        f"{ctx.counts['reports']} reports · {len(ctx.worth)} of kinds that have moved prices",
        "",
    ]
    for item in ctx.worth[:max_items]:
        st = item["stat"]
        verdict = {
            "holds_up": "✓ holds up",
            "unconfirmed": "not confirmed out of sample",
            "eaten_by_costs": "eaten by costs",
            "untested": "not yet tested",
            "noise": "probably noise",
            "too_few": "too few events",
        }.get(st["verdict"], st["verdict"])
        lines.append(f"<b>{e(item['ticker'])}</b> · {e(item['label'])}: {e(item['fact'][:120])}")
        h = item.get("horizon", HORIZON_DEFAULT)
        lines.append(f"Past: {pct(st['mean_car'])} / {h}d after entry · n={st['n']} · {verdict}")
    if not ctx.worth:
        lines.append("No event today is of a type that has moved prices significantly.")
    pk = ctx.pick
    if pk and pk.get("ready") and pk.get("pick"):
        verdict = i18n.PICK_VERDICTS["en"].get(pk["summary"]["verdict"], "")
        lines += [
            "",
            f"Stock of the week: <b>{e(pk['pick']['ticker'] or '')}</b> · model: {verdict}",
        ]
    lines += ["", f"<i>{e(ctx.health)}</i>", "Not investment advice. Historical statistics only."]
    buttons = [{"text": "Open dashboard", "url": ctx.site_url}]
    if ctx.worth:
        first = ctx.worth[0]
        buttons.append(
            {
                "text": f"{first['ticker']} details",
                "url": f"{ctx.site_url}event/{first['slug']}.html",
            }
        )
    return "\n".join(lines), {"inline_keyboard": [buttons]}


def send_telegram(
    text: str, markup: dict[str, Any] | None = None, transport: httpx.BaseTransport | None = None
) -> bool:
    """POST a message to the configured chat. Returns False (and logs) if not configured."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); skipping")
        return False
    payload: dict[str, Any] = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = json.dumps(markup)
    with httpx.Client(timeout=30, transport=transport) as c:
        r = c.post(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
    if r.status_code != 200:
        # never echo the URL (it contains the token)
        log.error("Telegram send failed: HTTP %s %s", r.status_code, r.text[:200])
        return False
    log.info("Telegram message sent (%d chars)", len(text))
    return True


def send_alert(message: str) -> bool:
    """Short alert instead of the digest (job failed or data stale)."""
    return send_telegram(f"⚠️ <b>Undercurrent alert</b>\n{html.escape(message)}")


def digest_markdown(ctx: SiteContext) -> str:
    """Plain archive copy of the digest (committed to main as digests/YYYY/MM/DD.md)."""
    text, _ = digest_message(ctx)
    import re

    md = re.sub(r"</?b>", "**", text)
    md = re.sub(r"</?i>", "_", md)
    return html.unescape(md) + "\n"


def publish(
    work: Path,
    data_dir: Path,
    day: date,
    *,
    out: Path = Path("_site"),
    digest_dir: Path = Path("digests"),
    send: bool = True,
    site_url: str | None = None,
    digest: bool = True,
) -> SiteContext:
    """Render the site and (optionally) archive and send the digest.

    ``digest=False`` (an exchange holiday) renders the site only: the day's reports go into
    the next session's digest, so no digest file is written for this day.
    """
    ctx = gather(work, data_dir, day, site_url or os.environ.get("SITE_URL", DEFAULT_SITE_URL))
    files = render_site(ctx, out)
    log.info("site: %d pages in %s", len(files), out)
    if digest:
        arch = digest_dir / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(digest_markdown(ctx), encoding="utf-8")
    if send:
        text, markup = digest_message(ctx)
        send_telegram(text, markup)
    return ctx


def expected_run_date(now: datetime) -> date:
    """Latest session day whose nightly run should have finished by ``now`` (20:15 Warsaw).

    Weekends and exchange holidays have no run due.
    """
    from undercurrent import gpw_calendar

    local = now.astimezone(WARSAW)
    day = local.date()
    if local.strftime("%H:%M") < "20:15":
        day -= timedelta(days=1)
    return gpw_calendar.last_session(day)


def is_stale(data_dir: Path, now: datetime | None = None) -> str | None:
    """Reason string if the latest due weekday run hasn't succeeded, else None."""
    today = expected_run_date(now or datetime.now(UTC))
    runs = lake.read_state(data_dir, "runs")
    if runs is None or runs.height == 0:
        return "no runs recorded"
    ok = runs.filter(pl.col("status").is_in(["ok", "degraded"]) & (pl.col("run_date") == today))
    if ok.height == 0:
        last = str(runs["run_date"].max())
        return f"no successful run for {today} (last run date: {last})"
    return None
