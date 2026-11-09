from datetime import date

import httpx
import pytest

from undercurrent import cli
from undercurrent.fetch import PoliteClient, SourceBlockedError, is_block_page
from undercurrent.sources import espi_list_form, gpw_archive_url, nc_daily_url


def test_cli_run_dispatches(tmp_path, monkeypatch):
    from undercurrent import pipeline

    seen = {}

    def fake_run_day(day, **kw):
        seen.update(day=day, **kw)
        return 0

    monkeypatch.setattr(pipeline, "run_day", fake_run_day)
    argv = [
        "--data-dir",
        str(tmp_path),
        "run",
        "--date",
        "2026-11-06",
        "--no-publish",
        "--no-wait-nc",
    ]
    assert cli.main(argv) == 0
    assert seen["day"] == date(2026, 11, 6)
    assert seen["publish"] is False and seen["wait_for_nc"] is False


def test_cli_backfill_rejects_reversed_range(tmp_path):
    assert cli.main(["backfill", "--start", "2026-11-06", "--end", "2026-11-01"]) == 2


def test_cli_requires_command():
    with pytest.raises(SystemExit):
        cli.main([])


def test_url_builders():
    d = date(2026, 11, 6)
    url = gpw_archive_url(d, instrument_type=10, xls=True)
    assert url.endswith("type=10&instrument=&date=06-11-2026")
    assert nc_daily_url(d).endswith("NC_20261106.xlsx")
    assert nc_daily_url(date(2015, 1, 5)).endswith("NC_20150105.xls")
    form = dict(espi_list_form(d))
    assert form["date"] == "06-11-2026"
    assert form["action"] == "GPWEspiReportUnion"


def test_block_page_detection():
    assert is_block_page(b"<html><head><title>Request Rejected</title>")
    assert not is_block_page(b"%PDF-1.7 ...")


def _client(handler, tmp_path):
    return PoliteClient(
        tmp_path, min_interval_s=0, max_jitter_s=0, transport=httpx.MockTransport(handler)
    )


def test_client_raises_on_block_page(tmp_path):
    with (
        _client(lambda req: httpx.Response(200, content=b"Request Rejected"), tmp_path) as c,
        pytest.raises(SourceBlockedError),
    ):
        c.get("https://example.test/x")


def test_client_sends_conditional_headers_and_uses_304(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"hello", headers={"ETag": '"v1"'})

    with _client(handler, tmp_path) as c:
        first = c.get("https://example.test/a")
        second = c.get("https://example.test/a")
    assert first.content == second.content == b"hello"
    assert not first.from_cache and second.from_cache
    assert seen[0]["user-agent"].startswith("undercurrent/")
    assert seen[1]["if-none-match"] == '"v1"'


def test_check_lake_budget(tmp_path):
    (tmp_path / "x.parquet").write_bytes(b"0" * 2_000_000)
    assert cli.main(["--data-dir", str(tmp_path), "check-lake", "--max-mb", "5"]) == 0
    assert cli.main(["--data-dir", str(tmp_path), "check-lake", "--max-mb", "1"]) == 1
