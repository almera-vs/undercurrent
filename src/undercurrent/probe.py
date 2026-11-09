"""Reachability probe for every official source.

Run from a GitHub-hosted runner (``.github/workflows/probe.yml``) to prove which
endpoints answer datacenter IPs. Results feed ``docs/SOURCES.md``. One request per
source, throttled by :class:`~undercurrent.fetch.PoliteClient`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date

from undercurrent.fetch import PoliteClient, SourceBlockedError, is_block_page
from undercurrent.sources import (
    ESPI_LIST_URL,
    espi_detail_url,
    espi_list_form,
    gpw_archive_url,
    nc_daily_url,
)


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one probe request."""

    name: str
    url: str
    ok: bool
    status: int | None
    content_type: str
    size: int
    blocked: bool
    note: str


def _targets(day: date, geru_id: int, attachment_path: str | None) -> list[tuple[str, str, str]]:
    targets = [
        ("robots_gpw", "GET", "https://www.gpw.pl/robots.txt"),
        ("robots_newconnect", "GET", "https://newconnect.pl/robots.txt"),
        ("espi_list", "POST", ESPI_LIST_URL),
        ("espi_detail", "GET", espi_detail_url(geru_id)),
        ("gpw_shares_xls", "GET", gpw_archive_url(day, instrument_type=10, xls=True)),
        ("gpw_indices_html", "GET", gpw_archive_url(day, instrument_type=1, xls=False)),
        ("newconnect_daily", "GET", nc_daily_url(day)),
        ("pap_espiebi", "GET", "https://espiebi.pap.pl/"),
    ]
    if attachment_path:
        targets.append(("espi_attachment", "GET", f"https://www.gpw.pl/{attachment_path}"))
    return targets


def run_probe(day: date, geru_id: int, attachment_path: str | None = None) -> list[ProbeResult]:
    """Probe all sources once and return one result per source (never raises)."""
    results: list[ProbeResult] = []
    with PoliteClient() as client:
        for name, method, url in _targets(day, geru_id, attachment_path):
            try:
                if method == "POST":
                    r = client.post(url, data=espi_list_form(day, limit=5), use_cache=False)
                else:
                    r = client.get(url, use_cache=False)
                results.append(
                    ProbeResult(
                        name,
                        url,
                        True,
                        r.status,
                        r.content_type,
                        len(r.content),
                        is_block_page(r.content),
                        "",
                    )
                )
            except SourceBlockedError as exc:
                results.append(ProbeResult(name, url, False, None, "", 0, True, str(exc)))
            except Exception as exc:  # report every failure mode, keep probing
                note = f"{type(exc).__name__}: {exc}"
                results.append(ProbeResult(name, url, False, None, "", 0, False, note))
    return results


def to_markdown(results: list[ProbeResult]) -> str:
    """Render results as a Markdown table (for ``$GITHUB_STEP_SUMMARY``)."""
    lines = [
        "| source | ok | status | blocked | size | content-type | note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {'yes' if r.ok else 'NO'} | {r.status} | {'YES' if r.blocked else 'no'} "
            f"| {r.size} | {r.content_type} | {r.note[:80]} |"
        )
    return "\n".join(lines)


def to_json(results: list[ProbeResult]) -> str:
    """Serialise results as JSON."""
    return json.dumps([asdict(r) for r in results], indent=2)
