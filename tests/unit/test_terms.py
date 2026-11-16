"""Hover definitions, the "How it works" page and the glossary stay consistent."""

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from markupsafe import escape

from undercurrent import i18n, publish, terms

TEMPLATES = Path(__file__).resolve().parents[2] / "site" / "templates"
# method notes that live on how.html only, not beside the numbers they explain
MOVED = (
    "risk_note", "val_note", "dcf_note", "rrg_note", "movers_note", "pick_lede", "why_note",
    "backtest_note", "model_note", "live_note", "pick_limits", "signals_lede",
    "signals_footnote", "setups_lede", "setups_footnote", "event_chart_note", "insider_note",
    "form_note", "screener_note", "chart_note", "xtb_note", "market_lede",
)  # fmt: skip


def _templates() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html")}


def test_every_term_has_both_languages():
    for key, entry in terms.TERMS.items():
        assert len(entry) == 4 and all(x.strip() for x in entry), key
        for lang in i18n.LANGS:
            assert terms.label(key, lang) and terms.definition(key, lang)
            # a definition says what a term is, in a sentence or two
            assert len(terms.definition(key, lang)) <= 220, (key, lang)
            assert "Undercurrent" not in terms.definition(key, lang), key


def test_terms_used_in_templates_exist():
    used = set()
    for text in _templates().values():
        # literal keys only: term('phase_' ~ quadrant) is checked by rendering
        used |= set(re.findall(r"term\('([a-z0-9_]+)'[,)]", text))
        used |= set(re.findall(r"define\('([a-z0-9_]+)'[,)]", text))
    assert used, "no terms found"
    assert not used - set(terms.TERMS)


def test_how_anchors_exist_and_notes_live_only_there():
    pages = _templates()
    how = pages["how.html"]
    ids = set(re.findall(r"item\('([a-z0-9-]+)'", how)) | set(re.findall(r'id="([a-z0-9-]+)"', how))
    assert set(terms.HOW.values()) <= ids
    linked = set()
    for text in pages.values():
        linked |= set(re.findall(r"how\.html#([a-z0-9-]+)", text))
    assert linked <= ids, linked - ids
    for name, text in pages.items():
        if name == "how.html":
            continue
        for key in MOVED:
            assert f"_('{key}'" not in text, (name, key)


def _ctx() -> publish.SiteContext:
    return publish.SiteContext(
        day=date(2026, 11, 12),
        generated_at=datetime(2026, 11, 12, 20, tzinfo=UTC),
        run={},
        counts={"reports": 3, "insider": 1, "inside": 1, "holder": 0, "other": 1},
        worth=[],
        feed=[],
        stats={},
        summary={},
        events=[],
        runs=[],
        coverage={},
        health="ok",
        site_url="https://example.invalid/",
    )


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_glossary_and_how_render(lang):
    env = publish._env(lang)
    kw = {"ctx": _ctx(), "root": "", "assets": "", "other_lang_url": "", "other_lang": "en"}
    glossary = env.get_template("glossary.html").render(page="glossary", **kw)
    for key in terms.TERMS:
        assert f'id="{key}"' in glossary
    assert str(escape(terms.definition("sigma", lang))) in glossary
    how = env.get_template("how.html").render(page="how", **kw)
    assert i18n.tr(lang)("g_costs") in how
    assert 'id="costs"' in how
    # no untranslated key leaks onto the page
    assert not re.search(r">\s*[a-z]+_[a-z_]+\s*<", how)


def test_term_markup_is_plain_text_with_a_definition():
    env = publish._env("pl")
    html = env.from_string("{{ term('beta') }} {{ term('sigma', 'σ 20 <b>') }}").render()
    assert 'class="term" tabindex="0" title="Beta: o ile procent' in html
    assert "σ 20 &lt;b&gt;" in html
    assert "<a " not in html
