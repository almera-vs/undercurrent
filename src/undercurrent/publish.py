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

from undercurrent import lake

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
SITE_DIR = Path(__file__).resolve().parents[2] / "site"
DEFAULT_SITE_URL = "https://almera-vs.github.io/undercurrent/"
HORIZON_DEFAULT = 20
VERDICT_ORDER = {"holds_up": 0, "eaten_by_costs": 1, "noise": 2}
VERDICT_LABEL = {"holds_up": "Holds up", "eaten_by_costs": "Eaten by costs", "noise": "Noise"}

SIGNAL_LABELS = {
    "insider_tx:buy": "Insider buy",
    "insider_tx:sell": "Insider sell",
    "insider_tx:unknown": "Insider transaction (details in PDF)",
    "holder_threshold": "Holder threshold crossed",
    "inside_info:profit_warning": "Results estimate / profit warning",
    "inside_info:large_contract": "Large contract",
    "inside_info:buyback": "Buyback",
    "inside_info:dividend_change": "Dividend decision",
    "inside_info:m_and_a": "M&A / tender offer",
    "inside_info:financing": "Financing (bonds, loans, issues)",
    "inside_info:strategy": "Strategy",
    "inside_info:litigation": "Litigation",
    "inside_info:rating": "Rating",
    "inside_info:other": "Other inside information",
}
BASIS_LABELS = {
    "insider_tx": "Art. 19 MAR",
    "holder_threshold": "Art. 69 POA",
    "inside_info": "Art. 17 MAR",
}
ROLE_LABELS = {
    "CEO": "CEO",
    "CFO": "CFO",
    "board": "board",
    "supervisory_board": "supervisory board",
    "closely_associated": "closely associated",
}


def label_for(key: str) -> str:
    """Human label for a signal key."""
    return SIGNAL_LABELS.get(key, key.replace("_", " ").replace(":", " · "))


# --------------------------------------------------------------------------- formatting


def pct(x: float | None, digits: int = 1, sign: bool = True) -> str:
    """``0.031`` → ``+3.1%``; ``None`` → ``—``."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:{'+' if sign else ''}.{digits}f}%"


def num(x: float | None) -> str:
    """Thousands with thin grouping, or ``—``."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if abs(x) >= 100 or float(x).is_integer():
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def short_pln(x: float | None) -> str:
    """``772800`` → ``773k``."""
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}m"
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}k"
    return f"{x:.0f}"


def tone(x: float | None) -> str:
    """CSS class: blue for up, orange for down."""
    if x is None or (isinstance(x, float) and math.isnan(x)) or x == 0:
        return "flat"
    return "up" if x > 0 else "down"


# --------------------------------------------------------------------------- SVG chart


def car_chart_svg(path: list[dict[str, Any]], width: int = 900, height: int = 380) -> str:
    """Average CAR path [-5, +20] with bootstrap band as inline SVG."""
    if not path:
        return '<p class="muted">Not enough past events for a chart yet.</p>'
    pad_l, pad_r, pad_t, pad_b = 56, 24, 20, 36
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
        f'aria-label="Average excess return path, day -5 to +20">',
        *(
            f'<line class="grid" x1="{pad_l}" x2="{width - pad_r}" '
            f'y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y(v) + 4:.1f}" text-anchor="end">'
            f"{v * 100:+.1f}%</text>"
            for v in ticks
        ),
        f'<line class="zero" x1="{pad_l}" x2="{width - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>',
        f'<line class="event" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="{pad_t}" y2="{height - pad_b}"/>',
        f'<text class="tick" x="{x(0) + 8:.1f}" y="{pad_t + 12}">report published</text>',
        f'<polygon class="band band-{end_tone}" points="{band}"/>',
        f'<polyline class="line line-{end_tone}" points="{line}"/>',
        f'<circle class="dot-{end_tone}" cx="{lx:.1f}" cy="{ly:.1f}" r="5"/>',
        f'<text class="tick strong" x="{lx - 8:.1f}" y="{ly - 12:.1f}" text-anchor="end">'
        f"{pct(last['mean_car'])}</text>",
        *(
            f'<text class="tick" x="{x(d):.1f}" y="{height - 10}" text-anchor="middle">'
            f"{'day 0' if d == 0 else f'{d:+d}'}</text>"
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
    steps: list[str] = field(default_factory=list)


def _report_days(day: date) -> list[date]:
    """Calendar days covered by a run on ``day``: since the previous weekday."""
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return [prev + timedelta(days=i) for i in range(1, (day - prev).days + 1)]


def _fact_line(r: dict[str, Any]) -> str:
    """One line of fact for a card / digest, built only from parsed fields or the title."""
    if r["signal_type"] == "insider_tx":
        who = ROLE_LABELS.get(r.get("insider_role") or "", "Insider")
        side = {"buy": "bought", "sell": "sold", "pledge": "pledged", "mixed": "traded"}.get(
            r.get("insider_side") or ""
        )
        if side and r.get("insider_volume"):
            s = f"{who} {side} {num(r['insider_volume'])} shares"
            if r.get("insider_avg_price"):
                s += f" at {r['insider_avg_price']:.2f} PLN"
            if r.get("insider_value"):
                s += f" ({short_pln(r['insider_value'])} PLN)"
            return s + "."
        if side:
            return f"{who} {side} shares; volume and price are only in the attached PDF."
        return f"Art. 19 MAR notification ({who}); trade details are only in the attached PDF."
    return r.get("title") or r.get("subject") or "(no title)"


def gather(work: Path, data_dir: Path, day: date, site_url: str = DEFAULT_SITE_URL) -> SiteContext:
    """Collect all site/digest data for ``day`` from the work dir and the lake."""
    from undercurrent.study import HORIZONS, signal_key
    from undercurrent.transform import duckdb_path

    con = duckdb.connect(str(duckdb_path(work)))
    try:
        reports = con.sql(
            "select report_id, site, system, kind, issuer_name, ticker, short_name, isin, title, "
            "subject, legal_basis, signal_type, subtype, insider_person, insider_role, "
            "insider_side, insider_volume, insider_avg_price, insider_value, "
            "insider_trade_date, insider_venue, insider_parse_notes, published_at_local, "
            "known_at, source_url, report_number from reports"
        ).pl()
    finally:
        con.close()
    stats_df = pl.read_parquet(work / "gold" / "signal_stats.parquet")
    paths_df = pl.read_parquet(work / "gold" / "car_paths.parquet")
    ev_cars = pl.read_parquet(work / "gold" / "event_cars.parquet")

    reports = reports.with_columns(
        pl.col("published_at_local").str.slice(0, 10).str.to_date().alias("pub_date"),
        pl.col("published_at_local").str.slice(11, 5).alias("pub_time"),
    )
    days = _report_days(day)
    today = reports.filter(pl.col("pub_date").is_in(days)).sort(
        "published_at_local", descending=True
    )

    stats_rows = stats_df.to_dicts()
    by_h: dict[int, list[dict[str, Any]]] = {h: [] for h in HORIZONS}
    for r in stats_rows:
        r["label"] = label_for(r["signal_key"])
        r["basis"] = BASIS_LABELS.get(r["signal_key"].split(":")[0], "")
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
    summary = {
        "tested": len(stat20),
        **{v: sum(1 for r in stat20.values() if r["verdict"] == v) for v in VERDICT_ORDER},
    }

    feed, worth, events = [], [], []
    for r in today.iter_rows(named=True):
        key = signal_key(r["signal_type"], r["subtype"], r["insider_side"])
        item = {
            **r,
            "signal_key": key,
            "label": label_for(key)
            if r["signal_type"] not in ("other", "periodic")
            else r["signal_type"].capitalize(),
            "fact": _fact_line(r),
            "ticker": r["ticker"] or r["short_name"] or "—",
            "filter": {
                "insider_tx": "insider",
                "inside_info": "inside",
                "holder_threshold": "holdings",
            }.get(r["signal_type"], "other"),
            "slug": r["report_id"].replace(":", "-"),
        }
        feed.append(item)
        if r["signal_type"] in ("insider_tx", "holder_threshold", "inside_info"):
            st = stat20.get(key)
            item["stat"] = st
            events.append(item)
            if st is not None and st.get("n"):
                worth.append(item)

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
        e["chart"] = car_chart_svg(p)
        e["path_n"] = p[0]["n"] if p else 0
        past = (
            ev_cars.filter(
                (pl.col("signal_key") == e["signal_key"])
                & (pl.col("event_date") < e["pub_date"])
                & pl.col("car_20").is_not_null()
            )
            .sort("event_date", descending=True)
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
        e["worst"] = (
            ev_cars.filter(pl.col("signal_key") == e["signal_key"])["car_20"].min()
            if ev_cars.height
            else None
        )

    runs_df = lake.read_state(data_dir, "runs")
    runs = runs_df.sort("started_at", descending=True).to_dicts() if runs_df is not None else []
    run = next((r for r in runs if r["run_date"] == day), runs[0] if runs else {})
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
    health_bits = [f"Run {status}"]
    if run.get("nc_pending"):
        health_bits.append("NewConnect prices pending")
    if stuck:
        health_bits.append(f"{stuck} reports failed 5+ times")
    if failed and not stuck:
        health_bits.append(f"{failed} queued for retry")
    health_bits.append(f"history since {coverage['first_event'] or '—'}")
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
        runs=runs[:60],
        coverage=coverage,
        health=" · ".join(health_bits),
        site_url=site_url,
    )


# --------------------------------------------------------------------------- site


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(SITE_DIR / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters |= {"pct": pct, "num": num, "short_pln": short_pln, "tone": tone}
    env.globals |= {"VERDICT_LABEL": VERDICT_LABEL, "tz": WARSAW}
    return env


def render_site(ctx: SiteContext, out: Path) -> list[Path]:
    """Write the static site into ``out``. Returns the files written."""
    if out.exists():
        shutil.rmtree(out)
    (out / "event").mkdir(parents=True)
    shutil.copytree(SITE_DIR / "static", out / "static")
    env = _env()
    written: list[Path] = []

    def write(name: str, template: str, **kw: Any) -> None:
        p = out / name
        p.write_text(env.get_template(template).render(ctx=ctx, **kw), encoding="utf-8")
        written.append(p)

    write("index.html", "today.html", page="today", root="")
    for h in ctx.stats:
        write(f"signals-{h}d.html", "signals.html", page="signals", root="", horizon=h)
    shutil.copy(out / f"signals-{HORIZON_DEFAULT}d.html", out / "signals.html")
    write("pipeline.html", "pipeline.html", page="pipeline", root="")
    for e in ctx.events:
        write(f"event/{e['slug']}.html", "event.html", page="event", root="../", e=e)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return written


# --------------------------------------------------------------------------- telegram


def digest_message(ctx: SiteContext, max_items: int = 5) -> tuple[str, dict[str, Any]]:
    """Telegram HTML message + inline keyboard for the nightly digest."""
    e = html.escape
    d = ctx.day
    lines = [
        f"<b>GPW digest · {d:%a} {d.day} {d:%b}</b>",
        f"{ctx.counts['reports']} reports · {len(ctx.worth)} with a track record",
        "",
    ]
    for item in ctx.worth[:max_items]:
        st = item["stat"]
        verdict = {
            "holds_up": "✓ holds up",
            "eaten_by_costs": "eaten by costs",
            "noise": "probably noise",
        }[st["verdict"]]
        lines.append(f"<b>{e(item['ticker'])}</b> · {e(item['label'])}: {e(item['fact'][:120])}")
        lines.append(f"Past: {pct(st['mean_car'])} / 20d · n={st['n']} · {verdict}")
    if not ctx.worth:
        lines.append("No event today has enough history for statistics yet.")
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
) -> SiteContext:
    """Render the site and (optionally) send the digest."""
    ctx = gather(work, data_dir, day, site_url or os.environ.get("SITE_URL", DEFAULT_SITE_URL))
    files = render_site(ctx, out)
    log.info("site: %d pages in %s", len(files), out)
    arch = digest_dir / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.md"
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text(digest_markdown(ctx), encoding="utf-8")
    if send:
        text, markup = digest_message(ctx)
        send_telegram(text, markup)
    return ctx


def is_stale(data_dir: Path, now: datetime | None = None) -> str | None:
    """Reason string if today's (Warsaw) weekday run hasn't succeeded, else None."""
    now = now or datetime.now(UTC)
    today = now.astimezone(WARSAW).date()
    if today.weekday() >= 5:
        return None
    runs = lake.read_state(data_dir, "runs")
    if runs is None or runs.height == 0:
        return "no runs recorded"
    ok = runs.filter(pl.col("status").is_in(["ok", "degraded"]) & (pl.col("run_date") == today))
    if ok.height == 0:
        last = str(runs["run_date"].max())
        return f"no successful run for {today} (last run date: {last})"
    return None
