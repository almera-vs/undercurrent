from datetime import UTC, date, datetime

import httpx
import polars as pl

from undercurrent import lake, pipeline, publish


def _ctx(worth=None):
    return publish.SiteContext(
        day=date(2026, 11, 9),
        generated_at=datetime(2026, 11, 9, tzinfo=UTC),
        run={},
        counts={"reports": 214},
        worth=worth or [],
        feed=[],
        stats={},
        summary={},
        events=[],
        runs=[],
        coverage={},
        health="Run ok",
        site_url="https://example.test/",
    )


def test_digest_message_lists_worth_and_disclaimer():
    item = {
        "ticker": "KDX",
        "label": "Insider buy",
        "fact": "CEO bought 42,000 shares.",
        "slug": "gpw-1",
        "stat": {"mean_car": 0.031, "n": 146, "verdict": "holds_up"},
    }
    text, markup = publish.digest_message(_ctx([item] * 7))
    assert text.startswith("<b>GPW digest · Mon 9 Nov</b>")
    assert text.count("KDX") == 5  # at most 5 items
    assert "+3.1% / 20d after entry · n=146 · ✓ holds up" in text
    assert "Not investment advice. Historical statistics only." in text
    assert markup["inline_keyboard"][0][0]["url"] == "https://example.test/"
    assert markup["inline_keyboard"][0][1]["url"].endswith("event/gpw-1.html")


def test_digest_escapes_html():
    item = {
        "ticker": "A<B",
        "label": "x",
        "fact": "1 < 2 & 3",
        "slug": "s",
        "stat": {"mean_car": 0.0, "n": 10, "verdict": "noise"},
    }
    text, _ = publish.digest_message(_ctx([item]))
    assert "A&lt;B" in text and "1 &lt; 2 &amp; 3" in text


def test_send_telegram_posts_without_leaking_token(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["body"] = req.content.decode()
        return httpx.Response(500, text="nope")

    ok = publish.send_telegram(
        "hi", {"inline_keyboard": []}, transport=httpx.MockTransport(handler)
    )
    assert not ok
    assert seen["path"] == "/bot123:SECRET/sendMessage"
    assert "chat_id=42" in seen["body"] and "parse_mode=HTML" in seen["body"]
    assert "SECRET" not in caplog.text


def test_send_telegram_unconfigured_is_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert publish.send_telegram("hi") is False


def test_is_stale(tmp_path):
    now = datetime(2026, 11, 9, 19, 15, tzinfo=UTC)  # Monday
    assert publish.is_stale(tmp_path, now) == "no runs recorded"
    row = {
        "run_date": date(2026, 11, 6),
        "started_at": now,
        "finished_at": now,
        "status": "ok",
        "reports_listed": 1,
        "reports_new": 1,
        "reports_failed": 0,
        "prices_gpw_rows": 1,
        "prices_nc_rows": 1,
        "nc_pending": False,
        "notes": None,
    }
    lake.write_state(tmp_path, "runs", pl.DataFrame([row], schema=pipeline.RUNS_SCHEMA))
    assert "no successful run for 2026-11-09" in publish.is_stale(tmp_path, now)
    row["run_date"] = date(2026, 11, 9)
    lake.write_state(tmp_path, "runs", pl.DataFrame([row], schema=pipeline.RUNS_SCHEMA))
    assert publish.is_stale(tmp_path, now) is None
    assert publish.is_stale(tmp_path, datetime(2026, 11, 8, 12, tzinfo=UTC)) is None  # Sunday


def test_formatting_helpers():
    assert publish.pct(0.031) == "+3.1%" and publish.pct(None) == "—"
    assert publish.num(0) == "0" and publish.num(1234567) == "1,234,567"
    assert publish.short_pln(772800) == "773k"
    assert publish.tone(-0.1) == "down" and publish.tone(0.1) == "up"


def test_car_chart_svg():
    path = [
        {"day": d, "mean_car": d / 1000, "lo": d / 1000 - 0.01, "hi": d / 1000 + 0.01}
        for d in range(-5, 21)
    ]
    svg = publish.car_chart_svg(path)
    assert svg.startswith("<svg") and 'class="entry"' in svg and "+2.0%" in svg
    assert "Not enough" in publish.car_chart_svg([])


def test_polish_formatting_and_plurals() -> None:
    from undercurrent import i18n

    assert i18n.pct(0.031, "pl") == "+3,1%"
    assert i18n.num(1234567, "pl") == "1\xa0234\xa0567"
    assert i18n.num(18.4, "pl") == "18,40"
    assert i18n.short_pln(772800, "pl") == "773 tys."
    assert [i18n.plural("pl", n, "event worth a look today").split()[0] for n in (1, 3, 5, 22)] == [
        "zdarzenie",
        "zdarzenia",
        "zdarzeń",
        "zdarzenia",
    ]
    assert i18n.long_date(date(2026, 11, 9), "pl") == "poniedziałek, 9 listopada 2026"


def test_fact_line_polish_and_english() -> None:
    row = {
        "signal_type": "insider_tx",
        "insider_role": "CEO",
        "insider_side": "buy",
        "insider_volume": 42000.0,
        "insider_avg_price": 18.4,
        "insider_value": 772800.0,
    }
    assert publish._fact_line(row, "pl") == (
        "Zakup (prezes zarządu): 42\xa0000 akcji po 18,40 zł (773 tys. zł)."
    )
    assert publish._fact_line(row) == "CEO bought 42,000 shares at 18.40 PLN (773k PLN)."
    gift = {**row, "insider_side": "gift", "insider_volume": None}
    assert "Darowizna" in publish._fact_line(gift, "pl")
