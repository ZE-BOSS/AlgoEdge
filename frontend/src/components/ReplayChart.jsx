import { useEffect, useMemo, useRef } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import { RectanglePrimitive, LevelPrimitive, BubblePrimitive } from './CustomChartPrimitives';

/**
 * TradingView-style replay chart. One component, two modes.
 *
 *   live   — bars stream in from the backtest's Phase-1 loop; the window slides
 *            right to keep the newest bar in view.
 *   replay — the finished series, scrubbed with play/pause/seek by the parent,
 *            which simply passes a shorter `bars` slice.
 *
 * The parent owns all data and cursor state; this component owns the chart
 * instance and does nothing but render what it is handed. That separation is
 * deliberate — the moment a chart starts deriving trade geometry itself it
 * becomes a second source of truth that can disagree with the engine
 * (Visualization plan §2, "Visualization is read-only").
 *
 * Props
 *   bars      [{time,open,high,low,close}]  ascending, unique times
 *   signals   [{time,direction,entry,sl,tp,markings,...}]
 *   follow    boolean — pin the viewport to the right edge (live mode)
 *   onUserScroll  called when the user pans/zooms, so the parent can unpin
 *   activeSignal  a signal to focus: its levels+zones render, others dim
 *   bubbles   [{time,price,value}] order-flow prints, optional
 */
export default function ReplayChart({
  bars = [],
  signals = [],
  follow = true,
  onUserScroll,
  activeSignal = null,
  bubbles = null,
  height = 460,
  windowBars = 180,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);
  const primitivesRef = useRef([]);
  const bubbleRef = useRef(null);

  // What the chart has already been given, so an append can stay an append.
  // Re-calling setData() on every websocket batch is what makes a naive live
  // chart stutter: it is O(n) in the whole series, every 120 ms.
  const drawnRef = useRef({ count: 0, lastTime: null });

  // `follow` is read inside chart event handlers registered once at mount.
  // Mirroring it into a ref avoids tearing the chart down and rebuilding it
  // whenever the user toggles follow — which would reset their zoom. The mirror
  // is written in an effect, not during render: a render that mutates a ref is
  // impure, and the React Compiler rejects it outright.
  const followRef = useRef(follow);

  useEffect(() => { followRef.current = follow; }, [follow]);

  // ── Create the chart once ───────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8b949e',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      handleScroll: true,
      handleScale: true,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb68b',
      downColor: '#f85149',
      borderVisible: false,
      wickUpColor: '#3fb68b',
      wickDownColor: '#f85149',
      priceLineVisible: false,
    });

    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = createSeriesMarkers(series, []);

    // Any pan or zoom by the user detaches the live follow-pin, exactly like
    // TradingView.
    //
    // Detected from real input events on the container rather than from
    // subscribeVisibleLogicalRangeChange. The range-change route was tried
    // first and is wrong: setData() auto-fits the scale and fires the very same
    // event, so the chart's own first paint looked identical to a user scroll
    // and switched follow off on the opening batch of every run. Suppression
    // flags around the programmatic calls do not fix it reliably either —
    // there is no way to know how many events a given mutation will emit.
    // Wheel and pointer-drag on the container are unambiguous: only a person
    // generates them.
    const el = containerRef.current;
    const notifyUserScroll = () => {
      if (followRef.current && onUserScroll) onUserScroll();
    };
    const onWheel = () => notifyUserScroll();
    let dragging = false;
    const onPointerDown = () => { dragging = true; };
    const onPointerMove = () => { if (dragging) notifyUserScroll(); };
    const onPointerUp = () => { dragging = false; };

    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('pointerdown', onPointerDown);
    el.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);

    const resize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('pointerdown', onPointerDown);
      el.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
      primitivesRef.current = [];
      drawnRef.current = { count: 0, lastTime: null };
    };
    // Intentionally mount-only. Data changes flow through the effects below;
    // rebuilding the chart on every prop change would reset zoom and thrash.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Bars: append when growing, redraw when the series is replaced ───────
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const drawn = drawnRef.current;
    const isAppend =
      bars.length > drawn.count &&
      drawn.count > 0 &&
      bars[drawn.count - 1]?.time === drawn.lastTime;

    if (isAppend) {
      for (let i = drawn.count; i < bars.length; i++) series.update(bars[i]);
    } else {
      // Fresh series, or the parent seeked backwards. lightweight-charts
      // rejects out-of-order or duplicate timestamps outright, and a
      // downsampled series can legitimately contain repeats at bucket
      // boundaries — so dedupe rather than letting it throw.
      const clean = [];
      let last = -Infinity;
      for (const b of bars) {
        if (b.time > last) { clean.push(b); last = b.time; }
      }
      series.setData(clean);
    }

    drawnRef.current = { count: bars.length, lastTime: bars[bars.length - 1]?.time ?? null };

    if (!bars.length) return;

    // Slide the window. Pinning the logical range (rather than scrollToRealTime)
    // keeps the bar width constant as the run advances, so the chart reads as a
    // moving window over the data instead of an accordion.
    if (follow) {
      try {
        chartRef.current?.timeScale().setVisibleLogicalRange({
          from: Math.max(0, bars.length - windowBars),
          to: bars.length + 2,
        });
      } catch { /* range not yet valid on the first paint */ }
    }
  }, [bars, follow, windowBars]);

  // ── Markers: entries and exits ─────────────────────────────────────────
  const markers = useMemo(() => {
    // A dense run can produce thousands of signals; past a few hundred markers
    // the chart is unreadable anyway and the DOM/canvas cost stops being free.
    // Keeping the most RECENT is right for live mode, where the window is at
    // the right edge.
    const MAX = 300;
    const src = signals.length > MAX ? signals.slice(-MAX) : signals;
    return src.map((s) => {
      const buy = s.direction === 'BUY';
      const active = activeSignal && s.time === activeSignal.time;
      return {
        time: s.time,
        position: buy ? 'belowBar' : 'aboveBar',
        color: active ? '#d29922' : (buy ? '#3fb68b' : '#f85149'),
        shape: buy ? 'arrowUp' : 'arrowDown',
        text: `${s.direction}${s.confluence_score ? ` ${s.confluence_score}` : ''}`,
      };
    }).sort((a, b) => a.time - b.time);
  }, [signals, activeSignal]);

  useEffect(() => {
    markersRef.current?.setMarkers(markers);
  }, [markers]);

  // ── Zones and levels from the active signal's markings ─────────────────
  // Only the focused signal's geometry is drawn. Rendering every signal's
  // zones at once turns a busy run into an unreadable wall of boxes; the
  // question the chart answers is "what did THIS entry look at", one at a time.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    for (const p of primitivesRef.current) {
      try { series.detachPrimitive(p); } catch { /* already gone */ }
    }
    primitivesRef.current = [];

    const sig = activeSignal || signals[signals.length - 1];
    if (!sig) return;

    const rightEdge = bars.length ? bars[bars.length - 1].time : null;
    const attach = (p) => { series.attachPrimitive(p); primitivesRef.current.push(p); };

    for (const m of sig.markings || []) {
      const isBox = m.top != null && m.bottom != null && m.top !== m.bottom;
      const label = ROLE_PREFIX[m.role] ? `${ROLE_PREFIX[m.role]} ${m.label}` : m.label;

      if (isBox) {
        attach(new RectanglePrimitive(
          { time: m.start_time, price: m.top },
          { time: m.end_time ?? rightEdge, price: m.bottom },
          m.color || 'rgba(88,166,255,0.14)',
          { label, borderColor: roleBorder(m.role) },
        ));
      } else if (m.price != null) {
        attach(new LevelPrimitive(m.price, m.color || '#8b949e', {
          startTime: m.start_time,
          endTime: m.end_time ?? null,
          dashed: m.role === 'context',
          lineWidth: m.role === 'trigger' || m.role === 'invalidation' ? 2 : 1,
          label,
        }));
      }
    }

    // Entry / SL / TP always drawn, regardless of what the strategy marked —
    // these are the trade itself, not a confluence.
    if (sig.entry != null) {
      attach(new LevelPrimitive(sig.entry, '#58a6ff', {
        startTime: sig.time, dashed: true, lineWidth: 2, label: 'Entry',
      }));
    }
    if (sig.sl != null) {
      attach(new LevelPrimitive(sig.sl, '#f85149', { startTime: sig.time, lineWidth: 2, label: 'SL' }));
    }
    if (sig.tp != null) {
      attach(new LevelPrimitive(sig.tp, '#3fb68b', { startTime: sig.time, lineWidth: 2, label: 'TP' }));
    }
  }, [activeSignal, signals, bars]);

  // ── Order-flow bubbles (opt-in) ────────────────────────────────────────
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    if (!bubbles || !bubbles.length) {
      if (bubbleRef.current) {
        try { series.detachPrimitive(bubbleRef.current); } catch { /* already gone */ }
        bubbleRef.current = null;
      }
      return;
    }
    if (!bubbleRef.current) {
      bubbleRef.current = new BubblePrimitive(bubbles);
      series.attachPrimitive(bubbleRef.current);
    } else {
      bubbleRef.current.setPoints(bubbles);
    }
  }, [bubbles]);

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height,
        borderRadius: 'var(--radius-sm)',
        overflow: 'hidden',
        border: '1px solid var(--border)',
        background: 'var(--bg-secondary)',
      }}
    />
  );
}

const ROLE_PREFIX = {
  trigger: '▶',
  confluence: '✓',
  invalidation: '✕',
  context: '·',
};

function roleBorder(role) {
  if (role === 'trigger') return 'rgba(210,153,34,0.55)';
  if (role === 'invalidation') return 'rgba(248,81,73,0.45)';
  return null;
}
