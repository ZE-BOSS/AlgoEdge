// Alphavantiq Capital — public site. No framework, no build: it reads three
// public endpoints and posts one form.
(function () {
  'use strict';
  const C = window.AVQ || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  // numbers arrive as decimal strings; format them as strings
  const money = (v) => {
    const [i, f = ''] = String(v).split('.');
    return '$' + i.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (f && f !== '00' ? '.' + (f + '0').slice(0, 2) : '');
  };
  const pct = (v, sign = true) => (sign && !String(v).startsWith('-') && Number(v) !== 0 ? '+' : '')
    + String(v).replace('-', '−') + '%';
  const fmtDate = (iso, opts = { month: 'short', year: 'numeric' }) =>
    new Date(iso + 'T12:00:00Z').toLocaleDateString(undefined, opts);
  const get = (path) => fetch(C.api + path).then((r) => (r.ok ? r.json() : Promise.reject(r)));

  $$('[data-app-link]').forEach((a) => { a.href = C.app; });
  $('#year').textContent = new Date().getFullYear();
  const contact = $('#contact');
  if (C.email) contact.href = 'mailto:' + C.email; else contact.hidden = true;
  if (C.regulatory) { const r = $('#regulatory'); r.textContent = C.regulatory; r.hidden = false; }

  // ── terms ──────────────────────────────────────────────────────────────
  get('/api/public/terms').then((t) => {
    const set = (k, v) => $$(`[data-term="${k}"]`).forEach((el) => { el.textContent = v; });
    set('min', money(t.min_investment));
    set('mgmt', `${t.management_fee_pct}% a year`);
    set('perf', `${t.performance_fee_pct}%`);
    set('lockup', t.lockup_days % 30 === 0 && t.lockup_days ? `${t.lockup_days / 30} month${t.lockup_days > 30 ? 's' : ''}` : `${t.lockup_days} days`);
    set('cap', `${t.withdrawal_cap_pct}% of monthly profit`);
    set('notice', t.notice_days % 7 === 0 && t.notice_days ? `${t.notice_days / 7} week${t.notice_days > 7 ? 's' : ''}` : `${t.notice_days} days`);
  }).catch(() => {});

  // ── performance ────────────────────────────────────────────────────────
  get('/api/public/performance').then((p) => {
    if (!p.enough_history) {
      $('#perf-asof').textContent = p.since
        ? `Our live track record began on ${fmtDate(p.since, { day: 'numeric', month: 'long', year: 'numeric' })}; figures appear once there is more than one price.`
        : 'Our live track record will appear here once trading begins.';
      $('#perf').hidden = true;
      return;
    }
    $('#perf-asof').textContent = `Live · updated ${fmtDate(p.as_of, { day: 'numeric', month: 'short', year: 'numeric' })}`;
    $('[data-perf="return"]').textContent = pct(p.return_pct);
    $('[data-perf="dd"]').textContent = pct(p.max_drawdown_pct, false);
    const months = Math.max(1, Math.round(p.days / 30.4));
    $('[data-perf="since"]').textContent = `${months} month${months > 1 ? 's' : ''}`;
    if (p.note) $('#perf-note').textContent = p.note;
    drawLine($('#nav-chart'), p.series);
    let w = window.innerWidth;
    window.addEventListener('resize', () => {
      if (Math.abs(window.innerWidth - w) < 40) return;
      w = window.innerWidth;
      drawLine($('#nav-chart'), p.series);
    });
    drawMonths($('#months'), p.months);
  }).catch(() => {
    $('#perf-asof').textContent = 'The live figures could not be loaded just now.';
    $('#perf').hidden = true;
  });

  // single series: gold line, crosshair tooltip on hover/touch, no legend (the caption names it)
  function drawLine(el, pts) {
    if (!pts || pts.length < 2) { el.innerHTML = '<p class="empty">Not enough history yet.</p>'; return; }
    // drawn at the container's real width, so axis text stays readable on a
    // phone instead of a fixed-width chart shrunk to fit
    const W = Math.max(280, Math.round(el.clientWidth || 720)), H = W < 500 ? 180 : 220;
    const P = { t: 12, r: 12, b: 26, l: 52 };
    const ys = pts.map((p) => Number(p.nav));
    let lo = Math.min(...ys), hi = Math.max(...ys);
    const pad = (hi - lo || 1) * 0.08; lo -= pad; hi += pad;
    const x = (i) => P.l + (i / (pts.length - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const d = ys.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
    const ticks = [0, 0.5, 1].map((f) => lo + pad + f * (hi - lo - 2 * pad));
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price per unit from ${fmtDate(pts[0].date)} to ${fmtDate(pts.at(-1).date)}">
      ${ticks.map((t) => `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(t)}" y2="${y(t)}"/><text class="axis" x="${P.l - 8}" y="${y(t)}" dy="0.32em" text-anchor="end">${t.toFixed(1)}</text>`).join('')}
      <text class="axis" x="${P.l}" y="${H - 6}">${fmtDate(pts[0].date)}</text>
      <text class="axis" x="${W - P.r}" y="${H - 6}" text-anchor="end">${fmtDate(pts.at(-1).date)}</text>
      <path class="line" d="${d}"/>
      <line class="cross" y1="${P.t}" y2="${H - P.b}" visibility="hidden"/>
      <circle class="dot hover" r="5" visibility="hidden"/>
      <circle class="dot" r="4" cx="${x(pts.length - 1)}" cy="${y(ys.at(-1))}"/>
    </svg><div class="tip" hidden></div>`;
    const svg = el.querySelector('svg'), cross = svg.querySelector('.cross'),
      dot = svg.querySelector('.dot.hover'), tip = el.querySelector('.tip');
    const move = (ev) => {
      const r = svg.getBoundingClientRect();
      const sx = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.min(pts.length - 1, Math.max(0, Math.round(((sx - P.l) / (W - P.l - P.r)) * (pts.length - 1))));
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(ys[i])); dot.setAttribute('visibility', 'visible');
      tip.hidden = false;
      tip.innerHTML = `<b>${Number(pts[i].nav).toFixed(4)}</b>${fmtDate(pts[i].date, { day: 'numeric', month: 'short', year: 'numeric' })}`;
      tip.style.left = Math.min(Math.max((x(i) / W) * r.width, 60), r.width - 60) + 'px';
    };
    svg.addEventListener('pointermove', move);
    svg.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); });
  }

  // gain/loss is polarity: teal right of zero, red left, every value labelled
  function drawMonths(el, months) {
    if (!months || !months.length) { el.innerHTML = '<p class="empty">The first full month is still in progress.</p>'; return; }
    const max = Math.max(...months.map((m) => Math.abs(Number(m.return_pct))), 0.01);
    el.innerHTML = months.slice(-24).map((m) => {
      const v = Number(m.return_pct), w = (Math.abs(v) / max) * 50;
      const label = new Date(m.month + '-15T12:00:00Z').toLocaleDateString(undefined, { month: 'short', year: '2-digit' });
      return `<div class="m-row"><span class="lbl">${label}${m.partial ? '*' : ''}</span>
        <span class="m-track"><span class="m-bar ${v < 0 ? 'neg' : 'pos'}" style="width:${w}%"></span></span>
        <span class="val">${pct(m.return_pct)}</span></div>`;
    }).join('') + (months.some((m) => m.partial) ? '<p class="fine">* month in progress</p>' : '');
  }

  // ── Android download (Phase 6 release manager) ─────────────────────────
  get('/api/public/app').then((a) => {
    if (!a || !a.url) return;
    const link = $('#apk-link');
    link.href = a.url; link.hidden = false;
    $('#apk-version').textContent = `(v${a.version})`;
    $('#apk-missing').hidden = true;
  }).catch(() => {});

  // ── application form ───────────────────────────────────────────────────
  const form = $('#apply-form'), msg = $('#apply-msg');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    msg.className = 'form-msg';
    if (!form.checkValidity()) {
      msg.textContent = 'Please fill in your name and email, and tick the risk notice.';
      msg.classList.add('err');
      form.reportValidity();
      return;
    }
    const data = Object.fromEntries(new FormData(form).entries());
    data.consent = form.consent.checked;
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true; msg.textContent = 'Sending…';
    try {
      const r = await fetch(C.api + '/api/public/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Please check the form and try again.');
      form.reset();
      msg.textContent = body.message || 'Thank you — we will be in touch.';
      msg.classList.add('ok');
    } catch (err) {
      msg.textContent = err.message || 'Could not send just now — please try again, or email us.';
      msg.classList.add('err');
    } finally {
      btn.disabled = false;
    }
  });
})();
