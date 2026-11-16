/* The header's stock search: suggestions as you type, with each stock's icon; Enter or a
 * click opens the stock's page. data/search.json is loaded on first focus. Without this
 * script, or with no match, the form opens the screener filtered by the text. */
(function () {
  'use strict';
  var cfg = window.UC_SEARCH;
  var form = document.querySelector('form.jump');
  if (!cfg || !form) return;
  var input = form.querySelector('input');
  var list = form.querySelector('.jump-list');
  var rows = null, loading = null, shown = [], active = -1, MAX = 8;

  function fold(s) {
    return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l');
  }

  function load() {
    if (!loading) {
      loading = fetch(cfg.url).then(function (r) { return r.json(); }).then(function (d) {
        rows = d.rows.map(function (r) {
          return { ticker: r[0], name: r[1], slug: r[2], logo: r[3], market: r[4], t: fold(r[0]), n: fold(r[1]) };
        });
        form.dataset.ready = '1';
      }).catch(function () { rows = null; });
    }
    return loading;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }

  function match(q) {
    q = fold(q.trim());
    if (!q || !rows) return [];
    var scored = [];
    rows.forEach(function (r) {
      var s = r.t === q ? 0 : r.t.indexOf(q) === 0 ? 1 : r.n.indexOf(q) === 0 ? 2
        : r.t.indexOf(q) > 0 ? 3 : r.n.indexOf(' ' + q) >= 0 ? 4 : r.n.indexOf(q) > 0 ? 5 : -1;
      if (s >= 0) scored.push([s, r]);
    });
    scored.sort(function (a, b) { return a[0] - b[0] || (a[1].t < b[1].t ? -1 : 1); });
    return scored.slice(0, MAX).map(function (x) { return x[1]; });
  }

  function icon(r) {
    if (r.logo) {
      return '<img class="tlogo' + (/\.png$/.test(r.logo) ? ' tlogo-png' : '') + '" src="' + cfg.logoBase +
        esc(r.logo) + '" alt="">';
    }
    return '<span class="tlogo tmono" aria-hidden="true">' +
      esc((r.ticker || '?').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 2)) + '</span>';
  }

  function render() {
    shown = match(input.value);
    active = shown.length ? 0 : -1;
    if (!input.value.trim()) { close(); return; }
    list.innerHTML = shown.map(function (r, i) {
      return '<li role="option" id="jump-' + i + '" data-i="' + i + '"' + (i === active ? ' aria-selected="true"' : '') +
        '>' + icon(r) + '<b class="mono">' + esc(r.ticker) + '</b><span>' + esc(r.name || '') +
        (r.market === 'NC' ? ' · NC' : '') + '</span></li>';
    }).join('') + (shown.length ? '' : '<li class="muted none">' + esc(cfg.none) + '</li>') +
      '<li class="all" data-all="1">' + esc(cfg.all) + ' →</li>';
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-activedescendant', active >= 0 ? 'jump-' + active : '');
  }

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function move(d) {
    if (!shown.length) return;
    active = (active + d + shown.length) % shown.length;
    Array.prototype.forEach.call(list.querySelectorAll('li[data-i]'), function (li) {
      li.setAttribute('aria-selected', String(+li.dataset.i === active));
    });
    input.setAttribute('aria-activedescendant', 'jump-' + active);
  }

  function open(r) { location.href = cfg.stockBase + r.slug + '.html'; }
  function screener() {
    location.href = form.getAttribute('action') + '#q=' + encodeURIComponent(input.value) + '&tier=all';
  }

  input.addEventListener('focus', load);
  input.addEventListener('input', function () { load().then(render); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Escape') { close(); }
  });
  form.addEventListener('submit', function (e) {
    if (!form.dataset.ready) return;  // the inline handler opens the screener
    e.preventDefault();
    if (active >= 0 && shown[active]) open(shown[active]); else screener();
  });
  list.addEventListener('mousedown', function (e) {
    var li = e.target.closest('li');
    if (!li) return;
    e.preventDefault();
    if (li.dataset.all) screener(); else if (li.dataset.i) open(shown[+li.dataset.i]);
  });
  input.addEventListener('blur', function () { setTimeout(close, 100); });
})();
