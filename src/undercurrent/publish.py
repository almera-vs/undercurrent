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

from undercurrent import i18n, lake

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
SITE_DIR = Path(__file__).resolve().parents[2] / "site"
DEFAULT_SITE_URL = "https://almera-vs.github.io/undercurrent/"
HORIZON_DEFAULT = 20
VERDICT_ORDER = {"holds_up": 0, "eaten_by_costs": 1, "noise": 2}
VERDICT_LABEL = i18n.VERDICT_LABELS["en"]


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


def car_chart_svg(
    path: list[dict[str, Any]], width: int = 900, height: int = 380, lang: str = "en"
) -> str:
    """Average CAR path [-5, +20] with bootstrap band as inline SVG."""
    _ = i18n.tr(lang)
    if not path:
        return f'<p class="muted">{_("Not enough past events for a chart yet.")}</p>'
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
        f'aria-label="{_("Average excess return path, day -5 to +20")}">',
        *(
            f'<line class="grid" x1="{pad_l}" x2="{width - pad_r}" '
            f'y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y(v) + 4:.1f}" text-anchor="end">'
            f"{pct(v, lang)}</text>"
            for v in ticks
        ),
        f'<line class="zero" x1="{pad_l}" x2="{width - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>',
        f'<line class="event" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="{pad_t}" y2="{height - pad_b}"/>',
        f'<text class="tick" x="{x(0) + 8:.1f}" y="{pad_t + 12}">{_("report published")}</text>',
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


def health_text(bits: list[tuple[str, dict[str, Any]]], lang: str) -> str:
    """One-line run health summary, e.g. ``Run ok · history since 2015-03-02``."""
    _ = i18n.tr(lang)
    out = []
    for tmpl, kw in bits:
        kw = {k: (_(v) if isinstance(v, str) and k == "s" else v) for k, v in kw.items()}
        out.append(_(tmpl, **kw))
    text = " · ".join(out)
    return text[:1].upper() + text[1:]


def _report_days(day: date) -> list[date]:
    """Calendar days covered by a run on ``day``: since the previous weekday."""
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return [prev + timedelta(days=i) for i in range(1, (day - prev).days + 1)]


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
        return r.get("title") or r.get("subject") or "(no title)"
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
            return f"{what.capitalize()} akcji ({who}); wolumen i cena są tylko w załączonym PDF."
        return (
            f"Powiadomienie z art. 19 MAR ({who}); szczegóły transakcji są tylko w załączonym PDF."
        )
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
        return f"{who} {verb} shares; volume and price are only in the attached PDF."
    return f"Art. 19 MAR notification ({who}); trade details are only in the attached PDF."


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
    summary = {
        "tested": len(stat20),
        **{v: sum(1 for r in stat20.values() if r["verdict"] == v) for v in VERDICT_ORDER},
    }

    feed, worth, events = [], [], []
    for r in today.iter_rows(named=True):
        key = signal_key(r["signal_type"], r["subtype"], r["insider_side"])
        label_key = key if r["signal_type"] not in ("other", "periodic") else r["signal_type"]
        item = {
            **r,
            "signal_key": key,
            "label_key": label_key,
            "label": label_for(label_key),
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
        e["path"] = p
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
    health_bits: list[tuple[str, dict[str, Any]]] = [("Run {s}", {"s": status})]
    if run.get("nc_pending"):
        health_bits.append(("NewConnect prices pending", {}))
    if stuck:
        health_bits.append(("{n} reports failed 5+ times", {"n": stuck}))
    if failed and not stuck:
        health_bits.append(("{n} queued for retry", {"n": failed}))
    health_bits.append(("history since {d}", {"d": coverage["first_event"] or "—"}))
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
        health=health_text(health_bits, "en"),
        health_bits=health_bits,
        site_url=site_url,
    )


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
        "chart": lambda p: car_chart_svg(p, lang=lang),
        "longdate": lambda d: i18n.long_date(d, lang),
        "health": lambda bits: health_text(bits, lang),
        "decimal": lambda x, d=2: "—" if x is None else i18n.group_digits(f"{x:.{d}f}", lang),
    }
    env.globals |= {
        "tz": WARSAW,
        "_": _,
        "lang": lang,
        "plural": lambda n, key: i18n.plural(lang, n, key),
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
