from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from undercurrent.parse import PL_MONTHS, parse_pl_date, parse_pl_number

MONTH_GENITIVE = {v: k for k, v in PL_MONTHS.items() if k.endswith(("a", "ego"))}
MONTH_GENITIVE[2] = "lutego"
MONTH_GENITIVE[5] = "maja"


def group(n: int, sep: str) -> str:
    s = f"{n:,}"
    return s.replace(",", sep)


@given(st.integers(min_value=0, max_value=10**12), st.sampled_from([" ", "\xa0", ".", ""]))
def test_integer_roundtrip(n, sep):
    assert parse_pl_number(group(n, sep)) == n


@given(
    st.integers(min_value=0, max_value=10**10),
    st.integers(min_value=0, max_value=99),
    st.sampled_from([" ", "\xa0", ".", ""]),
)
def test_polish_decimal_roundtrip(whole, cents, sep):
    text = f"{group(whole, sep)},{cents:02d}"
    assert parse_pl_number(text) == float(f"{whole}.{cents:02d}")


@given(st.integers(min_value=0, max_value=10**10), st.integers(min_value=0, max_value=99))
def test_english_decimal_roundtrip(whole, cents):
    text = f"{whole:,}.{cents:02d}"
    assert parse_pl_number(text) == float(f"{whole}.{cents:02d}")


@given(st.integers(min_value=1, max_value=10**8), st.sampled_from(["zł", " zł", " PLN", " szt."]))
def test_units_are_ignored(n, unit):
    assert parse_pl_number(f"{group(n, ' ')}{unit}") == n


@given(st.text(alphabet="abcxyz!?", min_size=1))
def test_non_numbers_are_none(text):
    assert parse_pl_number(text) is None


@given(st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)))
def test_date_formats_roundtrip(d):
    assert parse_pl_date(d.isoformat()) == d
    assert parse_pl_date(f"{d:%d.%m.%Y}") == d
    assert parse_pl_date(f"{d.day} {MONTH_GENITIVE[d.month]} {d.year} r.") == d
    assert parse_pl_date(f"w dniu {d.day:02d}-{d.month:02d}-{d.year} roku") == d


@given(
    st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)),
    st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)),
)
def test_first_date_wins(a, b):
    assert parse_pl_date(f"{a.isoformat()} oraz {b:%d.%m.%Y}") == a


def test_examples():
    assert parse_pl_number("1 234 567,89") == 1234567.89
    assert parse_pl_number("12,5") == 12.5
    assert parse_pl_number("1.234") == 1234
    assert parse_pl_number("0.50") == 0.5
    assert parse_pl_number("-3") == -3
    assert parse_pl_number("1,2,3") is None
    assert parse_pl_date("5 listopada 2026") == date(2026, 11, 5)
    assert parse_pl_date("31.02.2026") is None
    assert parse_pl_date(None) is None
