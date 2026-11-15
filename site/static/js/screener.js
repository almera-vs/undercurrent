/* The screener: every listed stock at the last session, from data/screener.json.
 * Sort by any column, filter by market, liquidity and text, or apply a preset. The state
 * lives in the URL hash, so a view can be bookmarked or shared. Numbers are the pipeline's
 * (stock_export.py); nothing is computed here but the order. */
(function (root) {
  'use strict';
  var C = root.UCChart;

  // column: key, kind (pct | num | int | text | sigma), group
  var COLUMNS = [
    ['ticker', 'text', 'base'], ['close', 'num', 'base'], ['ret', 'pct', 'base'],
    ['x5', 'pct', 'trend'], ['x20', 'pct', 'trend'], ['x60', 'pct', 'trend'], ['x250', 'pct', 'trend'],
    ['rsi', 'int', 'tech'], ['d50', 'pct', 'tech'], ['d200', 'pct', 'tech'], ['dhi', 'pct', 'tech'],
    ['atr', 'pct', 'tech'], ['rvol', 'num', 'tech'], ['adx', 'int', 'tech'],
    ['s20', 'pct', 'risk'], ['beta', 'num', 'risk'], ['z', 'sigma', 'risk'],
    ['var95', 'pct', 'risk'], ['es975', 'pct', 'risk'], ['dd', 'pct', 'risk'],
    ['vol_pct', 'pct', 'risk'],
    ['turnover', 'money', 'liq'], ['cost', 'pct', 'liq'],
    ['fc', 'pct', 'model'], ['fc_rank', 'int', 'model'], ['insider_net', 'money', 'model'],
    ['ey', 'pct', 'fund'], ['prof', 'pct', 'fund'], ['ag', 'pct', 'fund'], ['acc', 'pct', 'fund']
  ];

  // Presets that are chart setups are computed in Python (setups.states), the same
  // definition the Signals page tests; the row lists the ones the stock is in tonight.
  function inPreset(key) { return function (r) { return (r.presets || []).indexOf(key) >= 0; }; }
  var PRESETS = {
    all: function () { return true; },
    surprise: function (r) { return r.z !== null && Math.abs(r.z) >= 3; },
    insider: function (r) { return r.insider_net !== null && r.insider_net > 0; },
    forecast: function (r) { return r.fc !== null; }
  };
  ['new_hi', 'new_lo', 'rsi_low', 'rsi_high', 'above200', 'pullback', 'squeeze', 'breakout',
   'oversold', 'trend'].forEach(function (k) { PRESETS[k] = inPreset(k); });
  var PRESET_SORT = { new_hi: ['x250', -1], new_lo: ['x250', 1], rsi_low: ['rsi', 1], rsi_high: ['rsi', -1],
    above200: ['x60', -1], surprise: ['z', -1], insider: ['insider_net', -1], forecast: ['fc', -1],
    pullback: ['x250', -1], squeeze: ['atr', 1], breakout: ['rvol', -1], oversold: ['rsi', 1],
    trend: ['adx', -1] };

  function money(x) {
    if (x === null || !isFinite(x)) return '—';
    var a = Math.abs(x), s = x < 0 ? '−' : '';
    if (a >= 1e9) return s + C.fmt(a / 1e9, 1) + ' bn';
    if (a >= 1e6) return s + C.fmt(a / 1e6, 1) + ' M';
    if (a >= 1e3) return s + C.fmt(a / 1e3, 0) + ' k';
    return s + C.fmt(a, 0);
  }

  function cell(kind, v) {
    if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return ['—', ''];
    switch (kind) {
      case 'pct': return [C.pct(v), v > 0 ? 'up' : v < 0 ? 'down' : ''];
      case 'num': return [C.fmt(v, v >= 100 ? 1 : 2), ''];
      case 'int': return [C.fmt(v, 0), ''];
      case 'sigma': return [C.fmt(v, 1) + 'σ', Math.abs(v) >= 3 ? (v > 0 ? 'up' : 'down') : ''];
      case 'money': return [money(v), ''];
      default: return [String(v), ''];
    }
  }

  function mount(el, opts) {
    var labels = opts.labels, rows = [], st = readHash();
    var table = el.querySelector('table'), body = table.querySelector('tbody');
    var head = table.querySelector('thead tr'), count = el.querySelector('.scr-count');
    var form = el.querySelector('.scr-filters'), setupLine = el.querySelector('.scr-setup');

    function readHash() {
      var o = { sort: 'x20', dir: -1, market: 'all', tier: 'tradable', q: '', preset: 'all', groups: 'base,trend,tech,risk' };
      (location.hash.slice(1) || '').split('&').forEach(function (kv) {
        var p = kv.split('='); if (p[0] && p.length === 2) o[p[0]] = decodeURIComponent(p[1]);
      });
      o.dir = Number(o.dir) || -1;
      return o;
    }
    function writeHash() {
      var parts = ['sort', 'dir', 'market', 'tier', 'q', 'preset', 'groups'].map(function (k) {
        return k + '=' + encodeURIComponent(st[k]); });
      history.replaceState(null, '', '#' + parts.join('&'));
    }

    function visibleColumns() {
      var groups = st.groups.split(',');
      return COLUMNS.filter(function (c) { return c[2] === 'base' || groups.indexOf(c[2]) >= 0; });
    }

    function render() {
      var cols = visibleColumns();
      head.innerHTML = cols.map(function (c) {
        var active = st.sort === c[0];
        return '<th class="' + (c[1] === 'text' ? '' : 'r') + '" aria-sort="' +
          (active ? (st.dir > 0 ? 'ascending' : 'descending') : 'none') + '"><button type="button" data-sort="' +
          c[0] + '" title="' + C.esc(labels.titles[c[0]] || '') + '">' + C.esc(labels.cols[c[0]] || c[0]) +
          (active ? (st.dir > 0 ? ' ↑' : ' ↓') : '') + '</button></th>';
      }).join('');
      var q = st.q.trim().toLowerCase(), preset = PRESETS[st.preset] || PRESETS.all;
      var list = rows.filter(function (r) {
        if (st.market !== 'all' && r.market !== st.market) return false;
        if (st.tier === 'tradable' && r.tier !== 'tradable' && r.tier !== 'liquid') return false;
        if (st.tier === 'liquid' && r.tier !== 'liquid') return false;
        if (q && (r.ticker || '').toLowerCase().indexOf(q) < 0 && (r.name || '').toLowerCase().indexOf(q) < 0) return false;
        return preset(r);
      });
      var key = st.sort, dir = st.dir;
      list.sort(function (a, b) {
        var x = a[key], y = b[key];
        var xn = x === null || x === undefined, yn = y === null || y === undefined;
        if (xn && yn) return a.ticker < b.ticker ? -1 : 1;
        if (xn) return 1;
        if (yn) return -1;
        if (typeof x === 'string') return dir * x.localeCompare(y);
        return dir * (x - y);
      });
      count.textContent = labels.count.replace('{n}', list.length).replace('{t}', rows.length);
      var su = (labels.setups || {})[st.preset];
      setupLine.hidden = !su;
      if (su) setupLine.innerHTML = su;
      body.innerHTML = list.slice(0, 400).map(function (r) {
        return '<tr>' + cols.map(function (c) {
          if (c[0] === 'ticker') {
            return '<td class="sym"><a href="' + opts.stockBase + r.slug + '.html"><span class="tick-sym">' +
              C.esc(r.ticker) + '</span></a><small class="muted">' + C.esc(r.name || '') +
              (r.market === 'NC' ? ' · NC' : '') + '</small></td>';
          }
          var v = cell(c[1], r[c[0]]);
          return '<td class="r mono ' + v[1] + '">' + v[0] + '</td>';
        }).join('') + '</tr>';
      }).join('') || '<tr><td colspan="' + cols.length + '" class="muted">' + C.esc(labels.none) + '</td></tr>';
      if (list.length > 400) {
        body.insertAdjacentHTML('beforeend', '<tr><td colspan="' + cols.length + '" class="muted small">' +
          C.esc(labels.more.replace('{n}', list.length - 400)) + '</td></tr>');
      }
      writeHash();
    }

    head.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-sort]');
      if (!b) return;
      if (st.sort === b.dataset.sort) st.dir = -st.dir;
      else { st.sort = b.dataset.sort; st.dir = b.dataset.sort === 'ticker' ? 1 : -1; }
      render();
    });
    form.querySelectorAll('[data-field]').forEach(function (inp) {
      if (inp.type === 'checkbox') inp.checked = st.groups.split(',').indexOf(inp.value) >= 0;
      else inp.value = st[inp.dataset.field];
      inp.addEventListener(inp.type === 'search' ? 'input' : 'change', function () {
        if (inp.type === 'checkbox') {
          st.groups = Array.prototype.filter.call(form.querySelectorAll('input[data-field="groups"]'),
            function (x) { return x.checked; }).map(function (x) { return x.value; }).join(',');
        } else {
          st[inp.dataset.field] = inp.value;
          if (inp.dataset.field === 'preset' && PRESET_SORT[inp.value]) {
            st.sort = PRESET_SORT[inp.value][0]; st.dir = PRESET_SORT[inp.value][1];
          }
        }
        render();
      });
    });

    C.getJSON(opts.url).then(function (d) { rows = d.rows; render(); }).catch(function (err) {
      body.innerHTML = '<tr><td class="muted">' + C.esc(labels.loadError) + '</td></tr>';
      if (root.console) console.error(err);
    });
  }

  root.UCScreener = { mount: mount };
})(this);
