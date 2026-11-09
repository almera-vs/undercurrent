"""URL builders for the official GPW / NewConnect / ESPI-EBI endpoints.

Every pattern here is documented, with samples and the date it was verified, in
``docs/SOURCES.md``. Keep the two in sync.
"""

from __future__ import annotations

from datetime import date

GPW_BASE = "https://www.gpw.pl"
NC_BASE = "https://newconnect.pl"

ESPI_LIST_URL = f"{GPW_BASE}/ajaxindex.php"
ESPI_DETAIL_URL = f"{GPW_BASE}/komunikat"

# GPW quote-archive instrument types (value of the ``type`` select on /archiwum-notowan).
TYPE_INDICES = 1
TYPE_SHARES = 10

# Report-type codes on the ESPI listing form: RB current, P semi-annual,
# Q quarterly, O other, R annual.
ESPI_REPORT_TYPES: tuple[str, ...] = ("RB", "P", "Q", "O", "R")
ESPI_CATEGORIES: tuple[str, ...] = ("EBI", "ESPI")


def espi_list_form(day: date, *, offset: int = 0, limit: int = 100) -> list[tuple[str, str]]:
    """Form body for the ESPI/EBI listing AJAX POST, filtered to a single day."""
    form: list[tuple[str, str]] = [
        ("action", "GPWEspiReportUnion"),
        ("start", "ajaxSearch"),
        ("page", "komunikaty"),
        ("format", "html"),
        ("lang", "PL"),
        ("letter", ""),
        ("offset", str(offset)),
        ("limit", str(limit)),
    ]
    form += [("categoryRaports[]", c) for c in ESPI_CATEGORIES]
    form += [("typeRaports[]", t) for t in ESPI_REPORT_TYPES]
    form += [("searchText", ""), ("date", day.strftime("%d-%m-%Y"))]
    return form


def espi_detail_url(geru_id: int) -> str:
    """Public report page for one ESPI/EBI report."""
    return f"{ESPI_DETAIL_URL}?geru_id={geru_id}"


def gpw_archive_url(day: date, *, instrument_type: int, xls: bool) -> str:
    """GPW main-market quote archive for one session (XLS download or HTML table)."""
    fetch = "1" if xls else "0"
    url = (
        f"{GPW_BASE}/archiwum-notowan?fetch={fetch}&type={instrument_type}"
        f"&instrument=&date={day.strftime('%d-%m-%Y')}"
    )
    if not xls:
        url += "&show_x=Poka%C5%BC+wyniki"
    return url


def nc_daily_url(day: date) -> str:
    """NewConnect daily session statistics file.

    Files are ``.xlsx`` from late 2020 onwards and ``.xls`` before; the exact
    switch date is resolved at ingest time by reading the month listing page.
    """
    ext = "xlsx" if day >= date(2021, 1, 1) else "xls"
    return f"{NC_BASE}/pub/NEWCONNECT/statystyki/statystyki_dzienne/NC_{day:%Y%m%d}.{ext}"


def nc_month_listing_url(year: int, month: int) -> str:
    """NewConnect page listing the daily statistics files of one month."""
    return (
        f"{NC_BASE}/statystyki-okresowe?page=statystyki-okresowe"
        f"&year={year}&month={month:02d}&day=0"
    )
