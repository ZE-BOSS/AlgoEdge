import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import { RectanglePrimitive } from './CustomChartPrimitives';

/**
 * One trade's candles with its entry, stop, targets and the strategy's zones.
 *
 * `candles` is the one timeframe to draw, already trimmed around the trade by
 * the server (/backtest_result/trade/{id}/chart?tf=…). Older callers that pass
 * only `group` still work: the matching chart_data_* array is used instead.
 *
 * The chart instance is created once per mount and fed with setData, and
 * markers are snapped with a binary search. The previous version rebuilt the
 * whole chart whenever the group object changed identity and snapped every
 * marker with a scan over every candle.
 */
const THEME = {
  text: '#8a94a6',
  grid: 'rgba(148, 163, 184, 0.06)',
  border: '#1e2a3a',
  up: '#26a69a',
  down: '#ef5350',
  entry: '#5b9cf6',
  target: '#26a69a',
  stop: '#ef5350',
};

function pickCandles(group, timeframe) {
  if (!group) return [];
  if (timeframe === 'M15') return group.chart_data_m15 || [];
  if (timeframe === 'H1') return group.chart_data_h1 || [];
  if (timeframe === 'M5') return (group.chart_data_m5?.length ? group.chart_data_m5 : group.chart_data) || [];
  return group.chart_data || [];
}

function toEpoch(v) {
  if (v == null) return null;
  if (typeof v === 'number') return v;
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}

// Nearest candle time within `maxGap` seconds, by binary search over ascending times.
function snap(times, t, maxGap = 900) {
  if (t == null || !times.length) return null;
  let lo = 0;
  let hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] < t) lo = mid + 1; else hi = mid;
  }
  const cands = [times[lo], lo > 0 ? times[lo - 1] : null].filter(x => x != null);
  let best = null;
  for (const c of cands) if (best == null || Math.abs(c - t) < Math.abs(best - t)) best = c;
  return best != null && Math.abs(best - t) <= maxGap ? best : null;
}

export default function TradeChart({ group, candles, timeframe = 'M5', height = 320 }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);
  const decorRef = useRef({ lines: [], primitives: [] });

  // ── Create once ───────────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      height,
      layout: {
        background: { color: 'transparent' },
        textColor: THEME.text,
        fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: THEME.grid }, horzLines: { color: THEME.grid } },
      rightPriceScale: { borderColor: THEME.border },
      timeScale: { borderColor: THEME.border, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: THEME.up, downColor: THEME.down, borderVisible: false,
      wickUpColor: THEME.up, wickDownColor: THEME.down, priceLineVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = createSeriesMarkers(series, []);

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
      decorRef.current = { lines: [], primitives: [] };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Data and decorations ──────────────────────────────────────────────
  const data = candles || pickCandles(group, timeframe);
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    for (const l of decorRef.current.lines) { try { series.removePriceLine(l); } catch { /* gone */ } }
    for (const p of decorRef.current.primitives) { try { series.detachPrimitive(p); } catch { /* gone */ } }
    decorRef.current = { lines: [], primitives: [] };

    const clean = [];
    let last = -Infinity;
    for (const d of data || []) {
      if (d && d.time > last) { clean.push(d); last = d.time; }
    }
    series.setData(clean);
    if (!clean.length) { markersRef.current?.setMarkers([]); return; }
    const times = clean.map(d => d.time);
    const rightEdge = times[times.length - 1];

    const addLine = (price, color, title, lineStyle = 0) => {
      if (price == null || !Number.isFinite(+price)) return;
      decorRef.current.lines.push(series.createPriceLine({
        price: +price, color, lineWidth: 1, lineStyle, axisLabelVisible: true, title,
      }));
    };
    const legs = group?.sub_trades || [];
    const first = legs[0] || group || {};
    addLine(first.entry_price ?? group?.entry_price, THEME.entry, 'Entry', 2);
    addLine(first.stop_loss ?? group?.stop_loss, THEME.stop, 'SL');
    legs.forEach(sub => addLine(sub.take_profit, THEME.target, `TP${sub.tp_level || ''}`));

    const smc = group?.smc_data || {};
    const attach = (p) => { series.attachPrimitive(p); decorRef.current.primitives.push(p); };
    (smc.boxes || [])
      .filter(b => b.timeframe === timeframe || (timeframe === 'M5' && !b.timeframe))
      .forEach(b => attach(new RectanglePrimitive(
        { time: b.start_time, price: b.top }, { time: b.end_time || rightEdge, price: b.bottom },
        b.color || 'rgba(148, 163, 184, 0.14)')));
    (smc.fib_zones || [])
      .filter(z => z.timeframe === timeframe || !z.timeframe)
      .forEach(z => attach(new RectanglePrimitive(
        { time: z.start_time, price: z.top }, { time: z.end_time || rightEdge, price: z.bottom },
        'rgba(201, 162, 39, 0.14)')));

    const markers = [];
    (smc.markers || [])
      .filter(m => m.timeframe === timeframe || (timeframe === 'M5' && !m.timeframe))
      .forEach(m => {
        const t = snap(times, toEpoch(m.time));
        if (t != null) markers.push({ time: t, position: m.text === 'BOS' ? 'aboveBar' : 'belowBar', color: '#64748b', shape: 'circle', text: m.text });
      });
    const buy = String(group?.direction || '').toUpperCase().startsWith('B');
    const entryT = snap(times, toEpoch(group?.entry_time_iso || group?.entry_time));
    if (entryT != null) {
      markers.push({ time: entryT, position: buy ? 'belowBar' : 'aboveBar', color: THEME.entry, shape: buy ? 'arrowUp' : 'arrowDown', text: 'Entry' });
    }
    const exitT = snap(times, toEpoch(group?.exit_time_iso || group?.exit_time));
    if (exitT != null) {
      const win = (group?.combined_pnl ?? group?.pnl ?? 0) > 0;
      markers.push({ time: exitT, position: buy ? 'aboveBar' : 'belowBar', color: win ? THEME.up : THEME.down, shape: buy ? 'arrowDown' : 'arrowUp', text: win ? 'Exit ▲' : 'Exit ▼' });
    }
    markers.sort((a, b) => a.time - b.time);
    markersRef.current?.setMarkers(markers);
    chart.timeScale().fitContent();
  }, [data, group, timeframe]);

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%', height, borderRadius: 'var(--radius-xs)', overflow: 'hidden',
        border: '1px solid var(--border)', background: 'var(--bg-secondary)',
      }}
    />
  );
}
