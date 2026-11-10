"""Rule-based classification of reports. No ML, no LLM: a pure function of the inputs.

``classify`` looks at the legal basis first (the strongest signal, since issuers must cite
it), then falls back to title/body rules. Inside-information reports get a sub-type from
keyword rules. Rules are ordered; the first match wins and its name is returned as
``rule`` so every label can be traced back to the line that produced it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SIGNAL_TYPES = ("insider_tx", "holder_threshold", "inside_info", "periodic", "other")

PERIODIC_KINDS = frozenset(
    {
        "Kwartalny",
        "Półroczny",
        "Roczny",
        "Skonsolidowany kwartalny",
        "Skonsolidowany półroczny",
        "Skonsolidowany roczny",
    }
)


@dataclass(frozen=True)
class Classification:
    """Result of :func:`classify`."""

    signal_type: str
    subtype: str | None
    rule: str


def normalize(text: str | None) -> str:
    """Lower-case, strip diacritics and collapse whitespace, for robust matching."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower()).strip()


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# Legal-basis rules on normalised text: (rule name, signal type, pattern).
_BASIS_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "basis:mar19",
        "insider_tx",
        _rx(
            r"\bart\.?\s*19\b[^;]{0,80}\b(mar|596/2014|rozporzadzenia)|\b(mar|596/2014)\b[^;]{0,40}\bart\.?\s*19\b"
        ),
    ),
    (
        "basis:obrot160",
        "insider_tx",
        _rx(r"\bart\.?\s*160\b[^;]{0,60}obrocie"),
    ),  # pre-MAR (before 2016-07-03)
    # Art. 69 notifications, and Art. 70 pkt 1 (issuer publishing one). Art. 70 pkt 3 is the
    # list of >=5% holders at a general meeting, not a crossing, so it is excluded.
    (
        "basis:oferta69_70",
        "holder_threshold",
        _rx(r"\bart\.?\s*(69a?\b|70\s*(pkt\.?|ust\.?)\s*1\b)[^;]{0,80}(ofercie|oferty)"),
    ),
    (
        "basis:mar17",
        "inside_info",
        _rx(
            r"\bart\.?\s*17\b[^;]{0,80}\b(mar|596/2014|rozporzadzenia)|\b(mar|596/2014)\b[^;]{0,40}\bart\.?\s*17\b"
        ),
    ),
    (
        "basis:oferta56_poufna",
        "inside_info",
        _rx(r"\bart\.?\s*56\b[^;]{0,40}ust\.?\s*1\s*pkt\.?\s*1\b"),
    ),  # pre-MAR inside info
    (
        "basis:periodic",
        "periodic",
        _rx(r"raport (okresowy|kwartalny|polroczny|roczny)|\bart\.?\s*60\b[^;]{0,40}pkt\.?\s*2\b"),
    ),
)

# Title/body fallbacks (used only when the legal basis didn't decide).
_TEXT_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "text:mar19",
        "insider_tx",
        _rx(
            r"art\.?\s*19 (ust\.? ?\d )?(rozporzadzenia )?mar"
            r"|osob\w* pelniac\w* obowiazki zarzadcze"
            r"|transakcj\w+ (na|w) (akcjach|instrumentach)[^.]{0,80}(zarzad|nadzorcz)"
        ),
    ),
    (
        "text:holder",
        "holder_threshold",
        _rx(
            r"zmian\w* (stanu|udzialu) posiadani|przekroczeni\w* progu|znaczn\w+ pakiet\w* akcji"
            r"|zawiadomieni\w+ [^.]{0,60}art\.?\s*69"
        ),
    ),
)

# Inside-information sub-types. Order matters within one text. The title is tried first
# and decides when it matches anything: it names the subject, while the body mentions many
# things in passing (a contract's "estimated value" is not a results estimate).
_SUBTYPE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("buyback", _rx(r"skup\w* akcji wlasnych|nabyci\w* akcji wlasnych|buy-?back")),
    ("dividend_change", _rx(r"dywidend")),
    (
        "profit_warning",
        _rx(
            r"szacunk\w* [^.]{0,60}(wynik|dan\w+|przychod|zysk|strat|ebitda|informacj)|"
            r"(wynik\w*|dan\w+|przychod\w*|informacj\w+) [^.]{0,40}szacunk|"
            r"wstepn\w+ [^.]{0,60}(wynik|dan\w+|przychod|informacj)|"
            r"przychod\w* [^.]{0,30}(kwartal|miesiac|miesiec|miesieczn|polrocz|rok)|"
            r"miesieczn\w+ przychod|prognoz\w* [^.]{0,30}(wynik|przychod|ebitda|finansow)|"
            r"odpis\w* aktualizuj|korekt\w* prognoz|utrat\w+ wartosci"
        ),
    ),
    (
        "large_contract",
        _rx(
            r"(zawarci\w*|podpisani\w*|otrzymani\w*) [^.]{0,60}(umow|aneks)|"
            r"umow\w* (znaczac|istotn|ramow)|(znaczac|istotn)\w+ umow|"
            r"wybor\w* [^.]{0,30}ofert|zamowieni\w+"
        ),
    ),
    (
        "m_and_a",
        _rx(
            r"przejeci|polaczeni\w* (spolek|z)|(nabyci|zakup)\w* (udzialow|akcji spolki)|"
            r"wezwani\w+ do zapisywania"
        ),
    ),
    ("financing", _rx(r"obligacj|kredyt|emisj\w* akcji|finansowani|pozyczk")),
    ("strategy", _rx(r"strategi|przeglad\w* opcji strategicznych")),
    ("litigation", _rx(r"pozew|pozw(u|em|ie|y|ow)\b|postepowani\w* (sadow|arbitraz)|wyrok")),
    ("rating", _rx(r"rating")),
)
# Only the opening of the body is read when the title is silent.
_SUBTYPE_BODY_CHARS = 600
# Company-form abbreviations carry dots that would end a "same sentence" match.
_COMPANY_DOTS = _rx(r"\bs\.a\.|\bsp\. z o\.o\.|\bsp\. k\.|\bs\.k\.a\.")


def _subtype_text(text: str | None) -> str:
    return _COMPANY_DOTS.sub(lambda m: m.group(0).replace(".", ""), normalize(text))


def inside_info_subtype(title: str | None, body: str | None) -> str:
    """Keyword sub-type for inside information; ``other`` when nothing matches."""
    for text in (_subtype_text(title), _subtype_text((body or "")[:_SUBTYPE_BODY_CHARS])):
        for name, rx in _SUBTYPE_RULES:
            if text and rx.search(text):
                return name
    return "other"


# An Art. 19 basis must be backed by some insider wording; issuers occasionally pick the
# wrong basis from the ESPI dropdown (seen: a monthly revenue estimate filed under Art. 19).
_INSIDER_SUPPORT = _rx(
    r"transakcj|powiadomieni|zawiadomieni|art\.?\s*19|zastaw|nabyci|zbyci|obowiazki zarzadcze"
)

# Likewise an Art. 69/70 basis needs holder wording: seen a subsidiary merger filed under
# "Art. 70 pkt 1 - nabycie lub zbycie znacznego pakietu akcji".
_HOLDER_SUPPORT = _rx(
    r"art\.?\s*(69|87)\b|liczb\w+ glosow|znaczn\w+ pakiet|stan\w* posiadania|"
    r"prog\w* \d|udzial\w* w ogolnej|transakcj\w+ na akcjach|"
    r"(zawiadomieni|powiadomieni)\w+ (o |od |w trybie |akcjonariusz)"
)


def classify(
    legal_basis: str | None,
    title: str | None,
    body: str | None,
    kind: str | None = None,
) -> Classification:
    """Classify one report. Deterministic and side-effect free."""
    if kind in PERIODIC_KINDS:
        return Classification("periodic", None, "kind:periodic")
    basis = normalize(legal_basis)
    text = normalize(title) + " \n " + normalize((body or "")[:2000])
    for rule, signal, rx in _BASIS_RULES:
        if basis and rx.search(basis):
            if signal == "insider_tx" and (title or body) and not _INSIDER_SUPPORT.search(text):
                return Classification("other", None, f"{rule}:no_text_support")
            if (
                signal == "holder_threshold"
                and body  # a title alone is too short to judge
                and not _HOLDER_SUPPORT.search(text)
            ):
                return Classification("other", None, f"{rule}:no_text_support")
            sub = inside_info_subtype(title, body) if signal == "inside_info" else None
            return Classification(signal, sub, rule)
    for rule, signal, rx in _TEXT_RULES:
        if rx.search(text):
            return Classification(signal, None, rule)
    return Classification("other", None, "default")
