/* Chart indicators: the browser's copy of src/undercurrent/ta.py (the reference).
 * Same formulas, same warm-up, same restart after a hole in the lake (seg changes).
 * scripts/check_indicators.mjs runs both on one fixture in CI.
 * Every function takes plain arrays and returns an array of the same length, NaN where
 * the indicator is not defined yet. */
(function (root) {
  'use strict';

  function segments(seg, n) {
    if (!seg || n === 0) return [[0, n]];
    var out = [], a = 0;
    for (var i = 1; i < n; i++) if (seg[i] !== seg[i - 1]) { out.push([a, i]); a = i; }
    out.push([a, n]);
    return out;
  }

  function nans(n) { var o = new Array(n); for (var i = 0; i < n; i++) o[i] = NaN; return o; }

  function perSegment(f, seg, arrays) {
    var n = arrays[0].length, out = nans(n);
    segments(seg, n).forEach(function (s) {
      var part = f.apply(null, arrays.map(function (a) { return a.slice(s[0], s[1]); }));
      for (var i = 0; i < part.length; i++) out[s[0] + i] = part[i];
    });
    return out;
  }

  function mean(x, a, b) { var s = 0; for (var i = a; i < b; i++) s += x[i]; return s / (b - a); }

  // y = α·x + (1 − α)·y₋₁, started at index n − 1 from the mean of the first n
  function smooth(x, n, alpha) {
    var out = nans(x.length);
    if (x.length < n) return out;
    var y = mean(x, 0, n);
    out[n - 1] = y;
    for (var i = n; i < x.length; i++) { y = alpha * x[i] + (1 - alpha) * y; out[i] = y; }
    return out;
  }

  function sma1(x, n) {
    var out = nans(x.length);
    if (x.length < n) return out;
    var c = [0];
    for (var i = 0; i < x.length; i++) c.push(c[i] + x[i]);
    for (var j = n - 1; j < x.length; j++) out[j] = (c[j + 1] - c[j + 1 - n]) / n;
    return out;
  }

  function ema1(x, n) { return smooth(x, n, 2 / (n + 1)); }

  function rsi1(c, n) {
    var out = nans(c.length);
    if (c.length <= n) return out;
    var up = [], dn = [];
    for (var i = 1; i < c.length; i++) {
      var d = c[i] - c[i - 1];
      up.push(Math.max(d, 0)); dn.push(Math.max(-d, 0));
    }
    var g = smooth(up, n, 1 / n), l = smooth(dn, n, 1 / n);
    for (var k = 0; k < g.length; k++) {
      if (isNaN(g[k])) continue;
      out[k + 1] = l[k] === 0 ? (g[k] === 0 ? 50 : 100) : 100 - 100 / (1 + g[k] / l[k]);
    }
    return out;
  }

  function macd1(c, fast, slow, signal) {
    var f = ema1(c, fast), s = ema1(c, slow), line = c.map(function (_, i) { return f[i] - s[i]; });
    var sig = nans(c.length), first = -1;
    for (var i = 0; i < line.length; i++) if (!isNaN(line[i])) { first = i; break; }
    if (first >= 0) {
      var e = ema1(line.slice(first), signal);
      for (var j = 0; j < e.length; j++) sig[first + j] = e[j];
    }
    return [line, sig];
  }

  function std1(x, n) {
    var out = nans(x.length);
    for (var j = n - 1; j < x.length; j++) {
      var m = mean(x, j + 1 - n, j + 1), v = 0;
      for (var i = j + 1 - n; i <= j; i++) v += (x[i] - m) * (x[i] - m);
      out[j] = Math.sqrt(v / n);
    }
    return out;
  }

  function atr1(h, l, c, n) {
    var tr = h.map(function (_, i) {
      var r = h[i] - l[i];
      if (i === 0) return r;
      return Math.max(r, Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
    });
    return smooth(tr, n, 1 / n);
  }

  function rolling(x, n, pick) {
    var out = nans(x.length);
    for (var j = n - 1; j < x.length; j++) {
      var v = x[j + 1 - n];
      for (var i = j + 2 - n; i <= j; i++) v = pick(v, x[i]);
      out[j] = v;
    }
    return out;
  }

  function adx1(h, l, c, n) {
    var len = c.length, out = [nans(len), nans(len), nans(len)];
    if (len <= 2 * n) return out;
    var pdm = [], mdm = [], tr = [];
    for (var i = 1; i < len; i++) {
      var up = h[i] - h[i - 1], down = l[i - 1] - l[i];
      pdm.push(up > down && up > 0 ? up : 0);
      mdm.push(down > up && down > 0 ? down : 0);
      tr.push(Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])));
    }
    var a = 1 / n, st = smooth(tr, n, a), sp = smooth(pdm, n, a), sm = smooth(mdm, n, a);
    var pdi = [], mdi = [], dx = [];
    for (var k = 0; k < st.length; k++) {
      var p = st[k] > 0 ? 100 * sp[k] / st[k] : 0, m = st[k] > 0 ? 100 * sm[k] / st[k] : 0;
      if (isNaN(st[k])) { p = NaN; m = NaN; }
      pdi.push(p); mdi.push(m);
      dx.push(p + m > 0 ? 100 * Math.abs(p - m) / (p + m) : 0);
    }
    var first = n - 1, ad = smooth(dx.slice(first), n, a);
    for (var j = 0; j < st.length; j++) {
      out[0][j + 1] = j >= first ? ad[j - first] : NaN;
      out[1][j + 1] = pdi[j]; out[2][j + 1] = mdi[j];
    }
    return out;
  }

  var I = {
    YEAR: 250, RSI_N: 14, ATR_N: 14, MACD: [12, 26, 9], BB: [20, 2],
    sma: function (c, n, seg) { return perSegment(function (x) { return sma1(x, n); }, seg, [c]); },
    ema: function (c, n, seg) { return perSegment(function (x) { return ema1(x, n); }, seg, [c]); },
    rsi: function (c, n, seg) { return perSegment(function (x) { return rsi1(x, n || 14); }, seg, [c]); },
    macd: function (c, seg, fast, slow, signal) {
      fast = fast || 12; slow = slow || 26; signal = signal || 9;
      var line = perSegment(function (x) { return macd1(x, fast, slow, signal)[0]; }, seg, [c]);
      var sig = perSegment(function (x) { return macd1(x, fast, slow, signal)[1]; }, seg, [c]);
      return [line, sig, line.map(function (v, i) { return v - sig[i]; })];
    },
    bollinger: function (c, seg, n, k) {
      n = n || 20; k = k === undefined ? 2 : k;
      var mid = I.sma(c, n, seg);
      var sd = perSegment(function (x) { return std1(x, n); }, seg, [c]);
      return [mid.map(function (m, i) { return m - k * sd[i]; }), mid,
              mid.map(function (m, i) { return m + k * sd[i]; })];
    },
    atr: function (h, l, c, n, seg) {
      return perSegment(function (a, b, x) { return atr1(a, b, x, n || 14); }, seg, [h, l, c]);
    },
    highLow: function (h, l, seg, n) {
      n = n || 250;
      return [perSegment(function (x) { return rolling(x, n, Math.max); }, seg, [h]),
              perSegment(function (x) { return rolling(x, n, Math.min); }, seg, [l])];
    },
    adx: function (h, l, c, n, seg) {
      n = n || 14;
      return [0, 1, 2].map(function (k) {
        return perSegment(function (a, b, x) { return adx1(a, b, x, n)[k]; }, seg, [h, l, c]);
      });
    },
    fillRange: function (h, l, c) {
      var hh = c.map(function (x, i) { var v = h[i] > 0 ? h[i] : x; return Math.max(v, x); });
      var ll = c.map(function (x, i) { var v = l[i] > 0 ? l[i] : x; return Math.min(v, x); });
      return [hh, ll];
    }
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = I;
  else root.UCIndicators = I;
})(this);
