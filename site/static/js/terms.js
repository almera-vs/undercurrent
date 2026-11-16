/* Definitions of technical terms in a small panel: hover or keyboard focus on a desktop,
 * a tap on a phone. A term is <span class="term" tabindex="0" title="definition">; the
 * title is moved aside here, so the browser's own tooltip does not show as well (and
 * still works if this script does not run). One panel for the page, placed inside the
 * window, so tables that scroll sideways do not clip it. */
(function () {
  'use strict';
  var panel = null, current = null;
  var canHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;

  function termOf(node) {
    return node && node.closest ? node.closest('.term') : null;
  }

  function take(el) {
    if (el.hasAttribute('title')) {
      el.setAttribute('data-def', el.getAttribute('title'));
      el.removeAttribute('title');
    }
    return el.getAttribute('data-def');
  }

  function getPanel() {
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'term-tip';
      panel.id = 'term-tip';
      panel.setAttribute('role', 'tooltip');
      panel.hidden = true;
      document.body.appendChild(panel);
    }
    return panel;
  }

  function place(el, p) {
    var r = el.getBoundingClientRect();
    p.style.left = '0px';
    p.style.top = '0px';
    var w = p.offsetWidth, h = p.offsetHeight, gap = 8;
    var x = Math.min(Math.max(gap, r.left + r.width / 2 - w / 2), window.innerWidth - w - gap);
    var y = r.bottom + gap;
    if (y + h > window.innerHeight - gap && r.top - h - gap > gap) y = r.top - h - gap;
    p.style.left = (x + window.scrollX) + 'px';
    p.style.top = (y + window.scrollY) + 'px';
  }

  function show(el) {
    var def = take(el);
    if (!def) return;
    var p = getPanel();
    p.textContent = def;
    p.hidden = false;
    if (current && current !== el) current.removeAttribute('aria-describedby');
    current = el;
    el.setAttribute('aria-describedby', 'term-tip');
    place(el, p);
  }

  function hide() {
    if (panel) panel.hidden = true;
    if (current) current.removeAttribute('aria-describedby');
    current = null;
  }

  // a title must go before the browser shows its own tooltip
  document.addEventListener('mouseover', function (e) {
    var el = termOf(e.target);
    if (el) take(el);
    if (!canHover) return;
    if (el) { if (el !== current) show(el); } else if (current) hide();
  });
  document.addEventListener('focusin', function (e) {
    if (!canHover) return;  // on a phone a tap focuses and clicks: the click decides
    var el = termOf(e.target);
    if (el) show(el); else if (current) hide();
  });
  document.addEventListener('click', function (e) {
    // a control re-rendered by its click (the screener's sort buttons) drops its panel
    setTimeout(function () { if (current && !current.isConnected) hide(); }, 0);
    if (canHover) return;
    var el = termOf(e.target);
    if (!el) { if (current) hide(); return; }
    if (el.closest('button, a')) return;  // on a phone, a tap on a control is the control
    if (el === current) hide(); else show(el);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && current) { var el = current; hide(); el.blur(); }
  });
  window.addEventListener('scroll', function () { if (current) place(current, getPanel()); }, { passive: true });
  window.addEventListener('resize', function () { if (current) place(current, getPanel()); });
})();
