/**
 * FundamentalsChart.jsx
 *
 * [Phase 14 E.1] Chart view for each Fundamentals panel.
 *
 * Renders the API response from the fundamentals backend as a visual chart
 * rather than a table. Panel-specific renderers live as inner components;
 * the outer switch dispatches by `panel` prop.
 *
 * No extra npm dependencies — uses a thin SVG layer (inline) and the browser's
 * Canvas API via a React `useEffect` hook. That keeps the bundle identical to
 * before and avoids adding a charting library to a page that already loads
 * lightweight-charts for the replay view.
 */

import { useEffect, useRef } from 'react';

/* ── Palette ─────────────────────────────────────────────────────────────── */
const GREEN  = '#10b981';
const RED    = '#ef4444';
const AMBER  = '#f59e0b';
const BLUE   = '#3b82f6';
const MUTED  = 'rgba(148,163,184,0.5)';
const BG     = 'rgba(255,255,255,0.03)';
const BORDER = 'rgba(255,255,255,0.08)';

/* ── Shared canvas helper ─────────────────────────────────────────────────── */
function useCanvas(draw, deps) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const ratio = window.devicePixelRatio || 1;
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;
    canvas.width  = w * ratio;
    canvas.height = h * ratio;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, w, h);
    if (w > 0 && h > 0) draw(ctx, w, h);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

/* ── Order Flow chart ─────────────────────────────────────────────────────── */
function OrderFlowChart({ data: d }) {
  if (!d?.bubbles?.length) return <NoData label="No signed-volume data" />;

  const bubbles = d.bubbles.slice(0, 80);
  const maxVol = Math.max(1, ...bubbles.map(b => b.abs_volume));
  const prices = bubbles.map(b => parseFloat(b.price));
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);

  const canvasRef = useCanvas((ctx, w, h) => {
    const PAD_L = 68, PAD_R = 24, PAD_T = 12, PAD_B = 20;
    const chartW = w - PAD_L - PAD_R;
    const chartH = h - PAD_T - PAD_B;
    const priceRange = maxP - minP || 1;

    ctx.fillStyle = BG;
    ctx.fillRect(PAD_L, PAD_T, chartW, chartH);

    ctx.fillStyle = 'rgba(148,163,184,0.7)';
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    for (let tick = 0; tick <= 4; tick++) {
      const p = minP + (priceRange * tick) / 4;
      const y = PAD_T + chartH - (((p - minP) / priceRange) * chartH);
      ctx.fillText(p.toFixed(5), PAD_L - 4, y + 3);
      ctx.strokeStyle = BORDER;
      ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + chartW, y); ctx.stroke();
    }
    ctx.setLineDash([]);

    bubbles.forEach(b => {
      const isBuy = b.side === 'buy';
      const y = PAD_T + chartH - (((parseFloat(b.price) - minP) / priceRange) * chartH);
      const r = Math.max(4, (b.abs_volume / maxVol) * 28);
      ctx.beginPath();
      ctx.arc(PAD_L + (isBuy ? chartW * 0.55 : chartW * 0.45), y, r, 0, Math.PI * 2);
      ctx.fillStyle = isBuy ? 'rgba(16,185,129,0.45)' : 'rgba(239,68,68,0.45)';
      ctx.fill();
      ctx.strokeStyle = isBuy ? GREEN : RED;
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    if (d.cvd_series?.length > 1) {
      const cvd = d.cvd_series;
      const minC = Math.min(...cvd.map(p => p.value));
      const maxC = Math.max(...cvd.map(p => p.value));
      const cRange = maxC - minC || 1;
      ctx.beginPath();
      ctx.strokeStyle = BLUE;
      ctx.lineWidth = 1.5;
      cvd.forEach((pt, i) => {
        const cx = PAD_L + (i / (cvd.length - 1)) * chartW;
        const cy = PAD_T + chartH - (((pt.value - minC) / cRange) * chartH);
        i === 0 ? ctx.moveTo(cx, cy) : ctx.lineTo(cx, cy);
      });
      ctx.stroke();
    }

    if (d.volume_profile?.vpoc != null) {
      const y = PAD_T + chartH - (((d.volume_profile.vpoc - minP) / priceRange) * chartH);
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = AMBER;
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + chartW, y); ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [d]);

  return (
    <div style={{ position: 'relative', height: 320 }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 10, fontSize: '0.65rem' }}>
        <span style={{ color: GREEN }}>● Buy</span>
        <span style={{ color: RED }}>● Sell</span>
        <span style={{ color: BLUE }}>— CVD</span>
        <span style={{ color: AMBER }}>- VPOC</span>
      </div>
    </div>
  );
}

/* ── Order Book (depth) chart ─────────────────────────────────────────────── */
function OrderBookChart({ data: d }) {
  if (!d?.bids?.length && !d?.asks?.length) return <NoData label="Empty order book" />;

  const allLevels = [
    ...(d.bids || []).map(l => ({ ...l, side: 'bid' })),
    ...(d.asks || []).map(l => ({ ...l, side: 'ask' })),
  ].sort((a, b) => b.price - a.price);

  const maxVol = Math.max(1, ...allLevels.map(l => l.volume));

  const canvasRef = useCanvas((ctx, w, h) => {
    const barH = Math.max(4, Math.floor(h / (allLevels.length + 1)));
    const PAD_L = 70, PAD_R = 20;
    const BAR_MAX_W = w - PAD_L - PAD_R;
    const visible = allLevels.slice(0, Math.floor(h / barH));

    visible.forEach((l, i) => {
      const y = i * barH;
      const barW = (l.volume / maxVol) * BAR_MAX_W;
      ctx.fillStyle = l.side === 'bid' ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)';
      ctx.fillRect(PAD_L, y + 1, barW, barH - 2);
      ctx.fillStyle = 'rgba(148,163,184,0.7)';
      ctx.font = '9px monospace';
      ctx.textAlign = 'right';
      ctx.fillText(parseFloat(l.price).toFixed(5), PAD_L - 3, y + barH - 3);
    });
  }, [d]);

  return (
    <div style={{ height: 320, position: 'relative' }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 10, fontSize: '0.65rem' }}>
        <span style={{ color: GREEN }}>■ Bids</span>
        <span style={{ color: RED }}>■ Asks</span>
      </div>
    </div>
  );
}

/* ── Correlation heatmap ─────────────────────────────────────────────────── */
function CorrelationChart({ data: d }) {
  if (!d?.matrix || !d?.symbols?.length) return <NoData label="No correlation matrix" />;
  const syms = d.symbols;
  const n = syms.length;
  const cell = Math.min(70, Math.floor(300 / n));
  const PAD = 44;
  const size = PAD + n * cell + 4;

  const canvasRef = useCanvas((ctx, w, h) => {
    ctx.font = '10px sans-serif';
    ctx.fillStyle = 'rgba(148,163,184,0.9)';
    syms.forEach((s, i) => {
      ctx.textAlign = 'right';
      ctx.fillText(s, PAD - 4, PAD + i * cell + cell / 2 + 4);
      ctx.save();
      ctx.translate(PAD + i * cell + cell / 2, PAD - 4);
      ctx.rotate(-Math.PI / 4);
      ctx.textAlign = 'right';
      ctx.fillText(s, 0, 0);
      ctx.restore();
    });
    syms.forEach((a, ai) => {
      syms.forEach((b, bi) => {
        const v = d.matrix[a]?.[b] ?? 0;
        const alpha = Math.min(0.65, Math.abs(v) * 0.65);
        const x = PAD + bi * cell, y = PAD + ai * cell;
        ctx.fillStyle = a === b ? 'rgba(255,255,255,0.05)'
          : `rgba(${v > 0 ? '16,185,129' : '239,68,68'},${alpha})`;
        ctx.fillRect(x, y, cell - 1, cell - 1);
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.textAlign = 'center';
        ctx.font = '10px monospace';
        ctx.fillText(a === b ? '1.00' : v.toFixed(2), x + cell / 2, y + cell / 2 + 4);
      });
    });
  }, [d]);

  return (
    <div style={{ overflowX: 'auto' }}>
      <canvas ref={canvasRef} style={{ width: `${size}px`, height: `${size}px`, display: 'block', margin: '0 auto' }} />
    </div>
  );
}

/* ── GEX bar chart ───────────────────────────────────────────────────────── */
function GexChart({ data: d }) {
  if (!d?.by_strike?.length) return <NoData label="No GEX data" />;

  const strikes = d.by_strike
    .filter(r => !d.spot || Math.abs(r.strike - d.spot) / d.spot < 0.12)
    .sort((a, b) => a.strike - b.strike)
    .slice(0, 60);

  const maxAbs = Math.max(1, ...strikes.map(r => Math.abs(r.gex)));

  const canvasRef = useCanvas((ctx, w, h) => {
    const PAD_L = 60, PAD_R = 20, PAD_T = 20, PAD_B = 30;
    const barW = (w - PAD_L - PAD_R) / strikes.length;
    const chartH = h - PAD_T - PAD_B;
    const midY = PAD_T + chartH / 2;

    ctx.strokeStyle = BORDER;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD_L, midY); ctx.lineTo(w - PAD_R, midY); ctx.stroke();

    if (d.flip_strike != null) {
      const flipIdx = strikes.findIndex(r => r.strike >= d.flip_strike);
      if (flipIdx >= 0) {
        const x = PAD_L + flipIdx * barW + barW / 2;
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = AMBER;
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + chartH); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = AMBER;
        ctx.font = '9px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('flip', x, PAD_T - 4);
      }
    }

    strikes.forEach((r, i) => {
      const gex = r.gex;
      const x = PAD_L + i * barW;
      const bh = Math.abs(gex / maxAbs) * (chartH / 2 - 2);
      ctx.fillStyle = gex > 0 ? 'rgba(16,185,129,0.65)' : 'rgba(239,68,68,0.65)';
      ctx.fillRect(x + 1, gex > 0 ? midY - bh : midY, barW - 2, bh);
    });

    ctx.fillStyle = 'rgba(148,163,184,0.7)';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';
    strikes.forEach((r, i) => {
      if (i % 5 === 0) ctx.fillText(r.strike, PAD_L + i * barW + barW / 2, h - PAD_B + 10);
    });
  }, [d]);

  return (
    <div style={{ height: 280, position: 'relative' }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 10, fontSize: '0.65rem' }}>
        <span style={{ color: GREEN }}>■ +GEX (pin)</span>
        <span style={{ color: RED }}>■ −GEX (accel)</span>
        <span style={{ color: AMBER }}>- Flip</span>
      </div>
    </div>
  );
}

/* ── Calendar timeline ───────────────────────────────────────────────────── */
function CalendarChart({ data: d }) {
  if (!d?.events?.length) return <NoData label="No calendar events" />;

  const now = Date.now();
  const events = d.events.slice(0, 40).map(e => ({
    ...e, ts: new Date(e.date).getTime(),
  })).filter(e => !isNaN(e.ts)).sort((a, b) => a.ts - b.ts);

  if (!events.length) return <NoData label="No parseable event dates" />;
  const minTs = events[0].ts;
  const maxTs = events[events.length - 1].ts;
  const range = maxTs - minTs || 86400000;

  const canvasRef = useCanvas((ctx, w, h) => {
    const PAD_L = 16, PAD_R = 16, PAD_T = 30, PAD_B = 24;
    const timelineY = (h - PAD_T - PAD_B) / 2 + PAD_T;
    const chartW = w - PAD_L - PAD_R;
    const impColor = imp => imp === 'High' ? RED : imp === 'Medium' ? AMBER : MUTED;

    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(PAD_L, timelineY); ctx.lineTo(PAD_L + chartW, timelineY); ctx.stroke();

    const nowX = PAD_L + ((now - minTs) / range) * chartW;
    if (nowX >= PAD_L && nowX <= PAD_L + chartW) {
      ctx.strokeStyle = BLUE; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(nowX, PAD_T); ctx.lineTo(nowX, h - PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = BLUE; ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('now', nowX, PAD_T - 6);
    }

    events.forEach((e, i) => {
      const x = PAD_L + ((e.ts - minTs) / range) * chartW;
      const color = impColor(e.impact);
      const isAbove = i % 2 === 0;
      const stemEnd = isAbove ? timelineY - 30 : timelineY + 30;

      ctx.strokeStyle = color; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, timelineY); ctx.lineTo(x, stemEnd); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, timelineY, 4, 0, Math.PI * 2); ctx.fill();

      ctx.fillStyle = 'rgba(255,255,255,0.75)';
      ctx.font = '8px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(`${e.country} ${(e.title || '').slice(0, 14)}`, x, isAbove ? stemEnd - 5 : stemEnd + 12);
    });
  }, [d]);

  return (
    <div style={{ height: 200 }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      <div style={{ display: 'flex', gap: 10, fontSize: '0.65rem', marginTop: 4, justifyContent: 'center' }}>
        <span style={{ color: RED }}>● High</span>
        <span style={{ color: AMBER }}>● Medium</span>
        <span style={{ color: MUTED }}>● Low</span>
        <span style={{ color: BLUE }}>— Now</span>
      </div>
    </div>
  );
}

/* ── No-data placeholder ─────────────────────────────────────────────────── */
function NoData({ label }) {
  return (
    <div style={{
      height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: 'var(--text-muted)', fontSize: '0.8rem',
      border: `1px dashed ${BORDER}`, borderRadius: 6,
    }}>
      {label}
    </div>
  );
}

/* ── Public component ────────────────────────────────────────────────────── */
export default function FundamentalsChart({ panel, result }) {
  const d = result?.data;
  if (!d) return <NoData label="No data yet — click Refresh" />;

  switch (panel) {
    case 'orderflow':   return <OrderFlowChart   data={d} />;
    case 'orderbook':   return <OrderBookChart   data={d} />;
    case 'correlation': return <CorrelationChart data={d} />;
    case 'gex':         return <GexChart         data={d} />;
    case 'calendar':    return <CalendarChart    data={d} />;
    default:            return <NoData label={`No chart view for "${panel}"`} />;
  }
}
