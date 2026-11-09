"""Milestone 6 gate: signal_stats is byte-for-byte reproducible."""

from datetime import UTC, datetime

from tests.study_fixtures import synthetic
from undercurrent import lake, study


def test_signal_stats_sha256_identical_across_runs(tmp_path):
    shas = []
    for i in range(2):
        ev, rt, bench, _ = synthetic()
        inputs = study.cut_inputs(ev, rt, bench, datetime(2030, 1, 1, tzinfo=UTC))
        stats, _ = study.compute_signal_stats(inputs)
        out = tmp_path / f"run{i}" / "signal_stats.parquet"
        lake.write_frame(stats, out)
        shas.append(study.sha256_file(out))
    assert shas[0] == shas[1]
