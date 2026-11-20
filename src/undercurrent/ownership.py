"""Who owns what: major holders from threshold notices and general-meeting lists.

Two kinds of current report say who holds at least 5 % of a company's votes:

* **Threshold notices** (Art. 69/70 pkt 1 of the Public Offering Act): a holder crossed 5,
  10, 15, 20, 25, 33, 33⅓, 50, 75 or 90 % of the votes, or a holder above 10 % moved by
  2 % (1 % on the main market's largest holders). The issuer usually retells the notice:
  who, the stake before and after, as shares of all votes. Many issuers only attach the
  holder's letter as a PDF, which the exchange serves behind bot protection; for those the
  holder's name is often all the text says, and the stake stays ``None``.
* **General-meeting lists** (Art. 70 pkt 3): after each meeting the issuer lists the holders
  with at least 5 % of the votes *at that meeting*, usually with their share of all votes.
  A meeting share is never below the share of all votes (fewer votes attend than exist),
  so of a row's two percentages the smaller is the share of all votes.

From both, :func:`register` keeps each company's last known stake per holder, and
:func:`changes` the recent moves. A holder is matched across reports by the stems of the
words of its name (Polish declines names: *Pawła Knopika* is *Paweł Knopik*), and the large
pension funds by their family name, so a fund's notices at forty companies are one holder.
Plain rules; nothing is inferred: a stake the text does not state is ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from undercurrent.classify import normalize
from undercurrent.parse import parse_pl_number

# --------------------------------------------------------------------------- holder names

_UP = "A-ZŁŚŻŹĆŃÓĘĄ"
_HONORIFIC = (
    r"(?i:(?:akcjonariusz\w*|spółk\w*|spolk\w*|pan\w*|p\.|podmiot\w*|firm\w*|"
    r"inwestor\w*|zawiadamiając\w*|"
    r"(?:przewodnicząc\w*|wiceprzewodnicząc\w*|członk\w*|prezes\w*|wiceprezes\w*|sekretarz\w*)"
    r"\s+(?:rady nadzorczej|zarządu)(?:\s+(?:spółki|emitenta))?,?|"
    r"(?:pod firmą|działając\w* pod firmą))\s+)*"
)
# The name runs until a clause that is no longer part of it.
_NAME_END = (
    r"(?=\s+z siedzibą|\s+z siedziba|\s+KRS\b|\s+\(|\s*[,;(_\"“”„]|\s+w trybie|\s+zawiadomie|"
    r"\s+powiadomie|\s+informacj|\s+o zmian|\s+o przekrocz|\s+o zwiększ|\s+o zmniejsz|"
    r"\s+o nabyc|\s+o zbyc|\s+o osiągnięc|\s+o spadk|\s+informuj|\s+dotycz|\s+na podstawie|"
    r"\s+działając|\s+dzialajac|\s+sporządzon|\s+zgodnie|\s+w związku|\s+w zwiazku|"
    r"\s+w sprawie|\s+wraz|\s+wpłynęł|\s+wplynel|\s+otrzyman|\s+która|\s+który|\s+ktora|"
    r"\s+ktory|\s+posiada|\s+dalej|\s+jako|\s+tj\.|\s+zawiadamia|\s+zawiadomił|\s+poinformował|"
    r"\s+przekazał|\s+o\s|\s+w dniu|\s+dnia|\s+\d|\.\s|\.$|$)"
)
# Dots inside company forms would end the name early ("FTI Capital sp. z o.o.").
_FORM_DOTS = re.compile(
    r"\b(?:S\.A\.|S\.A|[Ss]p\. ?z ?o\. ?o\.?|sp\.k\.|Sp\.k\.|S\.K\.A\.|"
    r"sp\. k\.|Sp\. k\.|N\.V\.|B\.V\.|L\.P\.|Inc\.|Ltd\.|S\.à r\.l\.|S\.a\.r\.l\.)"
)


def _undot(text: str) -> str:
    return _FORM_DOTS.sub(lambda m: m.group(0).replace(".", ""), text)


# "zawiadomienie od X", "przez X", and "zawiadomienie spółki X" (the holder in the genitive)
_FROM = re.compile(
    r"\b(?:od|przez|ze strony|(?i:zawiadomieni\w*|powiadomieni\w*)(?=\s+(?i:spółki|akcjonariusza|"
    r"pana|pani|funduszu)\b))\s+"
    + _HONORIFIC
    + rf"(?P<name>[{_UP}0-9\"„][^,;:()_\n]{{1,140}}?)"
    + _NAME_END
)
# "Zawiadomienie od akcjonariusza – Quercus TFI" or "… Spółki: Quercus TFI S.A."
_TITLE_NAME = re.compile(
    r"(?:\bod|\bprzez|akcjonariusz\w*)\s*[-–:]?\s*"
    + _HONORIFIC
    + rf"(?P<name>[{_UP}][^,;:()_\n]{{1,100}}?)"
    + _NAME_END
)
_NOT_A_NAME = re.compile(
    r"^(dnia|dniu|spółk|spolk|emitent|zarząd|zarzad|rad[ay]|walne|akcjonariusz|komisj|"
    r"podmiot|zawiadom|powiadom|art\.|ustaw|osob|grup|pana$|pani$|niego|nią|nia|jego|"
    r"tego|tej|niej|ponad|wiecej|więcej|sąd|sad |krajow|udział|udzial|stan|\d)",
    re.I,
)
_LEGAL_FORM = re.compile(
    r"\b(s\.?a\.?|spolka akcyjna|sp\.? ?z ?o\.? ?o\.?|spolka z ograniczona odpowiedzialnoscia|"
    r"s\.?k\.?a\.?|sp\.?k\.?|spolka komandytow\w*( akcyjna)?|spolka jawna|sp\.? j\.?|"
    r"asi|alternatywna spolka inwestycyjna|fiz|fio|sfio|w organizacji|w likwidacji|"
    r"w upadlosci( ukladowej| likwidacyjnej)?|w restrukturyzacji|"
    r"ltd\.?|limited|inc\.?|plc|llc|gmbh|ag|n\.?v\.?|b\.?v\.?|se|l\.?p\.?|s\.?a\.?r\.?l\.?|"
    r"s\.?\s?l\.?|sicav|s\.?c\.?a\.?|z siedziba\b.*)$"
)

_PENSION = r"(\bofe\b|\bdfe\b|\bpte\b|otwart\w* fundusz\w* emeryt|emerytaln)"
_TFI = r"(\btfi\b|towarzystw\w* funduszy inwestycyjnych)"


def _brand(brand: str, kind: str | None = None) -> re.Pattern[str]:
    """``brand``, and when given ``kind`` within 50 characters of it, either side."""
    if kind is None:
        return re.compile(brand)
    return re.compile(rf"({brand}).{{0,50}}{kind}|{kind}.{{0,50}}({brand})")


# Large institutional families, so a fund's notices are one holder whatever form the
# issuer used ("Nationale-Nederlanden Otwartego Funduszu Emerytalnego", "NN OFE",
# "Powszechne Towarzystwo Emerytalne PZU", …). Pension funds before investment funds: a
# group's pension and investment arms are different holders.
FAMILIES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "nn_ofe",
        "Nationale-Nederlanden OFE",
        _brand(r"nationale\W*nederlanden|^nationale$|\bnn\b|\bing\b", _PENSION),
    ),
    ("nn_ofe", "Nationale-Nederlanden OFE", _brand(r"^nationale\W*(nederlanden)?\s*$")),
    ("allianz_ofe", "Allianz Polska OFE", _brand(r"allianz", _PENSION)),
    ("aviva_ofe", "Aviva / Goldman Sachs OFE", _brand(r"aviva|goldman sachs", _PENSION)),
    ("generali_ofe", "Generali OFE", _brand(r"generali", _PENSION)),
    ("pzu_ofe", "PZU OFE", _brand(r"\bpzu\b|zlota jesien", _PENSION)),
    ("aegon_ofe", "Aegon / Vienna OFE", _brand(r"aegon|vienna life|metlife", _PENSION)),
    ("axa_ofe", "AXA / Uniqa OFE", _brand(r"\baxa\b|uniqa", _PENSION)),
    ("pkobp_ofe", "PKO BP Bankowy OFE", _brand(r"\bpko\b|bankow\w*", _PENSION)),
    ("pekao_ofe", "Pekao OFE", _brand(r"pekao|pocztylion", _PENSION)),
    ("nordea_ofe", "Nordea OFE", _brand(r"nordea", _PENSION)),
    ("quercus", "Quercus TFI", _brand(r"quercus")),
    ("ipopema", "Ipopema TFI", _brand(r"ipopema", r"(tfi|towarzystw|fundusz)")),
    ("investors_tfi", "Investors TFI", _brand(r"investors", _TFI)),
    ("pzu_tfi", "PZU TFI", _brand(r"\bpzu\b", _TFI)),
    ("pko_tfi", "PKO TFI", _brand(r"\bpko\b", _TFI)),
    ("goldman_tfi", "Goldman Sachs TFI", _brand(r"goldman sachs", _TFI)),
    ("norges", "Norges Bank", _brand(r"norges bank")),
    ("skarb", "Skarb Państwa", _brand(r"skarb\w* panstwa")),
    ("blackrock", "BlackRock", _brand(r"blackrock")),
)


def holder_key(name: str | None) -> str | None:
    """A matching key for a holder's name, robust to Polish declension and legal forms."""
    n = normalize(name)
    if not n:
        return None
    for key, _, rx in FAMILIES:
        if rx.search(n):
            return key
    n = re.sub(r"[\"„”“'`]", " ", n)
    for _ in range(3):
        n = _LEGAL_FORM.sub("", n.strip(" .,-–")).strip()
    honorifics = {"i", "oraz", "p", "pan", "pana", "panu", "panem", "pani", "pania", "panstwo"}
    words = [w for w in re.split(r"[^a-z0-9]+", n) if w and w not in honorifics]
    if not words:
        return None
    return " ".join(_stem(w) for w in words[:6])


def _stem(word: str) -> str:
    """A stem that survives Polish declension.

    The first five letters of a longer word (knopika / knopik → knopi); a short word without
    its ending vowels (jana / jan → jan, anny / anna → ann).
    """
    if len(word) > 5:
        return word[:5]
    stem = word.rstrip("aeiouy")
    return stem if len(stem) >= 2 else word


def family_name(key: str | None) -> str | None:
    """The display name of an institutional family key, or ``None``."""
    return next((label for k, label, _ in FAMILIES if k == key), None)


# Lower-case words that belong inside a name: legal forms, fund types, particles.
_NAME_LOWER = frozenset(
    [
        "z",
        "o.o.",
        "oo",
        "o.o",
        "sp",
        "sp.",
        "spółka",
        "spolka",
        "akcyjna",
        "akcyjnej",
        "ograniczoną",
        "ograniczona",
        "odpowiedzialnością",
        "odpowiedzialnoscia",
        "komandytowa",
        "komandytowo-akcyjna",
        "jawna",
        "fundusz",
        "funduszu",
        "inwestycyjny",
        "inwestycyjnego",
        "otwarty",
        "otwartego",
        "zamknięty",
        "zamkniety",
        "zamkniętego",
        "specjalistyczny",
        "emerytalny",
        "emerytalnego",
        "w",
        "organizacji",
        "likwidacji",
        "upadłości",
        "upadlosci",
        "układowej",
        "ukladowej",
        "restrukturyzacji",
        "rodzinna",
        "de",
        "da",
        "do",
        "dos",
        "du",
        "di",
        "van",
        "von",
        "der",
        "la",
        "le",
        "del",
        "y",
        "e",
        "-",
        "–",
        "&",
        "and",
        "of",
    ]
)


def _tidy_name(raw: str) -> str:
    """The name alone: no trailing clause, and only the first of several holders."""
    s = re.sub(r"\s+", " ", raw).strip(' .,-–"„”“')
    s = re.split(r"\s+(?:oraz|i|a także)\s+|\s+(?i:z siedzibą|z siedziba|z/s)\b", s)[0]
    s = re.sub(r"\b([Ss]p)\.? ?z ?o\.? ?o\b\.?", r"\1. z o.o.", s)
    s = re.sub(r"\bSA\b", "S.A.", s)  # the dots _undot took out
    words = s.split(" ")
    kept: list[str] = []
    for w in words:
        if kept and w[:1].islower() and w.lower().strip(".,") not in _NAME_LOWER:
            break  # "Tomasza Grodzkiego zaktualizowane …"
        kept.append(w)
    # a connector left hanging at the end belonged to the next clause ("… S.A. z dnia")
    while len(kept) > 1 and kept[-1].lower() in ("z", "w", "do", "de", "da", "-", "–", "&"):
        kept.pop()
    return " ".join(kept).strip(' ,-–"„”“')


def holder_name(title: str | None, body: str | None) -> str | None:
    """The holder a threshold notice is about, as the issuer wrote it."""
    for text in (_undot((body or "")[:900]), _undot(title or "")):
        for m in _FROM.finditer(text):
            name = _tidy_name(m.group("name"))
            if _NOT_A_NAME.match(name) or len(name) < 3:
                continue
            return name
    for m in _TITLE_NAME.finditer(_undot(title or "")):
        name = _tidy_name(m.group("name"))
        if not _NOT_A_NAME.match(name) and len(name) >= 3:
            return name
    # the holder as the subject: "Jan Kalino poinformował pismem …"
    if subject := _SUBJECT.search(_undot((body or "")[:1500])):
        name = _tidy_name(subject.group("name"))
        if not _NOT_A_NAME.match(name):
            return name
    return None


_SUBJECT = re.compile(
    rf"(?<![\w.])(?P<name>[{_UP}][\w-]+(?:\s+[{_UP}][\w-]+){{1,4}})\s+"
    r"(?:poinformował\w*|zawiadomił\w*|powiadomił\w*)\b"
)


# --------------------------------------------------------------------------- stakes

_VOTE_PCT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,6})?)\s*(?:%|proc\.?|procent\w*)\s*"
    r"(?:\w+\s+){0,3}?(?:ogoln\w*\s+)?(?:liczb\w*\s+)?glos"
)
# "below the 5 % threshold", "by more than 2 %": a threshold or a step, not a stake
_THRESHOLD_CTX = re.compile(
    r"\b(prog\w*|powyzej|ponizej|przekroczeni\w*|przekroczyl\w*|ponad|wiecej niz|niz|"
    r"co najmniej|najmniej|o|osiagnieci\w*|zejsci\w*|spadk\w*|lacznie)\s*(o\s+)?(ponad\s+)?$"
)
_BEFORE = re.compile(
    r"przed (powyzsz\w* |ww\.? |ta |tym |tymi |wskazan\w* |opisan\w* )?"
    r"(zmian|transakcj|nabyci|zbyci|rozliczeni|dokonani|zarejestrowani|zawarci|objeci|"
    r"umorzeni|podwyzszeni|zdarzeni|wniesieni|przenies|zapisani|zbyc|nabyc|rejestracj)\w*"
    # not "dotychczas": "o zmianie dotychczas posiadanego udziału" is the notice's own title
    r"|\bwczesniej\b|stan\w* przed|przed zmiana"
)
_AFTER = re.compile(
    r"po (zmian|transakcj|nabyci|zbyci|rozliczeni|dokonani|zarejestrowani|zawarci|objeci|"
    r"umorzeni|podwyzszeni|zdarzeni|wniesieni|przenies|zapisani|zbyc|nabyc|tej|ww|powyzsz|"
    r"rejestracj|zarejestrowani)\w*"
    r"|obecnie|aktualnie|na dzien (sporzadzenia|zlozenia|zawiadomienia|dzisiejszy)|stan\w* po\b"
    r"|w (wyniku|nastepstwie) (transakcji|rozliczenia|powyzsz\w*|czego|zmiany|tego)"
    r"|posiada (obecnie |aktualnie )?[\d ]+ akcj"
)
_UP_WORDS = re.compile(
    r"zwieksz\w*|przekroczeni\w* (progu|powyzej)|przekroczyl\w*|powyzej|wzrost\w*|"
    r"osiagnieci\w* (progu|udzialu)|nabyci\w*|nabyl\w*|zakup\w*|objeci\w*|objal\w*"
)
_DOWN_WORDS = re.compile(
    r"zmniejsz\w*|ponizej|zejsci\w*|spad\w*|obnizeni\w*|zbyci\w*|zbyl\w*|sprzedaz\w*|"
    r"sprzedal\w*|zmniejszyl\w*"
)
_THRESHOLDS = (5, 10, 15, 20, 25, 33, 50, 75, 90)


@dataclass
class HoldingChange:
    """A threshold notice as the report states it; ``None`` = not stated."""

    holder: str | None = None
    pct_before: float | None = None
    pct_after: float | None = None
    direction: str | None = None  # up | down
    threshold: float | None = None
    notes: list[str] = field(default_factory=list)


def _vote_pcts(text: str) -> list[tuple[int, float]]:
    out = []
    for m in _VOTE_PCT.finditer(text):
        if _THRESHOLD_CTX.search(text[max(0, m.start() - 22) : m.start()]):
            continue
        v = parse_pl_number(m.group(1))
        if v is not None and 0 <= v <= 100:
            out.append((m.start(), v))
    return out


def extract_holding_change(title: str | None, body: str | None) -> HoldingChange:
    """Holder, stake before and after (shares of all votes), threshold and direction."""
    h = HoldingChange()
    h.holder = holder_name(title, body)
    if h.holder is None:
        h.notes.append("holder not named")
    t, b = normalize(title), normalize(body)
    b = b.replace("_", " ")
    pcts = _vote_pcts(b)
    anchors = sorted(
        [(m.start(), "before") for m in _BEFORE.finditer(b)]
        + [(m.start(), "after") for m in _AFTER.finditer(b)]
    )
    labelled: list[tuple[str | None, float]] = []
    for pos, v in pcts:
        lab = next((a for p, a in reversed(anchors) if p < pos), None)
        labelled.append((lab, v))
    before = next((v for lab, v in labelled if lab == "before"), None)
    after = next((v for lab, v in labelled if lab == "after"), None)
    unlabelled = [v for lab, v in labelled if lab is None]
    # "posiadałem … 16,8 % głosów. Wcześniej … 13,6 %": the stake stated first is the new one
    if after is None and before is not None and unlabelled:
        after = unlabelled[0]
    if after is None and before is None and len({v for _, v in labelled}) == 1:
        after = labelled[0][1]
        h.notes.append("single stake taken as the stake after")
    h.pct_before, h.pct_after = before, after
    if (m := re.search(r"prog\w*\s+(\d{1,2}(?:[.,]\d+)?)\s*(?:%|proc)", t + " " + b)) or (
        m := re.search(r"(?:powyzej|ponizej)\s+(\d{1,2}(?:[.,]\d+)?)\s*(?:%|proc)", t + " " + b)
    ):
        h.threshold = parse_pl_number(m.group(1))
    if before is not None and after is not None and before != after:
        h.direction = "up" if after > before else "down"
    else:
        up_t, down_t = bool(_UP_WORDS.search(t)), bool(_DOWN_WORDS.search(t))
        if up_t != down_t:
            h.direction = "up" if up_t else "down"
        else:
            up_b, down_b = bool(_UP_WORDS.search(b[:1200])), bool(_DOWN_WORDS.search(b[:1200]))
            if up_b != down_b:
                h.direction = "up" if up_b else "down"
    if h.pct_after is None:
        h.notes.append("stake not in the text (attachment only)")
    return h


# --------------------------------------------------------------------------- meeting lists

_MEETING_LIST_BASIS = re.compile(r"\bart\.?\s*70\s*pkt\.?\s*3\b")
_MEETING_LIST_TITLE = re.compile(
    r"(wykaz|list\w*) (akcjonariuszy|podmiotow)[^.]{0,80}(5|piec)\s*%|"
    r"akcjonariusz\w* posiadajac\w* (co najmniej|powyzej|ponad) 5"
)
_PCT_ANY = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,6})?)\s*(?:%|proc\.?)")
_NUM = re.compile(r"(?<![\w.,])\d[\d .\xa0]*(?:,\d+)?(?![\w])")
_HEADER_WORDS = re.compile(
    r"\b(lp|l\.p|akcjonariusz\w*|nazwa|imie i nazwisko|liczba|ilosc|akcji|glosow|glosy|"
    r"udzial\w*|w|na|z|ze|wza|zwz|zwza|nwz|nwza|wz|walnym|walnego|zgromadzeni\w*|ogolnej|"
    r"ogolna|liczbie|kapitale|zakladowym|obecnych|obecnymi|reprezentowanych|posiadanych|"
    r"zarejestrowanych|procentowy|spolce|spolki|emitenta|tym|oraz|i|%|szt|razem|suma|"
    r"zwyczajnym|nadzwyczajnym|uprawniajacych|wykonywanych|wykonanych|prawo|do|od)\b"
)


@dataclass(frozen=True)
class ListedHolder:
    """One row of a general-meeting list."""

    holder: str
    pct_total: float  # share of all votes in the company
    pct_meeting: float


def is_meeting_list(legal_basis: str | None, title: str | None) -> bool:
    """True for an Art. 70 pkt 3 list of the ≥5 % holders at a general meeting."""
    return bool(
        _MEETING_LIST_BASIS.search(normalize(legal_basis))
        or _MEETING_LIST_TITLE.search(normalize(title))
    )


# Where a row of a list starts: "Akcjonariusz: …", "1_ …", "2. …", "3) …", "• …", "- …".
_ROW_START = re.compile(
    rf"(?:\b(?i:akcjonariusz)\s*(?:nr\s*\d+\s*)?:\s*"
    rf"|(?<![\d,.])\d{{1,2}}\s*[_.)]\s*(?=[{_UP}\"„])"
    # a dash is a bullet only after punctuation: "byli: - Marcin …", ", - spółka Biovo …",
    # not inside a name ("Biuro Informacyjno - Prawne")
    rf"|(?:^|(?<=[:;.,]\s))[-–•·▪]\s+(?:(?i:spółka|spolka)\s+)?(?=[{_UP}\"„]))"
)
# A row written as a sentence: "MERA S.A., posiadająca 2 697 835 akcji …",
# "… roku: Biuro Informacyjno-Prawne AKCEPT S.A. Liczba akcji: …".
_ROW_SENTENCE = re.compile(
    rf"(?:^|[.:;]\s+)(?P<name>[{_UP}][^.:;]{{2,90}}?),?\s+(?=(?i:posiadając\w*|dysponując\w*|"
    r"wykonując\w*|wykonywał\w*|liczba akcji|liczba głosów)\b)"
)
# The name ends where the row's figures begin.
_ROW_NAME_END = re.compile(
    # a dash ends the name only before the row's lower-case details ("Kornacki - liczba"),
    # not inside it ("Biuro Informacyjno - Prawne")
    r"\s*(?::|\s[-–]\s(?=[a-ząćęłńóśźż])|,\s|\s(?i:liczba|ilość|ilosc|posiadając\w*|posiadajac\w*|posiada\w*|"
    r"uprawnion\w*|dysponując\w*|reprezentując\w*|wykonywał\w*|wykonując\w*|który|ktory|"
    r"która|ktora|z liczbą|zarejestrował\w*|akcji|głosów|glosow|udział)\b|\s\d)"
)


def _rows_from(text: str, starts: list[int]) -> list[ListedHolder]:
    """The rows beginning at ``starts``: a name, then at least two percentages."""
    out = []
    for i, s in enumerate(starts):
        seg = text[s : starts[i + 1] if i + 1 < len(starts) else s + 600]
        cut = _ROW_NAME_END.search(seg)
        name = _tidy_name(seg[: cut.start()] if cut else seg[:80])
        vals = []
        for m in _PCT_ANY.finditer(seg):
            # "co najmniej 5% liczby głosów" in a trailing sentence is not a stake
            if re.search(
                r"(najmniej|ponad|powyzej|powyżej)\s*$", seg[max(0, m.start() - 15) : m.start()]
            ):
                continue
            v = parse_pl_number(m.group(1))
            if v is not None:
                vals.append(v)
        if len(vals) < 2 or not re.search(rf"[{_UP}]", name) or not 3 <= len(name) <= 120:
            continue
        if _HEADER_WORDS.fullmatch(normalize(name.split()[0]).strip(".,:")):
            continue
        a, b = vals[0], vals[1]
        if not (0 < min(a, b) <= max(a, b) <= 100):
            continue
        out.append(ListedHolder(name, min(a, b), max(a, b)))
    return out


def extract_meeting_list(body: str | None) -> list[ListedHolder]:
    """The holders of a meeting list that give both percentages.

    Lists come as labelled, numbered or bulleted entries (``Akcjonariusz: Name … 49,22% …
    34,04%``, ``1_ Name - …``, ``- Name …``), as sentences (``Name, posiadający … 5,27% … i
    2,62% …``) or as a flattened table (``Name 1.741.651 99,9999% 57,57% Name2 …``). Each
    reading is tried and the one that finds the most rows wins. A row is kept only when it
    carries two percentages, of which the smaller is the share of all votes.
    """
    raw = _undot(body or "")
    # numbered rows use "1_" in the exchange's text, so underscores stay for the row starts
    entries = _rows_from(raw, [m.end() for m in _ROW_START.finditer(raw)])
    text = raw.replace("_", " ")
    sentences = _rows_from(text, [m.start("name") for m in _ROW_SENTENCE.finditer(text)])
    table = _table_rows(text)
    return max((entries, sentences, table), key=len)


def _table_rows(text: str) -> list[ListedHolder]:
    """Rows of a list flattened from a table: a name, then its numbers."""
    pcts = list(_PCT_ANY.finditer(text))
    if len(pcts) < 2:
        return []
    # group percentages that follow each other with only numbers and spaces between
    groups: list[list[re.Match[str]]] = []
    for m in pcts:
        if groups:
            gap = text[groups[-1][-1].end() : m.start()]
            if re.fullmatch(r"[\s\d.,*%()\xa0/-]*", gap):
                groups[-1].append(m)
                continue
        groups.append([m])
    out: list[ListedHolder] = []
    prev_end = 0
    for g in groups:
        seg = text[prev_end : g[0].start()]
        prev_end = g[-1].end()
        if len(g) != 2:
            continue
        a, b = (parse_pl_number(x.group(1)) for x in g)
        if a is None or b is None or not (0 < min(a, b) <= max(a, b) <= 100):
            continue
        # the name is what is left of the segment once numbers and header words are gone
        name_part = _NUM.sub(" ", seg)
        name_part = re.split(r"[:;]\s|\.\s(?=[A-ZŁŚŻ])", name_part)[-1]
        words = name_part.split()
        kept = [w for w in words if not _HEADER_WORDS.fullmatch(normalize(w).strip(".,:-–()*"))]
        name = _tidy_name(" ".join(kept))
        if not re.search(rf"[{_UP}]", name) or len(name) < 3 or len(kept) > 14:
            continue
        out.append(ListedHolder(name, min(a, b), max(a, b)))
    return out


# --------------------------------------------------------------------------- build

OBS_SCHEMA: dict[str, Any] = {
    "report_id": pl.String,
    "isin": pl.String,
    "published": pl.Date,
    "source": pl.String,  # notice | meeting
    "holder": pl.String,
    "holder_key": pl.String,
    "pct_before": pl.Float64,
    "pct_after": pl.Float64,
    "direction": pl.String,
    "threshold": pl.Float64,
    "notes": pl.String,
}


def observations(reports: pl.DataFrame) -> pl.DataFrame:
    """Every holder observation in ``reports``.

    ``reports``: report_id, isin, published, signal_type, legal_basis, title, body_text.
    """
    rows: list[dict[str, Any]] = []
    for r in reports.iter_rows(named=True):
        if not r["isin"]:
            continue
        base = {"report_id": r["report_id"], "isin": r["isin"], "published": r["published"]}
        if r["signal_type"] == "holder_threshold":
            h = extract_holding_change(r["title"], r["body_text"])
            rows.append(
                base
                | {
                    "source": "notice",
                    "holder": h.holder,
                    "holder_key": holder_key(h.holder),
                    "pct_before": h.pct_before,
                    "pct_after": h.pct_after,
                    "direction": h.direction,
                    "threshold": h.threshold,
                    "notes": "; ".join(h.notes) or None,
                }
            )
        elif is_meeting_list(r["legal_basis"], r["title"]):
            for x in extract_meeting_list(r["body_text"]):
                rows.append(
                    base
                    | {
                        "source": "meeting",
                        "holder": x.holder,
                        "holder_key": holder_key(x.holder),
                        "pct_before": None,
                        "pct_after": x.pct_total,
                        "direction": None,
                        "threshold": None,
                        "notes": None,
                    }
                )
    return pl.DataFrame(rows, schema=OBS_SCHEMA)


# A stake older than this is shown as stale: holders between thresholds need not report.
STALE_DAYS = 3 * 365
MIN_STAKE = 5.0


def register(obs: pl.DataFrame, day: date) -> pl.DataFrame:
    """Each company's holders as of ``day``: the last known stake per holder.

    A holder whose last notice took them below 5 % has left the register; a notice that
    states no stake but a fall below a threshold of 5 % does too. Stakes come from
    observations up to ``day`` only.
    """
    # no early return on an empty frame: the register keeps its columns either way
    o = obs.filter((pl.col("published") <= day) & pl.col("holder_key").is_not_null())
    last = (
        o.sort("published", "report_id")
        .group_by("isin", "holder_key", maintain_order=True)
        .agg(
            pl.col("holder").last(),
            pl.col("published").last().alias("as_of"),
            pl.col("report_id").last(),
            pl.col("source").last(),
            pl.col("direction").last(),
            pl.col("threshold").last(),
            pl.col("pct_after").last().alias("pct_last"),
            # the latest stated stake, and when it was stated
            pl.col("pct_after").drop_nulls().last().alias("pct"),
            pl.col("published").filter(pl.col("pct_after").is_not_null()).last().alias("pct_as_of"),
        )
    )
    gone = (pl.col("pct_last").is_not_null() & (pl.col("pct_last") < MIN_STAKE)) | (
        pl.col("pct_last").is_null()
        & (pl.col("direction") == "down")
        & (pl.col("threshold") == MIN_STAKE)
    )
    return (
        last.filter(~gone)
        .with_columns(stale=(pl.lit(day) - pl.col("as_of")).dt.total_days() > STALE_DAYS)
        .sort(["isin", pl.col("pct").fill_null(-1)], descending=[False, True])
    )


def changes(obs: pl.DataFrame, day: date, days: int = 90) -> pl.DataFrame:
    """Threshold notices of the last ``days`` days up to ``day``, newest first."""
    return obs.filter(
        (pl.col("source") == "notice")
        & (pl.col("published") <= day)
        & (pl.col("published") > day - timedelta(days=days))
    ).sort("published", "report_id", descending=True)


_SEAT = re.compile(r"\s+(?:z siedzibą|z siedziba|z/s|KRS)\b.*$", re.I)


def display_names(obs: pl.DataFrame) -> dict[str, str]:
    """One name per holder: a family's own, else a meeting list's, else the latest notice's.

    Meeting lists write names in the nominative ("Paweł Knopik") where notices decline them
    ("od Pawła Knopika").
    """
    out: dict[str, str] = {}
    o = obs.filter(pl.col("holder_key").is_not_null()).sort("published", "report_id")
    for src in ("notice", "meeting"):  # meeting lists last, so they win
        for key, name in (
            o.filter(pl.col("source") == src).select("holder_key", "holder").iter_rows()
        ):
            out[key] = _SEAT.sub("", name).strip(" ,;/")
    for key in out:
        if (fam := family_name(key)) is not None:
            out[key] = fam
    return out


def holders_index(reg: pl.DataFrame, min_companies: int = 3) -> pl.DataFrame:
    """Holders with stakes in at least ``min_companies`` companies, largest portfolios first."""
    if reg.is_empty():
        return reg
    cur = reg.filter(~pl.col("stale"))
    return (
        cur.group_by("holder_key")
        .agg(
            pl.col("holder").sort_by("as_of").last(),
            pl.len().alias("companies"),
            pl.col("isin"),
            pl.col("pct"),
            pl.col("as_of").max(),
        )
        .filter(pl.col("companies") >= min_companies)
        .sort("companies", "holder_key", descending=[True, False])
    )


# --------------------------------------------------------------------------- run and site

GOLD_FILE = "holder_obs.parquet"
CHANGE_DAYS = 90


def run_ownership(duckdb_file: Path, work: Path, day: date) -> Path:
    """Holder observations from every report known by ``day``, written to gold."""
    import duckdb

    from undercurrent import lake, study

    con = duckdb.connect(str(duckdb_file))
    try:
        reps = con.sql(
            "select report_id, isin, signal_type, legal_basis, title, body_text, source_url, "
            "cast(substr(published_at_local, 1, 10) as date) as published "
            "from reports where isin is not null and known_at <= ? and ("
            "signal_type = 'holder_threshold' or legal_basis ilike '%70 pkt%3%' "
            "or legal_basis ilike '%70 ust%3%' or title ilike '%akcjonariusz%5%')",
            params=[study.as_of_for(day)],
        ).pl()
    finally:
        con.close()
    obs = observations(reps).join(
        reps.select("report_id", "source_url"), on="report_id", how="left"
    )
    out = work / "gold" / GOLD_FILE
    lake.write_frame(obs.sort("published", "report_id", "holder_key", nulls_last=True), out)
    return out


def context(work: Path, day: date, stocks: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """What the shareholders page and the stock pages show, or ``None`` before a first run.

    ``stocks``: ISIN → the stock's page metadata; holdings in stocks without a page are left
    out of the lists but still count towards a holder's portfolio.
    """
    path = work / "gold" / GOLD_FILE
    if not path.exists():
        return None
    obs = pl.read_parquet(path)
    names = display_names(obs)
    reg = register(obs, day)
    by_isin: dict[str, list[dict[str, Any]]] = {}
    for r in reg.iter_rows(named=True):
        by_isin.setdefault(r["isin"], []).append(
            {**r, "name": names.get(r["holder_key"], r["holder"])}
        )
    recent = []
    for r in changes(obs, day, CHANGE_DAYS).iter_rows(named=True):
        st = stocks.get(r["isin"])
        if st is None:
            continue
        name = names.get(r["holder_key"] or "", r["holder"]) if r["holder_key"] else r["holder"]
        recent.append({**r, "stock": st, "name": name})
    index = []
    for r in holders_index(reg).head(24).iter_rows(named=True):
        stakes = sorted(
            (
                {"stock": stocks[i], "pct": p}
                for i, p in zip(r["isin"], r["pct"], strict=True)
                if i in stocks
            ),
            key=lambda s: -(s["pct"] or 0),
        )
        index.append(
            {
                "key": r["holder_key"],
                "name": names.get(r["holder_key"], r["holder"]),
                "companies": r["companies"],
                "stakes": stakes,
            }
        )
    notices = obs.filter(pl.col("source") == "notice")
    n = notices.height
    coverage = {
        "notices": n,
        "named": notices["holder"].is_not_null().sum() / n if n else None,
        "stake": notices["pct_after"].is_not_null().sum() / n if n else None,
        "lists": obs.filter(pl.col("source") == "meeting")["report_id"].n_unique(),
        "holders": reg.filter(~pl.col("stale")).height,
        "companies": reg.filter(~pl.col("stale"))["isin"].n_unique(),
    }
    ups = sum(1 for r in recent if r["direction"] == "up")
    downs = sum(1 for r in recent if r["direction"] == "down")
    return {
        "by_isin": by_isin,
        "recent": recent,
        "index": index,
        "coverage": coverage,
        "ups": ups,
        "downs": downs,
        "days": CHANGE_DAYS,
    }
