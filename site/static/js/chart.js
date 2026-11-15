/* Charts of the analysis pages, on TradingView Lightweight Charts (vendored, Apache-2.0).
 *
 * UCChart.stock(el, opts): one stock's candles, volume, overlays, indicator panes and
 *   report markers, from data/stocks/<ISIN>.json and the calendar in data/market.json.
 * UCChart.market(el, opts): WIG with the regime's stress probability and the breadth.
 *
 * Numbers come from the pipeline (stock_export.py); indicators are recomputed here with
 * indicators.js, the copy of ta.py, so a reader can change their parameters. */
(function (root) {
  'use strict';
  var LW = root.LightweightCharts, I = root.UCIndicators;

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function theme() {
    return {
      bg: css('--panel', '#11161d'), text: css('--muted', '#8b97a7'), grid: css('--line', '#1e2630'),
      up: css('--up', '#3fb68b'), down: css('--down', '#e5534b'), accent: css('--accent', '#5aa9ff'),
      amber: css('--amber', '#e0a84e'), violet: css('--violet', '#a78bfa'), ink: css('--ink', '#e6edf3')
    };
  }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + ': ' + r.status);
      return r.json();
    });
  }

  function num(v) { return v === null || v === undefined ? NaN : v; }

  function expandRuns(runs) {
    var out = [];
    runs.forEach(function (r) { for (var i = 0; i < r[1]; i++) out.push(r[0] + i); });
    return out;
  }

  function expandSteps(points, n) {
    var out = new Array(n), k = 0;
    for (var i = 0; i < n; i++) {
      while (k + 1 < points.length && points[k + 1][0] <= i) k++;
      out[i] = points.length ? points[k][1] : 1;
    }
    return out;
  }

  function baseOptions(t, height) {
    return {
      height: height,
      autoSize: true,
      layout: { background: { type: 'solid', color: t.bg }, textColor: t.text,
                fontFamily: 'Inter, system-ui, sans-serif', attributionLogo: true,
                panes: { separatorColor: t.grid } },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.grid },
      timeScale: { borderColor: t.grid },
      crosshair: { mode: LW.CrosshairMode.Normal },
      // the wheel scrolls the page; zoom with the range buttons, a drag on the axis or a pinch
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: false }
    };
  }

  function nextDay(t) {
    var d = new Date(t + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + 1);
    return d.toISOString().slice(0, 10);
  }

  // A blank slot after the last session before each hole in the lake: lines break there and
  // the gap shows, instead of a straight line across months without data.
  function gapped(data, seg) {
    if (!seg) return data;
    var out = [];
    for (var i = 0; i < data.length; i++) {
      if (i && seg[i] !== seg[i - 1]) out.push({ time: nextDay(data[i - 1].time) });
      out.push(data[i]);
    }
    return out;
  }

  // Contiguous stretches [a, b) of finite values inside one segment of the calendar.
  function runs(values, seg) {
    var out = [], a = -1;
    for (var i = 0; i <= values.length; i++) {
      var ok = i < values.length && isFinite(values[i]) && (a < 0 || !seg || seg[i] === seg[a]);
      if (ok && a < 0) a = i;
      if (!ok && a >= 0) {
        out.push([a, i]);
        a = i < values.length && isFinite(values[i]) ? i : -1;
      }
    }
    return out;
  }

  // A line (or area) as one series per stretch: the library joins a line across blank
  // points, so a hole in the lake or an undefined stretch would be drawn as a straight line.
  // Returns the first series (for price lines); only the last shows its value on the axis.
  function addLine(chart, kind, opts, pane, values, times, seg) {
    var rs = runs(values, seg), first = null;
    rs.forEach(function (r, k) {
      var o = Object.assign({}, opts);
      if (k < rs.length - 1) o.lastValueVisible = false;
      var sr = chart.addSeries(kind, o, pane);
      var data = [];
      for (var i = r[0]; i < r[1]; i++) data.push({ time: times[i], value: values[i] });
      sr.setData(data);
      if (!first) first = sr;
    });
    return first || chart.addSeries(kind, opts, pane);
  }

  function fmt(x, d) {
    if (!isFinite(x)) return '—';
    return x.toLocaleString(document.documentElement.lang || 'pl', {
      minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function pct(x, d) {
    if (!isFinite(x)) return '—';
    return (x > 0 ? '+' : x < 0 ? '−' : '') + fmt(Math.abs(x) * 100, d === undefined ? 1 : d) + '%';
  }

  // ------------------------------------------------------------------ the stock's data

  function stockData(market, s, adjusted) {
    var pos = expandRuns(s.pos), n = pos.length;
    var f = adjusted ? expandSteps(s.adj, n) : pos.map(function () { return 1; });
    var times = pos.map(function (p) { return market.cal[p]; });
    var segAll = expandSteps(market.seg, market.cal.length);
    var seg = pos.map(function (p) { return segAll[p]; });
    function adj(a) { return a.map(function (v, i) { return num(v) * f[i]; }); }
    var c = adj(s.c), o = adj(s.o).map(function (v, i) { return isFinite(v) && v > 0 ? v : c[i]; });
    var hl = I.fillRange(adj(s.h), adj(s.l), c);
    var gaps = [0];  // blank slots before each session (chart positions = row + gaps)
    for (var i = 1; i < n; i++) gaps.push(gaps[i - 1] + (seg[i] !== seg[i - 1] ? 1 : 0));
    return { n: n, pos: pos, times: times, seg: seg, gaps: gaps, o: o, h: hl[0], l: hl[1], c: c,
             tv: s.tv.map(num), s20: s.s20.map(num), beta: s.beta.map(num),
             z: s.z.map(num),
             ev: s.ev.map(function (e) { return [e[0], s.kinds[e[1]], e[2], e[3]]; }) };
  }

  var KIND_COLORS = { insider_tx: 'up', holder_threshold: 'accent', inside_info: 'violet' };

  function markerFor(e, d, t, labels) {
    var kind = e[1], fam = kind.split(':')[0], side = kind.split(':')[1];
    var color = t[KIND_COLORS[fam] || 'text'];
    if (fam === 'insider_tx' && side === 'sell') color = t.down;
    return {
      time: d.times[e[0]], position: 'aboveBar', shape: 'circle', color: color, size: 0.6,
      id: String(e[0]) + '|' + (e[3] || ''),
      text: ''
    };
  }

  // ------------------------------------------------------------------ the stock chart

  function stock(el, opts) {
    var labels = opts.labels || {}, t = theme();
    var state = { adjusted: true, overlays: { sma20: false, sma50: true, sma200: true, bb: false, band: false },
                  panes: { rsi: true, macd: false, atr: false, z: true, beta: false } };
    var controls = el.querySelector('.chart-controls');
    var host = el.querySelector('.chart-host');
    var legend = el.querySelector('.chart-legend');
    var evList = opts.eventsEl;
    var market, s, d, chart;

    controls.querySelectorAll('input[data-overlay]').forEach(function (cb) {
      cb.checked = !!state.overlays[cb.dataset.overlay];
      cb.addEventListener('change', function () { state.overlays[cb.dataset.overlay] = cb.checked; draw(); });
    });
    controls.querySelectorAll('input[data-pane]').forEach(function (cb) {
      cb.checked = !!state.panes[cb.dataset.pane];
      cb.addEventListener('change', function () { state.panes[cb.dataset.pane] = cb.checked; draw(); });
    });
    var adjBox = controls.querySelector('input[data-adjusted]');
    if (adjBox) adjBox.addEventListener('change', function () { state.adjusted = adjBox.checked; draw(); });
    controls.querySelectorAll('button[data-range]').forEach(function (b) {
      b.addEventListener('click', function () {
        controls.querySelectorAll('button[data-range]').forEach(function (x) {
          x.setAttribute('aria-pressed', String(x === b)); });
        setRange(b.dataset.range);
      });
    });

    function setRange(r) {
      if (!chart || !d.n) return;
      var sessions = { '1m': 21, '3m': 63, '6m': 126, '1y': 250, '3y': 750 }[r];
      var last = d.n - 1 + (d.n ? d.gaps[d.n - 1] : 0);
      if (!sessions) { chart.timeScale().fitContent(); return; }
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, last - sessions), to: last + 2 });
    }

    function draw() {
      if (chart) chart.remove();
      d = stockData(market, s, state.adjusted);
      chart = LW.createChart(host, baseOptions(t, opts.height || 520));
      var candles = chart.addSeries(LW.CandlestickSeries, {
        upColor: t.up, downColor: t.down, borderVisible: false, wickUpColor: t.up, wickDownColor: t.down,
        priceLineVisible: false });
      candles.setData(gapped(d.times.map(function (tm, i) {
        return { time: tm, open: d.o[i], high: d.h[i], low: d.l[i], close: d.c[i] }; }), d.seg));
      var vol = chart.addSeries(LW.HistogramSeries, {
        priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(gapped(d.times.map(function (tm, i) {
        return { time: tm, value: isFinite(d.tv[i]) ? d.tv[i] * 1000 : 0,
                 color: (d.c[i] >= (i ? d.c[i - 1] : d.c[i]) ? t.up : t.down) + '55' }; }), d.seg));

      var thin = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
      function overlay(values, color, extra) {
        return addLine(chart, LW.LineSeries, Object.assign({ color: color }, thin, extra || {}), 0,
                       values, d.times, d.seg);
      }
      if (state.overlays.sma20) overlay(I.sma(d.c, 20, d.seg), t.violet);
      if (state.overlays.sma50) overlay(I.sma(d.c, 50, d.seg), t.amber);
      if (state.overlays.sma200) overlay(I.sma(d.c, 200, d.seg), t.accent);
      if (state.overlays.bb) {
        var bb = I.bollinger(d.c, d.seg);
        overlay(bb[0], t.text, { lineStyle: 2 }); overlay(bb[2], t.text, { lineStyle: 2 });
      }
      if (state.overlays.band) {
        // the ±1σ range the market state forecast 20 sessions earlier, for this session's close
        var lo = [], hi = [];
        for (var i = 0; i < d.n; i++) {
          var j = i - 20, ok = j >= 0 && d.seg[j] === d.seg[i] && isFinite(d.s20[j]);
          lo.push(ok ? d.c[j] * Math.exp(-d.s20[j]) : NaN);
          hi.push(ok ? d.c[j] * Math.exp(d.s20[j]) : NaN);
        }
        overlay(lo, t.ink + '66', { lineStyle: 1 }); overlay(hi, t.ink + '66', { lineStyle: 1 });
      }

      var pane = 0;
      function addPane(stretch) { pane += 1; return pane; }
      var paneSeries = [];
      if (state.panes.rsi) {
        var p = addPane();
        var r = addLine(chart, LW.LineSeries, Object.assign({ color: t.violet }, thin), p,
                        I.rsi(d.c, 14, d.seg), d.times, d.seg);
        r.createPriceLine({ price: 70, color: t.grid, lineStyle: 2, axisLabelVisible: false });
        r.createPriceLine({ price: 30, color: t.grid, lineStyle: 2, axisLabelVisible: false });
        paneSeries.push(['RSI 14', r]);
      }
      if (state.panes.macd) {
        var pm = addPane(), m = I.macd(d.c, d.seg);
        var hist = chart.addSeries(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pm);
        hist.setData(gapped(d.times.map(function (tm, i) {
          return isFinite(m[2][i]) ? { time: tm, value: m[2][i], color: (m[2][i] >= 0 ? t.up : t.down) + '88' } : { time: tm }; }), d.seg));
        addLine(chart, LW.LineSeries, Object.assign({ color: t.accent }, thin), pm, m[0], d.times, d.seg);
        addLine(chart, LW.LineSeries, Object.assign({ color: t.amber }, thin), pm, m[1], d.times, d.seg);
        paneSeries.push(['MACD', hist]);
      }
      if (state.panes.atr) {
        var pa = addPane(), a = I.atr(d.h, d.l, d.c, 14, d.seg);
        var ar = addLine(chart, LW.LineSeries, Object.assign({ color: t.text }, thin), pa,
                         a.map(function (v, i) { return v / d.c[i] * 100; }), d.times, d.seg);
        paneSeries.push(['ATR %', ar]);
      }
      if (state.panes.z) {
        var pz = addPane();
        var zs = chart.addSeries(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pz);
        zs.setData(gapped(d.times.map(function (tm, i) {
          var z = d.z[i];
          return isFinite(z) ? { time: tm, value: z, color: Math.abs(z) >= 3 ? (z > 0 ? t.up : t.down) : t.text + '66' } : { time: tm }; }), d.seg));
        paneSeries.push([labels.surprise || 'Surprise σ', zs]);
      }
      if (state.panes.beta) {
        var pb = addPane();
        var bs = addLine(chart, LW.LineSeries, Object.assign({ color: t.accent }, thin), pb,
                         d.beta, d.times, d.seg);
        paneSeries.push([labels.beta || 'Beta to WIG', bs]);
      }
      var panes = chart.panes();
      if (panes.length > 1) {
        panes[0].setStretchFactor(3);
        for (var k = 1; k < panes.length; k++) panes[k].setStretchFactor(1);
      }

      var marks = d.ev.filter(function (e) { return e[0] < d.n; })
        .map(function (e) { return markerFor(e, d, t, labels); });
      LW.createSeriesMarkers(candles, marks);
      chart.subscribeClick(function (param) {
        var id = param.hoveredObjectId;
        if (!id || typeof id !== 'string') return;
        var page = id.split('|')[1];
        if (page) window.location.href = (opts.eventBase || '') + page + '.html';
      });

      var byTime = {};
      d.times.forEach(function (tm, i) { byTime[tm] = i; });
      function showLegend(i) {
        if (i === undefined || i < 0) i = d.n - 1;
        var prev = i > 0 ? d.c[i - 1] : NaN;
        var evHere = d.ev.filter(function (e) { return e[0] === i; });
        legend.innerHTML =
          '<span class="mono">' + d.times[i] + '</span>' +
          ' <span>O <b class="mono">' + fmt(d.o[i], 2) + '</b></span>' +
          ' <span>H <b class="mono">' + fmt(d.h[i], 2) + '</b></span>' +
          ' <span>L <b class="mono">' + fmt(d.l[i], 2) + '</b></span>' +
          ' <span>C <b class="mono">' + fmt(d.c[i], 2) + '</b></span>' +
          ' <b class="mono ' + (d.c[i] >= prev ? 'up' : 'down') + '">' + pct(d.c[i] / prev - 1, 2) + '</b>' +
          ' <span>' + (labels.turnover || 'Turnover') + ' <b class="mono">' + fmt(d.tv[i], 0) + ' k</b></span>' +
          (isFinite(d.z[i]) ? ' <span>' + (labels.surprise || 'Surprise') + ' <b class="mono">' + fmt(d.z[i], 1) + 'σ</b></span>' : '') +
          (evHere.length ? '<span class="legend-ev">' + evHere.map(function (e) {
            return '<span class="chip chip-mini">' + esc((labels.kinds || {})[e[1]] || e[1]) + '</span> ' + esc(e[2]);
          }).join('<br>') + '</span>' : '');
      }
      chart.subscribeCrosshairMove(function (param) {
        showLegend(param && param.time !== undefined ? byTime[param.time] : undefined);
      });
      showLegend();
      var pressed = controls.querySelector('button[data-range][aria-pressed="true"]');
      if (opts.focus) focusOn(opts.focus);
      else setRange(pressed ? pressed.dataset.range : '1y');
    }

    function focusOn(day) {
      var i = d.times.indexOf(day);
      if (i < 0) { for (i = 0; i < d.n && d.times[i] < day; i++); }
      var x = Math.min(i, d.n - 1), at = x + (x >= 0 ? d.gaps[x] : 0), end = d.n - 1 + d.gaps[d.n - 1];
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, at - 60), to: Math.min(end + 2, at + 45) });
    }

    function renderEvents() {
      if (!evList) return;
      var items = d.ev.slice(-40).reverse();
      if (!items.length) { evList.innerHTML = '<li class="muted">' + esc(labels.noEvents || '—') + '</li>'; return; }
      evList.innerHTML = items.map(function (e) {
        var title = esc(e[2] || ''), kind = esc((labels.kinds || {})[e[1]] || e[1]);
        var link = e[3] ? '<a href="' + (opts.eventBase || '') + e[3] + '.html" lang="pl">' + title + '</a>' : '<span lang="pl">' + title + '</span>';
        return '<li><time class="mono muted">' + d.times[e[0]] + '</time><span><span class="chip chip-mini chip-' +
          e[1].split(':')[0] + '">' + kind + '</span> ' + link + '</span></li>';
      }).join('');
    }

    host.setAttribute('aria-busy', 'true');
    Promise.all([getJSON(opts.marketUrl), getJSON(opts.stockUrl)]).then(function (res) {
      market = res[0]; s = res[1];
      draw();
      renderEvents();
      host.removeAttribute('aria-busy');
    }).catch(function (err) {
      host.removeAttribute('aria-busy');
      host.innerHTML = '<p class="muted">' + esc(labels.loadError || 'Chart data could not be loaded.') + '</p>';
      if (root.console) console.error(err);
    });
  }

  function esc(x) {
    return String(x).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }

  // ------------------------------------------------------------------ the market chart

  function market(el, opts) {
    var t = theme(), labels = opts.labels || {};
    getJSON(opts.marketUrl).then(function (m) {
      var chart = LW.createChart(el, baseOptions(t, opts.height || 380));
      var wig = m.index.WIG || [], seg = expandSteps(m.seg, m.cal.length);
      addLine(chart, LW.AreaSeries, {
        lineColor: t.accent, topColor: t.accent + '44', bottomColor: t.accent + '05', lineWidth: 2,
        priceLineVisible: false }, 0, wig.map(num), m.cal, seg);
      var stress = chart.addSeries(LW.HistogramSeries, {
        priceLineVisible: false, lastValueVisible: false, priceFormat: { type: 'percent' } }, 1);
      stress.setData(gapped(m.cal.map(function (tm, i) {
        var v = num(m.regime[i]);
        return isFinite(v) ? { time: tm, value: v * 100, color: v > 0.5 ? t.down + 'aa' : t.text + '44' } : { time: tm };
      }), seg));
      var b = m.breadth;
      addLine(chart, LW.LineSeries, {
        color: t.amber, lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
        priceFormat: { type: 'percent' } }, 2,
        b.n200.map(function (n, i) { return n >= 20 ? 100 * b.above200[i] / n : NaN; }), m.cal, seg);
      var panes = chart.panes();
      panes[0].setStretchFactor(4); panes[1].setStretchFactor(1); panes[2].setStretchFactor(1.4);
      var n = m.cal.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 500), to: n + 2 });
      if (opts.legendEl) {
        opts.legendEl.innerHTML = '<span class="key key-wig"></span> WIG · <span class="key key-stress"></span> ' +
          esc(labels.stress || 'stress probability') + ' · <span class="key key-breadth"></span> ' +
          esc(labels.breadth || 'share above SMA200');
      }
    }).catch(function (err) {
      el.innerHTML = '<p class="muted">' + esc(labels.loadError || 'Chart data could not be loaded.') + '</p>';
      if (root.console) console.error(err);
    });
  }

  root.UCChart = { stock: stock, market: market, pct: pct, fmt: fmt, esc: esc, getJSON: getJSON };
})(this);
