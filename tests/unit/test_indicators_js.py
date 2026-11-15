"""The browser's indicators (site/static/js/indicators.js) equal the reference (ta.py)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from undercurrent import ta

JS = Path(__file__).parents[2] / "site" / "static" / "js" / "indicators.js"
NODE = shutil.which("node")

RUNNER = """
const I = require(process.argv[1]);
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const [hh, ll] = I.fillRange(d.h, d.l, d.c);
const [bl, bm, bu] = I.bollinger(d.c, d.seg);
const [ml, ms, mh] = I.macd(d.c, d.seg);
const [hi, lo] = I.highLow(hh, ll, d.seg, 60);
const out = {
  sma: I.sma(d.c, 20, d.seg), ema: I.ema(d.c, 20, d.seg), rsi: I.rsi(d.c, 14, d.seg),
  macd: ml, macd_signal: ms, macd_hist: mh, bb_lo: bl, bb_mid: bm, bb_hi: bu,
  atr: I.atr(hh, ll, d.c, 14, d.seg), hi: hi, lo: lo,
};
process.stdout.write(JSON.stringify(out, (k, v) => (typeof v === 'number' && isNaN(v) ? null : v)));
"""


def _fixture() -> dict[str, list[float | None]]:
    rng = np.random.default_rng(7)
    n = 400
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    h = c * (1 + rng.uniform(0, 0.03, n))
    lo = c * (1 - rng.uniform(0, 0.03, n))
    h[[10, 200]] = np.nan  # sessions without a range
    seg = np.array([0] * 150 + [1] * 250)  # a hole after session 150
    return {"c": c.tolist(), "h": h.tolist(), "l": lo.tolist(), "seg": seg.tolist()}


def _python(d: dict[str, list[float | None]]) -> dict[str, np.ndarray]:
    c, seg = np.array(d["c"]), np.array(d["seg"])
    hh, ll = ta.fill_range(np.array(d["h"], dtype=float), np.array(d["l"]), c)
    bl, bm, bu = ta.bollinger(c, seg)
    ml, ms, mh = ta.macd(c, seg)
    hi, lo = ta.high_low(hh, ll, seg, 60)
    return {
        "sma": ta.sma(c, 20, seg),
        "ema": ta.ema(c, 20, seg),
        "rsi": ta.rsi(c, 14, seg),
        "macd": ml,
        "macd_signal": ms,
        "macd_hist": mh,
        "bb_lo": bl,
        "bb_mid": bm,
        "bb_hi": bu,
        "atr": ta.atr(hh, ll, c, 14, seg),
        "hi": hi,
        "lo": lo,
    }


def test_js_indicators_match_python() -> None:
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is needed in CI to check indicators.js")
        pytest.skip("node not installed")
    d = _fixture()
    payload = json.dumps(d).replace("NaN", "null")
    res = subprocess.run(
        [NODE, "-e", RUNNER, str(JS)],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    js = json.loads(res.stdout)
    py = _python(d)
    for name, ref in py.items():
        got = np.array([np.nan if v is None else v for v in js[name]], dtype=float)
        assert np.array_equal(np.isnan(got), np.isnan(ref)), name
        ok = ~np.isnan(ref)
        np.testing.assert_allclose(got[ok], ref[ok], rtol=1e-9, atol=1e-9, err_msg=name)
