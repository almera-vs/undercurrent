"""Finding the holes in the price lake, and a backfill that stops when the source is down."""

from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl

from tests.unit.test_pipeline import FakeClock
from undercurrent import gpw_calendar, lake, pipeline
from undercurrent.fetch import PoliteClient


def _sessions(a: date, b: date) -> list[date]:
    days = (a + timedelta(days=i) for i in range((b - a).days + 1))
    return [d for d in days if gpw_calendar.is_session(d)]


def _land(root, days: list[date]) -> None:
    for d in days:
        lake.append_partition(
            root, "bronze", "prices_gpw", d, pl.DataFrame({"trade_date": [d], "close": [1.0]})
        )


def test_gaps_are_the_sessions_without_a_price_file(tmp_path) -> None:
    sessions = _sessions(date(2018, 11, 1), date(2019, 1, 31))
    gone = [d for d in sessions if date(2018, 12, 12) <= d <= date(2018, 12, 31)]
    _land(tmp_path, [d for d in sessions if d not in gone])
    assert pipeline.price_gaps(tmp_path, date(2018, 11, 1), date(2019, 1, 31)) == gone
    # 24-26 and 31 December are holidays, not gaps
    assert date(2018, 12, 24) not in gone and date(2018, 12, 31) not in gone


def test_compacted_months_count_as_present(tmp_path) -> None:
    feb = _sessions(date(2016, 2, 1), date(2016, 2, 29))
    _land(tmp_path, feb)
    lake.compact_month(tmp_path, "bronze", "prices_gpw", date(2016, 2, 1))
    assert not list((tmp_path / "bronze" / "prices_gpw").glob("date=*"))
    assert pipeline.price_gaps(tmp_path, date(2016, 2, 1), date(2016, 2, 29)) == []


def test_ranges_follow_runs_and_take_in_the_month_edges() -> None:
    dec = [d for d in _sessions(date(2018, 12, 12), date(2018, 12, 31))]
    jun = _sessions(date(2018, 6, 27), date(2018, 6, 29))
    feb = _sessions(date(2020, 2, 1), date(2020, 2, 29))
    one = [date(2016, 5, 18)]
    ranges = pipeline.gap_ranges(jun + dec + feb + one)
    assert ranges == [
        (date(2016, 5, 18), date(2016, 5, 18)),  # one session inside a month
        (date(2018, 6, 27), date(2018, 6, 30)),  # to the month's end: its last session
        (date(2018, 12, 12), date(2018, 12, 31)),
        (date(2020, 2, 1), date(2020, 2, 29)),  # a whole month, weekends included
    ]
    # a run never spans two months: each month is its own job
    assert pipeline.gap_ranges(_sessions(date(2019, 1, 28), date(2019, 2, 5))) == [
        (date(2019, 1, 28), date(2019, 1, 31)),
        (date(2019, 2, 1), date(2019, 2, 5)),
    ]


def test_backfill_stops_when_the_source_is_unreachable(tmp_path) -> None:
    requests: list[str] = []

    def down(req: httpx.Request) -> httpx.Response:
        requests.append(str(req.url))
        raise httpx.ConnectTimeout("timed out", request=req)

    slept: list[float] = []
    client = PoliteClient(
        tmp_path / "cache",
        min_interval_s=0,
        max_jitter_s=0,
        transport=httpx.MockTransport(down),
        sleep=slept.append,
    )
    noon = FakeClock(datetime(2020, 2, 3, 11, 0, tzinfo=UTC))
    with client:
        rc = pipeline.backfill(
            date(2020, 2, 1),
            date(2020, 2, 29),
            data_dir=tmp_path / "lake",
            client=client,
            sleep=slept.append,
            clock=noon,
        )
    assert rc == 1
    # each day's first request is tried four times; three days, then it stops: no retry pass
    assert len(requests) == pipeline.MAX_UNREACHABLE_DAYS * 4
