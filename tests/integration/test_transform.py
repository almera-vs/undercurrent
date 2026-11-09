"""End-to-end: fake site → bronze → silver → dbt build (models + data tests)."""

from datetime import UTC, datetime

import duckdb
import pytest

pytest.importorskip("dbt.cli.main")

from tests.unit.test_pipeline import DAY, FakeClock, _client, _site
from undercurrent import pipeline, transform


def test_run_day_with_transform_builds_gold(tmp_path):
    clock = FakeClock(datetime(2026, 11, 6, 17, 50, tzinfo=UTC))
    root, work = tmp_path / "lake", tmp_path / "work"
    with _client(tmp_path, _site(nc_available_after=0)) as c:
        rc = pipeline.run_day(
            DAY, data_dir=root, work_dir=work, client=c, sleep=clock.sleep, clock=clock
        )
    assert rc == 0
    con = duckdb.connect(str(transform.duckdb_path(work)))
    n_reports = con.execute("select count(*) from reports").fetchone()[0]
    n_events = con.execute("select count(*) from events").fetchone()[0]
    sigs = dict(con.execute("select signal_type, count(*) from reports group by 1").fetchall())
    con.close()
    assert n_reports == 88
    assert n_events > 0
    assert sigs["insider_tx"] > 0  # the fake site serves the insider page for every report
