"""The calendar and shareholders pages render, with data and before a first run."""

import re
from datetime import UTC, date, datetime

import pytest

from undercurrent import i18n, publish

STOCK = {"isin": "PL0000000001", "ticker": "TST", "slug": "tst", "name": "TEST SPÓŁKA AKCYJNA"}


def _ctx(calendar=None, owners=None):
    return publish.SiteContext(
        day=date(2026, 11, 12),
        generated_at=datetime(2026, 11, 12, 20, tzinfo=UTC),
        run={},
        counts={},
        worth=[],
        feed=[],
        stats={},
        summary={},
        events=[],
        runs=[],
        coverage={},
        health="ok",
        site_url="",
        calendar=calendar,
        owners=owners,
    )


def _render(template, lang, ctx):
    return (
        publish._env(lang)
        .get_template(template)
        .render(ctx=ctx, root="", assets="", other_lang_url="", other_lang="en", page="x")
    )


def _calendar():
    div = {
        "stage": "proposed",
        "dps": 2.5,
        "currency": "PLN",
        "yield": 0.05,
        "record_date": date(2026, 12, 2),
        "ex_date": date(2026, 12, 1),
        "pay_date": date(2026, 12, 9),
        "stock": STOCK,
    }
    entries = [
        {
            "date": date(2026, 11, 20),
            "kind": "results",
            "isin": STOCK["isin"],
            "stock": STOCK,
            "period": "q3",
            "fiscal_year": 2026,
        },
        {
            "date": date(2026, 12, 1),
            "kind": "ex",
            "isin": STOCK["isin"],
            "stock": STOCK,
            "div": div,
        },
    ]
    return {
        "days": [{"date": e["date"], "entries": [e]} for e in entries],
        "dividends": [div],
        "by_isin": {STOCK["isin"]: entries},
        "counts": {"ex": 1, "pay": 0, "results": 1, "zwz": 0, "nwz": 0, "wz": 0},
        "stats": {
            "checked": 759,
            "agree": 661,
            "drop": {"n": 605, "median": 0.81, "lo": 0.75, "hi": 0.87},
            "dates": {"n": 100, "kept": 67, "early": 11},
        },
        "until": date(2026, 12, 27),
    }


def _owners():
    change = {
        "published": date(2026, 11, 10),
        "stock": STOCK,
        "name": "Alfa OFE",
        "pct_before": 4.9,
        "pct_after": 5.04,
        "direction": "up",
        "threshold": 5.0,
        "source_url": "https://example.invalid/r",
    }
    return {
        "by_isin": {},
        "recent": [change],
        "index": [
            {
                "key": "alfa",
                "name": "Alfa OFE",
                "companies": 3,
                "stakes": [{"stock": STOCK, "pct": 7.5}, {"stock": STOCK, "pct": None}],
            }
        ],
        "coverage": {
            "notices": 10,
            "named": 0.8,
            "stake": 0.4,
            "lists": 5,
            "holders": 30,
            "companies": 12,
        },
        "ups": 1,
        "downs": 0,
        "days": 90,
    }


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_calendar_page(lang):
    html = _render("calendar.html", lang, _ctx(calendar=_calendar()))
    assert 'href="stock/tst.html"' in html
    assert html.count('data-type="div"') == 1 and html.count('data-type="results"') == 1
    assert "81" in html and "661" in html  # the ex-date finding and the exchange check
    assert not re.search(r">\s*[a-z]+_[a-z_]+\s*<", html)  # no untranslated key
    empty = _render("calendar.html", lang, _ctx())
    assert i18n.tr(lang)("The calendar has not been built yet tonight.") in empty


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_owners_page(lang):
    html = _render("owners.html", lang, _ctx(owners=_owners()))
    assert "Alfa OFE" in html and "5.04%" in html.replace(",", ".")
    assert "5%+" in html  # a stake the text did not state
    assert not re.search(r">\s*[a-z]+_[a-z_]+\s*<", html)
    empty = _render("owners.html", lang, _ctx())
    assert i18n.tr(lang)("The holder register has not been built yet tonight.") in empty
