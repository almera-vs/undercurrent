"""Milestone 6 gate: point-in-time replay never sees rows known after the as-of date."""

from datetime import UTC, datetime, time

import polars as pl
import pytest

from tests.study_fixtures import WARSAW, synthetic
from undercurrent import study


@pytest.mark.parametrize("cut_index", [700, 1100, 1500])
def test_replay_as_of_past_date(cut_index):
    ev, rt, bench, cal = synthetic()
    as_of = datetime.combine(cal[cut_index], time(23, 59), tzinfo=WARSAW).astimezone(UTC)
    inputs = study.cut_inputs(ev, rt, bench, as_of)
    study.assert_point_in_time(inputs)
    for df in (inputs.events, inputs.returns, inputs.benchmark):
        assert df["known_at"].max() <= as_of
    stats, _ = study.compute_signal_stats(inputs)
    assert (stats["as_of"] == as_of).all()
    # events whose post-window extends past as_of can't count for that horizon
    full = study.compute_signal_stats(
        study.cut_inputs(ev, rt, bench, datetime(2030, 1, 1, tzinfo=UTC))
    )[0]
    merged = stats.join(full, on=["signal_key", "horizon"], suffix="_full")
    assert (merged["n"] <= merged["n_full"]).all()


def test_unfiltered_inputs_raise():
    ev, rt, bench, cal = synthetic(n_events=40)
    as_of = datetime.combine(cal[800], time(12, 0), tzinfo=WARSAW).astimezone(UTC)
    leaky = study.StudyInputs(
        as_of=as_of,
        events=ev.select("report_id", "isin", "known_at", "event_date").with_columns(
            pl.lit("x").alias("signal_key")
        ),
        returns=rt,
        benchmark=bench,
    )
    with pytest.raises(study.LookAheadError):
        study.assert_point_in_time(leaky)
