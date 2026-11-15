"""Sector rotation: returns against WIG, trend and momentum, quadrants, holes, the chart."""

import numpy as np
import pytest

from undercurrent import sectors


def _seg(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int64)


def test_a_sector_pulling_ahead_and_speeding_up_is_leading() -> None:
    n = 200
    wig = np.full(n, 100.0)
    lead = 100 * np.exp(np.linspace(0, 0.3, n) ** 2)  # beats WIG faster and faster
    lag = 100 * np.exp(-np.linspace(0, 0.3, n))  # falls behind
    rows = {r["key"]: r for r in sectors.rotation({"BANKI": lead, "ENERG": lag}, wig, _seg(n))}
    b, e = rows["BANKI"], rows["ENERG"]
    assert b["quadrant"] == "leading" and b["trend"] > 100 and b["momentum"] > 100
    assert e["trend"] < 100 and e["quadrant"] in ("lagging", "improving")
    assert b["x20"] == pytest.approx(lead[-1] / lead[-21] - 1)  # WIG is flat
    assert len(b["tail"]) == sectors.TAIL_WEEKS + 1


def test_returns_and_trend_stop_at_a_hole() -> None:
    n = 120
    wig = np.full(n, 100.0)
    a = np.linspace(100, 130, n)
    seg = np.array([0] * 100 + [1] * 20)
    r = sectors.rotation({"INFO": a}, wig, seg)[0]
    assert r["x5"] is not None and r["x60"] is None  # 60 sessions back is across the hole
    assert r["trend"] is None and r["quadrant"] is None  # 20 sessions < the 50 the trend needs


def test_quadrants_and_chart() -> None:
    assert sectors.quadrant(101, 99) == "weakening"
    assert sectors.quadrant(99, 101) == "improving"
    assert sectors.quadrant(None, 100) is None
    row = {"key": "GRY", "quadrant": "leading", "tail": [[100.5, 100.2], [101.0, 100.4]]}
    titles = {
        k: k.title()
        for k in ("leading", "weakening", "lagging", "improving", "trend", "momentum", "chart")
    }
    svg = sectors.rrg_svg([row], "pl", titles)
    assert svg.startswith("<svg") and "Gry" in svg and "polyline" in svg
    assert sectors.rrg_svg([], "en", titles) == ""
