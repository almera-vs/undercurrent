from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from undercurrent import lake, pipeline
from undercurrent.fetch import PoliteClient

FIX = Path(__file__).parents[1] / "fixtures"
DAY = date(2026, 11, 6)


def _site(nc_available_after: int | None):
    """Fake gpw.pl/newconnect.pl. The NC file appears after N requests for it (None = never)."""
    listing = {
        "gpw": (FIX / "listings" / "gpw_2026-11-06.html").read_text("utf-8"),
        "nc": (FIX / "listings" / "nc_2026-11-06.html").read_text("utf-8"),
    }
    page = (FIX / "pages" / "gpw_500131_espi_legacy_insider.html").read_text("utf-8")
    shares = (FIX / "prices" / "gpw_shares_2026-11-06.xls").read_bytes()
    idx = (FIX / "prices" / "gpw_indices_2026-11-06.xls").read_bytes()
    nc = (FIX / "prices" / "nc_2026-11-06.xlsx").read_bytes()
    html404 = httpx.Response(
        200, text="<!DOCTYPE html><html>404</html>", headers={"content-type": "text/html"}
    )
    nc_hits = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        site = "nc" if "newconnect" in req.url.host else "gpw"
        url = str(req.url)
        if req.url.path == "/ajaxindex.php":
            body = req.content.decode()
            only_day = "date=06-11-2026" in body and "offset=0&" in body
            return httpx.Response(200, text=listing[site] if only_day else "")
        if req.url.path == "/komunikat":
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        if req.url.path == "/archiwum-notowan":
            if "date=06-11-2026" not in url:
                return html404
            data = idx if "type=1&" in url else shares
            return httpx.Response(
                200, content=data, headers={"content-type": "application/vnd.ms-excel"}
            )
        if "NC_20261106.xlsx" in url:
            nc_hits["n"] += 1
            if nc_available_after is not None and nc_hits["n"] > nc_available_after:
                return httpx.Response(200, content=nc, headers={"content-type": "application/xlsx"})
        return html404

    return handler


def _client(tmp_path, handler):
    return PoliteClient(
        tmp_path / "cache", min_interval_s=0, max_jitter_s=0, transport=httpx.MockTransport(handler)
    )


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start
        self.slept = 0.0

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept += s
        self.now += timedelta(seconds=s)


def test_run_day_waits_for_newconnect_file(tmp_path):
    clock = FakeClock(datetime(2026, 11, 6, 17, 50, tzinfo=UTC))
    with _client(tmp_path, _site(nc_available_after=2)) as c:
        rc = pipeline.run_day(
            DAY,
            data_dir=tmp_path / "lake",
            client=c,
            sleep=clock.sleep,
            clock=clock,
            run_transform=False,
        )
    assert rc == 0
    assert clock.slept == 2 * pipeline.NC_POLL_EVERY_S
    runs = lake.read_state(tmp_path / "lake", "runs")
    assert runs is not None
    row = runs.row(0, named=True)
    assert row["status"] == "ok" and not row["nc_pending"]
    assert row["reports_new"] == 88 and row["prices_nc_rows"] > 300


def test_run_day_marks_newconnect_pending_after_cutoff(tmp_path):
    clock = FakeClock(datetime(2026, 11, 6, 17, 50, tzinfo=UTC))
    with _client(tmp_path, _site(nc_available_after=None)) as c:
        rc = pipeline.run_day(
            DAY,
            data_dir=tmp_path / "lake",
            client=c,
            sleep=clock.sleep,
            clock=clock,
            run_transform=False,
        )
    assert rc == 0
    # cut-off 20:05 Warsaw = 19:05 UTC in November; never sleeps past it
    assert clock.now <= datetime(2026, 11, 6, 19, 5, tzinfo=UTC)
    row = lake.read_state(tmp_path / "lake", "runs").row(0, named=True)
    assert row["status"] == "degraded" and row["nc_pending"]


def test_run_started_long_before_the_cutoff_does_not_wait_all_day(tmp_path):
    """A schedule delivered at 01:50 for a day with no data yet gives up within the cap."""
    clock = FakeClock(datetime(2026, 11, 6, 0, 50, tzinfo=UTC))
    with _client(tmp_path, _site(nc_available_after=None)) as c:
        rc = pipeline.run_day(
            DAY,
            data_dir=tmp_path / "lake",
            client=c,
            sleep=clock.sleep,
            clock=clock,
            run_transform=False,
        )
    assert rc == 0
    assert clock.slept <= pipeline.NC_MAX_WAIT_S
    assert lake.read_state(tmp_path / "lake", "runs").row(0, named=True)["nc_pending"]


def test_backfill_rerun_writes_nothing_new(tmp_path):
    clock = FakeClock(datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
    root = tmp_path / "lake"
    with _client(tmp_path, _site(nc_available_after=0)) as c:
        pipeline.backfill(
            date(2026, 11, 5),
            date(2026, 11, 6),
            data_dir=root,
            client=c,
            sleep=clock.sleep,
            clock=clock,
        )
        before = sorted(p.relative_to(root) for p in root.rglob("*.parquet"))
        snap = {p: (root / p).read_bytes() for p in before if "bronze" in str(p)}
        pipeline.backfill(
            date(2026, 11, 5),
            date(2026, 11, 6),
            data_dir=root,
            client=c,
            sleep=clock.sleep,
            clock=clock,
        )
    after = sorted(p.relative_to(root) for p in root.rglob("*.parquet"))
    assert before == after
    assert all((root / p).read_bytes() == b for p, b in snap.items())
    tables = {p.parts[1] for p in after if p.parts[0] == "bronze"}
    assert tables == {"reports", "prices_gpw", "indices_gpw", "prices_nc"}


def test_backfill_yields_inside_nightly_window(tmp_path):
    clock = FakeClock(datetime(2026, 11, 9, 18, 0, tzinfo=UTC))  # Monday 18:00 UTC
    with _client(tmp_path, _site(nc_available_after=0)) as c:
        pipeline.backfill(
            DAY, DAY, data_dir=tmp_path / "lake", client=c, sleep=clock.sleep, clock=clock
        )
    assert clock.slept == 90 * 60
