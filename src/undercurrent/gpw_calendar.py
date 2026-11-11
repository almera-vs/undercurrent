"""GPW session calendar: which weekdays the Warsaw exchange is closed.

The exchange sets its closed days once a year (for 2026: resolution 753/2025 of the GPW
management board). They follow the Polish public holidays, plus three days that are not
public holidays: Good Friday, Christmas Eve and New Year's Eve. The rule below reproduces
every closed weekday since 2015 in the lake's own WIG calendar, apart from the one-off
closures listed in ``EXTRA_CLOSED``. A new one-off closure has to be added there.

Standard library only: the nightly workflow's gate step runs this file with the runner's
``python3`` before the project is installed::

    python3 src/undercurrent/gpw_calendar.py closed 2026-11-11        # prints the reason
    python3 src/undercurrent/gpw_calendar.py last-session 2026-11-11  # prints 2026-11-10
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

# One-off closures announced by the exchange.
EXTRA_CLOSED: dict[date, str] = {
    date(2013, 4, 16): "trading system change",
    date(2018, 1, 2): "MiFID II start",
    date(2018, 11, 12): "Independence centenary",
}


def easter(year: int) -> date:
    """Easter Sunday (Gregorian calendar, anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def closures(year: int) -> dict[date, str]:
    """Every day of ``year`` without a session for a reason other than the weekend."""
    e = easter(year)
    days = {
        date(year, 1, 1): "New Year's Day",
        date(year, 1, 6): "Epiphany",
        e - timedelta(days=2): "Good Friday",
        e + timedelta(days=1): "Easter Monday",
        date(year, 5, 1): "Labour Day",
        date(year, 5, 3): "Constitution Day",
        e + timedelta(days=60): "Corpus Christi",
        date(year, 8, 15): "Assumption Day",
        date(year, 11, 1): "All Saints' Day",
        date(year, 11, 11): "Independence Day",
        date(year, 12, 24): "Christmas Eve",
        date(year, 12, 25): "Christmas Day",
        date(year, 12, 26): "Boxing Day",
        date(year, 12, 31): "New Year's Eve",
    }
    days.update({d: why for d, why in EXTRA_CLOSED.items() if d.year == year})
    return days


def closed_reason(d: date) -> str | None:
    """Why there is no session on weekday ``d`` (a holiday's name); None on a session day.

    Weekends are not reported: callers skip them already.
    """
    if d.weekday() >= 5:
        return None
    return closures(d.year).get(d)


def is_session(d: date) -> bool:
    """Whether GPW holds a session on ``d``."""
    return d.weekday() < 5 and d not in closures(d.year)


def last_session(d: date) -> date:
    """The latest session day on or before ``d``."""
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def main(argv: list[str]) -> int:
    """``closed DAY`` prints the reason (exit 0) or nothing (exit 1); ``last-session DAY``."""
    if len(argv) != 2 or argv[0] not in ("closed", "last-session"):
        print("usage: gpw_calendar.py closed|last-session YYYY-MM-DD", file=sys.stderr)
        return 2
    d = date.fromisoformat(argv[1])
    if argv[0] == "last-session":
        print(last_session(d).isoformat())
        return 0
    reason = closed_reason(d)
    if reason:
        print(reason)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
