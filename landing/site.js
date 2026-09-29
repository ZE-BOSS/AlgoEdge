// Alphavantiq Capital public site. No framework, no build: it reads three
// public endpoints and posts one form. Every chart is SVG drawn with
// attributes, because the site's security policy blocks inline styles.
(function () {
  'use strict';
  const C = window.AVQ || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const SVG = 'http://www.w3.org/2000/svg';

  // numbers arrive as decimal strings; format them as strings
  const money = (v) => {
    const [i, f = ''] = String(v).split('.');
    return '$' + i.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (f && f !== '00' ? '.' + (f + '0').slice(0, 2) : '');
  };
  const dollars = (n, dp = 0) => '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const pct = (v, sign = true) => {
    const n = Number(v);
    return (sign && n > 0 ? '+' : '') + n.toFixed(2) + '%';
  };
  const fmtDate = (iso, opts = { month: 'short', year: 'numeric' }) =>
    new Date(iso + 'T12:00:00Z').toLocaleDateString('en-US', opts);
  const get = (path) => fetch(C.api + path).then((r) => (r.ok ? r.json() : Promise.reject(r)));

  $$('[data-app-link]').forEach((a) => { a.href = C.app; });
  const signup = $('#signup-link');
  if (signup) signup.href = C.app + '/signup';
  $('#year').textContent = new Date().getFullYear();
  const contact = $('#contact');
  if (C.email) contact.href = 'mailto:' + C.email; else contact.hidden = true;
  if (C.regulatory) { const r = $('#regulatory'); r.textContent = C.regulatory; r.hidden = false; }

  // terms: the page ships with the agreed figures; the API confirms them
  get('/api/public/terms').then((t) => {
    const set = (k, v) => $$(`[data-term="${k}"]`).forEach((el) => { el.textContent = v; });
    set('min', money(t.min_investment));
    set('mgmt', `${t.management_fee_pct}% of profit, every two months`);
    set('mgmt-short', `${t.management_fee_pct}% of profit`);
    set('perf', `${t.performance_fee_pct}%`);
    set('lockup', t.lockup_days % 30 === 0 && t.lockup_days ? `${t.lockup_days / 30} month${t.lockup_days > 30 ? 's' : ''}` : `${t.lockup_days} days`);
    set('cap', `${t.withdrawal_cap_pct}% of monthly profit`);
    set('notice', t.notice_days % 7 === 0 && t.notice_days ? `${t.notice_days / 7} week${t.notice_days > 7 ? 's' : ''}` : `${t.notice_days} days`);
  }).catch(() => {});

  // small SVG helper: el('path', { d: ..., class: ... })
  function el(name, attrs = {}, text) {
    const n = document.createElementNS(SVG, name);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function svgBox(host, W, H, label) {
    host.textContent = '';
    const s = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label });
    host.appendChild(s);
    return s;
  }

  // performance
  get('/api/public/performance').then((p) => {
    if (!p.enough_history) {
      $('#perf-asof').textContent = p.since
        ? `Our live track record began on ${fmtDate(p.since, { day: 'numeric', month: 'long', year: 'numeric' })}. Figures appear once there is more than one valuation.`
        : 'Our live track record will appear here once trading begins.';
      $('#perf').hidden = true;
      return;
    }
    $('#perf-asof').textContent = `Updated ${fmtDate(p.as_of, { day: 'numeric', month: 'short', year: 'numeric' })}`;
    const ret = $('[data-perf="return"]');
    ret.textContent = pct(p.return_pct);
    ret.classList.add(Number(p.return_pct) >= 0 ? 'good' : 'bad');
    $('[data-perf="dd"]').textContent = pct(p.max_drawdown_pct, false);
    const full = (p.months || []).filter((m) => !m.partial);
    const pool = full.length ? full : (p.months || []);
    if (pool.length) {
      const best = pool.reduce((a, b) => (Number(b.return_pct) > Number(a.return_pct) ? b : a));
      const worst = pool.reduce((a, b) => (Number(b.return_pct) < Number(a.return_pct) ? b : a));
      $('[data-perf="best"]').textContent = pct(best.return_pct);
      $('[data-perf="worst"]').textContent = pct(worst.return_pct);
    }
    const months = Math.max(1, Math.round(p.days / 30.4));
    $('[data-perf="since"]').textContent = String(months);
    if (p.note) $('#perf-note').textContent = p.note;

    const draw = () => {
      drawGrowth($('#growth-chart'), p.series);
      drawMonths($('#months'), p.months);
      drawDrawdown($('#dd-chart'), p.series);
    };
    draw();
    let w = window.innerWidth;
    window.addEventListener('resize', () => {
      if (Math.abs(window.innerWidth - w) < 40) return;
      w = window.innerWidth;
      draw();
    });
  }).catch(() => {
    $('#perf-asof').textContent = 'The live figures could not be loaded just now.';
    $('#perf').hidden = true;
  });

  // Growth of $10,000 at launch: the fund opens at 100 a unit, so value = price x 100
  function drawGrowth(host, pts) {
    if (!pts || pts.length < 2) { host.innerHTML = '<p class="empty">Not enough history yet.</p>'; return; }
    const W = Math.max(280, Math.round(host.clientWidth || 720)), H = W < 500 ? 200 : 260;
    const P = { t: 14, r: 12, b: 26, l: 70 };
    const start = Number(pts[0].nav);
    const ys = pts.map((q) => (Number(q.nav) / start) * 10000);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    const pad = (hi - lo || 100) * 0.1; lo -= pad; hi += pad;
    const x = (i) => P.l + (i / (pts.length - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const s = svgBox(host, W, H, `Growth of 10,000 dollars from ${fmtDate(pts[0].date)} to ${fmtDate(pts.at(-1).date)}, now ${dollars(ys.at(-1))}`);
    [0, 0.25, 0.5, 0.75, 1].map((f) => lo + pad + f * (hi - lo - 2 * pad)).forEach((t) => {
      s.appendChild(el('line', { class: 'grid', x1: P.l, x2: W - P.r, y1: y(t), y2: y(t) }));
      s.appendChild(el('text', { class: 'axis', x: P.l - 8, y: y(t), dy: '0.32em', 'text-anchor': 'end' }, dollars(t)));
    });
    s.appendChild(el('text', { class: 'axis', x: P.l, y: H - 6 }, fmtDate(pts[0].date)));
    s.appendChild(el('text', { class: 'axis', x: W - P.r, y: H - 6, 'text-anchor': 'end' }, fmtDate(pts.at(-1).date)));
    const line = ys.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
    s.appendChild(el('path', { class: 'area', d: `${line}L${x(ys.length - 1).toFixed(1)},${H - P.b}L${P.l},${H - P.b}Z` }));
    s.appendChild(el('path', { class: 'line', d: line }));
    s.appendChild(el('circle', { class: 'dot', r: 4, cx: x(ys.length - 1), cy: y(ys.at(-1)) }));
    hover(host, s, W, P, ys.length, x, (i) => y(ys[i]),
      (i) => `<b>${dollars(ys[i])}</b>${fmtDate(pts[i].date, { day: 'numeric', month: 'short', year: 'numeric' })}`);
  }

  // How far below the previous high the fund was on each day: 0 at a new high
  function drawDrawdown(host, pts) {
    if (!pts || pts.length < 2) { host.innerHTML = '<p class="empty">Not enough history yet.</p>'; return; }
    const W = Math.max(280, Math.round(host.clientWidth || 520)), H = 180;
    const P = { t: 12, r: 12, b: 26, l: 52 };
    let peak = -Infinity;
    const dd = pts.map((q) => { const n = Number(q.nav); peak = Math.max(peak, n); return (n / peak - 1) * 100; });
    const lo = Math.min(-1, ...dd) * 1.1;
    const x = (i) => P.l + (i / (pts.length - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (v / lo) * (H - P.t - P.b);
    const s = svgBox(host, W, H, `Largest fall from a previous high was ${Math.min(...dd).toFixed(2)} percent`);
    [0, lo / 2, lo].forEach((t) => {
      s.appendChild(el('line', { class: t === 0 ? 'zero' : 'grid', x1: P.l, x2: W - P.r, y1: y(t), y2: y(t) }));
      s.appendChild(el('text', { class: 'axis', x: P.l - 8, y: y(t), dy: '0.32em', 'text-anchor': 'end' }, `${t.toFixed(1)}%`));
    });
    s.appendChild(el('text', { class: 'axis', x: P.l, y: H - 6 }, fmtDate(pts[0].date)));
    s.appendChild(el('text', { class: 'axis', x: W - P.r, y: H - 6, 'text-anchor': 'end' }, fmtDate(pts.at(-1).date)));
    const line = dd.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
    s.appendChild(el('path', { class: 'dd-area', d: `${line}L${x(dd.length - 1).toFixed(1)},${y(0)}L${P.l},${y(0)}Z` }));
    hover(host, s, W, P, dd.length, x, (i) => y(dd[i]),
      (i) => `<b>${dd[i].toFixed(2)}%</b>${fmtDate(pts[i].date, { day: 'numeric', month: 'short', year: 'numeric' })}`);
  }

  // Monthly returns as columns: green above zero, red below, each one labelled
  function drawMonths(host, months) {
    if (!months || !months.length) { host.innerHTML = '<p class="empty">The first full month is still in progress.</p>'; return; }
    const list = months.slice(-12);
    const W = Math.max(280, Math.round(host.clientWidth || 520)), H = 180;
    const P = { t: 18, r: 8, b: 26, l: 8 };
    const vals = list.map((m) => Number(m.return_pct));
    const max = Math.max(0.5, ...vals.map(Math.abs));
    const mid = P.t + (H - P.t - P.b) / 2;
    const half = (H - P.t - P.b) / 2 - 12;
    const bw = (W - P.l - P.r) / list.length;
    const s = svgBox(host, W, H, 'Monthly returns: ' + list.map((m, i) => `${m.month} ${vals[i].toFixed(2)} percent`).join(', '));
    s.appendChild(el('line', { class: 'zero', x1: P.l, x2: W - P.r, y1: mid, y2: mid }));
    list.forEach((m, i) => {
      const v = vals[i], h = Math.max(1, (Math.abs(v) / max) * half);
      const cx = P.l + i * bw + bw / 2, w = Math.min(28, bw * 0.6);
      s.appendChild(el('rect', { class: v < 0 ? 'bar-neg' : 'bar-pos', x: cx - w / 2, width: w,
        y: v < 0 ? mid : mid - h, height: h, rx: 2 }));
      if (bw > 30) {
        s.appendChild(el('text', { class: 'axis', x: cx, 'text-anchor': 'middle', y: v < 0 ? mid + h + 12 : mid - h - 4 },
          `${v > 0 ? '+' : ''}${v.toFixed(1)}%`));
      }
      const label = new Date(m.month + '-15T12:00:00Z').toLocaleDateString('en-US', { month: 'short' });
      s.appendChild(el('text', { class: 'axis', x: cx, y: H - 6, 'text-anchor': 'middle' }, label + (m.partial ? '*' : '')));
    });
    if (list.some((m) => m.partial)) host.insertAdjacentHTML('beforeend', '<p class="fine">* month in progress</p>');
  }

  // crosshair and tooltip for a single-series chart
  function hover(host, s, W, P, n, x, yAt, tipHtml) {
    const cross = el('line', { class: 'cross', y1: P.t, y2: s.viewBox.baseVal.height - P.b, visibility: 'hidden' });
    const dot = el('circle', { class: 'dot', r: 5, visibility: 'hidden' });
    s.appendChild(cross); s.appendChild(dot);
    const tip = document.createElement('div');
    tip.className = 'tip'; tip.hidden = true; host.appendChild(tip);
    s.addEventListener('pointermove', (ev) => {
      const r = s.getBoundingClientRect();
      const sx = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.min(n - 1, Math.max(0, Math.round(((sx - P.l) / (W - P.l - P.r)) * (n - 1))));
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', x(i)); dot.setAttribute('cy', yAt(i)); dot.setAttribute('visibility', 'visible');
      tip.hidden = false;
      tip.innerHTML = tipHtml(i);
      tip.style.left = Math.min(Math.max((x(i) / W) * r.width, 60), r.width - 60) + 'px';
    });
    s.addEventListener('pointerleave', () => {
      tip.hidden = true; cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden');
    });
  }

  // Android download (release manager in the admin console)
  get('/api/public/app').then((a) => {
    if (!a || !a.url) return;
    const link = $('#apk-link');
    link.href = a.url; link.hidden = false;
    $('#apk-version').textContent = `(v${a.version})`;
    $('#apk-missing').hidden = true;
  }).catch(() => {});

  // contact form
  const form = $('#apply-form'), msg = $('#apply-msg');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    msg.className = 'form-msg';
    if (!form.checkValidity()) {
      msg.textContent = 'Please fill in your name and email, and tick the box.';
      msg.classList.add('err');
      form.reportValidity();
      return;
    }
    const data = Object.fromEntries(new FormData(form).entries());
    data.consent = form.consent.checked;
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true; msg.textContent = 'Sending...';
    try {
      const r = await fetch(C.api + '/api/public/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Please check the form and try again.');
      form.reset();
      msg.textContent = body.message || 'Thank you. We will be in touch.';
      msg.classList.add('ok');
    } catch (err) {
      msg.textContent = err.message || 'Could not send just now. Please try again, or email us.';
      msg.classList.add('err');
    } finally {
      btn.disabled = false;
    }
  });
})();
