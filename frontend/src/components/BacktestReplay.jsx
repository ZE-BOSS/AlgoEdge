import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getReplaySeries, getSavedReplaySeries } from '../services/api';
import {
  Play, Pause, SkipBack, SkipForward, Crosshair, Radio, Activity, Layers,
} from 'lucide-react';
import ReplayChart from './ReplayChart';

/**
 * The backtest replay surface. Replaces the skeleton loader that used to sit
 * here while a run was in flight.
 *
 * Live mode — bars arrive over the WebSocket from the engine's Phase-1 bar walk
 * and the window slides right. For a portfolio run there is one tab per LEG
 * (slot), not per symbol, and the active tab follows the leg being simulated
 * until the user picks one manually.
 *
 * Replay mode — once the run finishes, the same chart becomes scrubable over the
 * whole window with play/pause/speed/seek.
 *
 * Honest note on what "live" means here: the backtest computes signals in one
 * pass and simulates trades in a second pass afterwards, so during the run this
 * shows bars and SIGNALS as the strategy fires them — not filled trades, which
 * do not exist yet. Trade-level detail becomes available when the run completes.
 *
 * Ingest design: websocket messages land in a mutable pending buffer (a ref),
 * and a requestAnimationFrame tick folds that buffer into React state as NEW
 * arrays. Two reasons for the indirection rather than setState per message:
 * a fast run emits far more messages than frames, so batching collapses them
 * into one paint; and appending in place would leave the array identity
 * unchanged, so nothing downstream would re-render at all.
 */

const SPEEDS = [1, 2, 4, 8, 16];
const BARS_PER_TICK = 3;          // replay advance per frame at 1x
const REPLAY_FRAME_MS = 40;       // ~25 fps — smooth without burning CPU
const FLUSH_FALLBACK_MS = 250;    // drains the buffer when rAF is suspended
const EMPTY_LEG = { bars: [], signals: [], total: 0, done: false };

// Bars folded into state per frame during a LIVE run.
//
// The engine sends ~400 bars per message. Applying a whole message in one frame
// makes the window jump 400 bars at a time — the chart teleports instead of
// sliding. Draining at a fixed rate turns the same data into motion: 24 bars a
// frame at ~60fps is ~1,400 bars/second, fast enough to keep up with the engine
// on a long run and slow enough to read.
//
// The cap is a floor, not a ceiling: if the buffer runs far ahead (a fast engine,
// or a backgrounded tab that queued thousands), the drain scales up so the chart
// still catches up rather than falling permanently behind.
const LIVE_BARS_PER_FRAME = 24;
const LIVE_CATCHUP_RATIO = 0.12;  // drain at least this fraction of a big backlog

export default function BacktestReplay({ progress, result, isRunning }) {
  const [legs, setLegs] = useState([]);
  const [legData, setLegData] = useState({});   // slot_id -> { bars, signals, total, done }
  const [activeSlot, setActiveSlot] = useState(null);
  const [pinned, setPinned] = useState(false);
  const [follow, setFollow] = useState(true);
  const [activeSignal, setActiveSignal] = useState(null);

  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);

  // Between-frame buffer. Only ever touched inside event handlers and effects.
  const pendingRef = useRef({});   // slot_id -> { bars: [], signals: [] }
  const rafRef = useRef(null);
  const fallbackRef = useRef(null);
  // `pinned` is read by the websocket handler, which is registered once; a ref
  // keeps the handler stable instead of re-subscribing on every toggle.
  const pinnedRef = useRef(false);
  // The frame drain re-arms itself while a backlog remains. A ref breaks the
  // definition cycle (scheduleFlush -> flush -> scheduleFlush) without making
  // the callback depend on itself.
  const scheduleFlushRef = useRef(null);

  useEffect(() => { pinnedRef.current = pinned; }, [pinned]);

  // ── Fold the pending buffer into state, at most once per frame ─────────
  const scheduleFlush = useCallback(() => {
    if (rafRef.current != null || fallbackRef.current != null) return;

    const flush = () => {
      if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
      if (fallbackRef.current != null) { clearTimeout(fallbackRef.current); fallbackRef.current = null; }

      const pending = pendingRef.current;
      if (!Object.keys(pending).length) return;

      // Take only a slice of the buffered bars this frame and leave the rest
      // for the next one, so the window slides instead of teleporting. Signals
      // and the done flag are never held back — a marker arriving late would
      // land on the wrong bar, and a withheld `done` would leave the UI
      // believing the leg is still running.
      let anyLeft = false;
      const take = {};
      for (const [slot, buf] of Object.entries(pending)) {
        const backlog = buf.bars.length;
        const quota = Math.max(
          LIVE_BARS_PER_FRAME,
          Math.ceil(backlog * LIVE_CATCHUP_RATIO),
        );
        take[slot] = {
          bars: backlog > quota ? buf.bars.slice(0, quota) : buf.bars,
          signals: buf.signals,
          total: buf.total,
          done: buf.done,
        };
        if (backlog > quota) {
          buf.bars = buf.bars.slice(quota);
          buf.signals = [];
          anyLeft = true;
        } else {
          delete pending[slot];
        }
      }

      setLegData((prev) => {
        const next = { ...prev };
        for (const [slot, buf] of Object.entries(take)) {
          const cur = next[slot] || EMPTY_LEG;
          next[slot] = {
            // concat rather than push: a new identity is what makes the chart
            // notice there is anything new to draw.
            bars: buf.bars.length ? cur.bars.concat(buf.bars) : cur.bars,
            signals: buf.signals.length ? cur.signals.concat(buf.signals) : cur.signals,
            total: buf.total ?? cur.total,
            done: buf.done ?? cur.done,
          };
        }
        return next;
      });

      // Still draining — keep the pump running.
      if (anyLeft) scheduleFlushRef.current?.();
    };

    // rAF is the right clock while the tab is visible — it paces the flush to
    // the display and never runs faster than a frame.
    rafRef.current = requestAnimationFrame(flush);
    // ...but browsers suspend rAF entirely in a hidden tab. Without a fallback,
    // switching away mid-run means the buffer accumulates for the whole
    // backtest and then lands as one enormous concat plus a full setData the
    // moment you switch back — a visible freeze on a long run. setTimeout is
    // throttled in the background but still fires, so the buffer drains
    // steadily instead. Whichever clock fires first cancels the other.
    fallbackRef.current = setTimeout(flush, FLUSH_FALLBACK_MS);
  }, []);

  useEffect(() => { scheduleFlushRef.current = scheduleFlush; }, [scheduleFlush]);

  const buffer = useCallback((slot) => {
    const p = pendingRef.current;
    if (!p[slot]) p[slot] = { bars: [], signals: [] };
    return p[slot];
  }, []);

  // ── WebSocket ingest ───────────────────────────────────────────────────
  useEffect(() => {
    const onMessage = (e) => {
      const m = e.detail;
      if (!m || typeof m.type !== 'string' || !m.type.startsWith('replay_')) return;

      switch (m.type) {
        case 'replay_init': {
          pendingRef.current = {};
          const fresh = {};
          for (const leg of m.legs || []) fresh[leg.slot_id] = EMPTY_LEG;
          setLegData(fresh);
          setLegs(m.legs || []);
          setPinned(false);
          setFollow(true);
          setActiveSignal(null);
          setActiveSlot((m.legs || [])[0]?.slot_id ?? null);
          break;
        }
        case 'replay_leg_start': {
          // Discard anything still buffered for this leg — a restart means the
          // previous attempt's bars are stale, not a prefix to continue from.
          delete pendingRef.current[m.slot_id];
          setLegData((prev) => ({
            ...prev,
            [m.slot_id]: { bars: [], signals: [], total: m.total || 0, done: false },
          }));
          // Auto-advance to whichever leg is being simulated — unless the user
          // has picked a tab, in which case leave them where they are.
          if (!pinnedRef.current) {
            setActiveSlot(m.slot_id);
            setFollow(true);
            setActiveSignal(null);
          }
          break;
        }
        case 'replay_bars': {
          const buf = buffer(m.slot_id);
          for (const b of m.bars) buf.bars.push(b);
          buf.total = m.total || buf.total;
          scheduleFlush();
          break;
        }
        case 'replay_signal': {
          buffer(m.slot_id).signals.push(m);
          scheduleFlush();
          break;
        }
        case 'replay_leg_done': {
          buffer(m.slot_id).done = true;
          scheduleFlush();
          break;
        }
        default:
          break;
      }
    };

    window.addEventListener('ws-message', onMessage);
    return () => {
      window.removeEventListener('ws-message', onMessage);
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      if (fallbackRef.current != null) clearTimeout(fallbackRef.current);
    };
  }, [buffer, scheduleFlush]);

  // ── Hydration and mode, DERIVED rather than assigned in an effect ──────
  // Both used to be effects that called setState, which the React Compiler
  // rejects and which cost an extra render pass each. Neither is really a side
  // effect: they are functions of the props, so they belong in render.

  // A run opened from history — or a page reloaded mid-run — never saw the live
  // stream, so its bars come from the saved series instead. A live-streamed
  // series is always richer than the saved one (which is downsampled to 6k
  // bars), so it wins wherever both exist.
  // The result payload deliberately omits the replay series — it is megabytes,
  // and the WS strips it at completion (see backtest.py). So when there is no
  // live stream and no embedded series, fetch it: the saved endpoint for a run
  // opened from history, the current-run endpoint otherwise. Both reconstruct
  // from per-trade chart data when a run predates the replay feature, so an old
  // backtest replays too instead of showing an empty panel.
  const hasLiveBars = Object.values(legData).some(l => l.bars.length > 0);
  const needsFetch = !isRunning && !hasLiveBars && !result?.replay?.series && !!result;

  const { data: fetchedReplay } = useQuery({
    queryKey: ['replay-series', result?.backtest_id ?? 'current'],
    queryFn: () => (
      result?.backtest_id
        ? getSavedReplaySeries(result.backtest_id).then(r => r.data)
        : getReplaySeries().then(r => r.data)
    ),
    enabled: needsFetch,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const replaySrc = result?.replay?.series ? result.replay : fetchedReplay;

  const mergedLegData = useMemo(() => {
    const saved = replaySrc?.series;
    if (!saved) return legData;
    const next = { ...legData };
    for (const [slot, bars] of Object.entries(saved)) {
      const cur = next[slot];
      if (cur && cur.bars.length >= bars.length) continue;
      // A reconstructed series carries its signals alongside; a live-streamed
      // leg already has its own, so only fall back when there are none.
      const savedSignals = replaySrc?.signals?.[slot];
      next[slot] = {
        bars,
        signals: (cur?.signals?.length ? cur.signals : savedSignals) || [],
        total: bars.length,
        done: true,
      };
    }
    return next;
  }, [legData, replaySrc]);

  const shownLegs = legs.length ? legs : (replaySrc?.legs || []);
  const resolvedSlot = activeSlot
    ?? shownLegs[0]?.slot_id
    ?? Object.keys(mergedLegData)[0]
    ?? null;

  const active = mergedLegData[resolvedSlot] || EMPTY_LEG;
  const activeBarCount = active.bars.length;

  // Replay mode is simply "the run is over and there are bars to scrub".
  const replayMode = !isRunning && !!result && activeBarCount > 0;

  // Entering replay parks the cursor at the end of the series. Adjusting state
  // during render against a tracked previous value is React's documented
  // alternative to a derive-in-effect, and it costs no extra commit.
  const [prevReplayMode, setPrevReplayMode] = useState(false);
  if (replayMode !== prevReplayMode) {
    setPrevReplayMode(replayMode);
    if (replayMode) {
      setCursor(activeBarCount);
      setPlaying(false);
      setFollow(false);
    }
  }

  // ── Replay playback ────────────────────────────────────────────────────
  useEffect(() => {
    if (!replayMode || !playing) return;
    const id = setInterval(() => {
      setCursor((c) => {
        const next = c + BARS_PER_TICK * speed;
        if (next >= activeBarCount) { setPlaying(false); return activeBarCount; }
        return next;
      });
    }, REPLAY_FRAME_MS);
    return () => clearInterval(id);
  }, [replayMode, playing, speed, activeBarCount]);

  // ── Derived view data ──────────────────────────────────────────────────
  const visibleBars = useMemo(
    () => (replayMode ? active.bars.slice(0, Math.max(1, cursor)) : active.bars),
    [replayMode, active.bars, cursor],
  );

  const visibleSignals = useMemo(() => {
    if (!replayMode) return active.signals;
    const cutoff = visibleBars.length ? visibleBars[visibleBars.length - 1].time : Infinity;
    return active.signals.filter((s) => s.time <= cutoff);
  }, [replayMode, active.signals, visibleBars]);

  const selectSlot = (slot) => {
    setPinned(true);
    setActiveSlot(slot);
    setActiveSignal(null);
    setCursor((mergedLegData[slot] || EMPTY_LEG).bars.length);
    setFollow(!replayMode);
  };

  const jumpToSignal = (sig) => {
    setActiveSignal(sig);
    if (!replayMode) return;
    const idx = active.bars.findIndex((b) => b.time >= sig.time);
    if (idx >= 0) setCursor(Math.min(active.bars.length, idx + 40));
  };

  if (!shownLegs.length && !activeBarCount) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
        <Activity size={28} style={{ marginBottom: 12, opacity: 0.5 }} />
        <div style={{ fontSize: '0.85rem' }}>
          {isRunning ? 'Waiting for the first bars…' : 'Run a backtest to see the replay.'}
        </div>
        {progress?.stage && (
          <div style={{ fontSize: '0.7rem', marginTop: 6, opacity: 0.7 }}>{progress.stage}</div>
        )}
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
        borderBottom: '1px solid var(--border)', flexWrap: 'wrap',
      }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 5,
          fontSize: '0.68rem', letterSpacing: '0.06em', textTransform: 'uppercase',
          color: replayMode ? 'var(--text-secondary)' : 'var(--green)', fontWeight: 600,
        }}>
          {replayMode ? <><Layers size={12} /> Replay</> : <><Radio size={12} /> Live</>}
        </span>

        {shownLegs.length > 1 && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', flex: 1 }}>
            {shownLegs.map((leg) => (
              <LegTab
                key={leg.slot_id}
                leg={leg}
                data={mergedLegData[leg.slot_id] || EMPTY_LEG}
                isActive={leg.slot_id === resolvedSlot}
                onSelect={selectSlot}
              />
            ))}
          </div>
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {!replayMode && !follow && (
            <button className="btn btn-sm btn-secondary" onClick={() => setFollow(true)}>
              <Crosshair size={12} /> Follow
            </button>
          )}
          {pinned && isRunning && (
            <button
              className="btn btn-sm btn-secondary"
              onClick={() => setPinned(false)}
              title="Resume auto-advance between legs"
            >Auto</button>
          )}
          <span style={{
            fontSize: '0.66rem', color: 'var(--text-muted)',
            fontFamily: 'ui-monospace, monospace',
          }}>
            {visibleBars.length.toLocaleString()} bars · {visibleSignals.length} signals
          </span>
        </div>
      </div>

      {/* Chart beside the signal rail on a wide screen, stacked below it on a
          narrow one. Side-by-side at phone width left the chart a ~120px strip
          dominated by its own price axis — technically rendered, practically
          unreadable. `wrap` plus a min-width on the chart column is what makes
          the rail drop underneath instead of squeezing it. */}
      <div style={{ display: 'flex', alignItems: 'stretch', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 420px', minWidth: 0, padding: 10 }}>
          <ReplayChart
            bars={visibleBars}
            signals={visibleSignals}
            follow={!replayMode && follow}
            onUserScroll={() => setFollow(false)}
            activeSignal={activeSignal}
            height={440}
          />

          {replayMode && (
            <ReplayControls
              cursor={cursor}
              total={activeBarCount}
              playing={playing}
              speed={speed}
              onSeek={setCursor}
              onTogglePlay={() => setPlaying((p) => !p)}
              onSpeed={setSpeed}
            />
          )}
        </div>

        <SignalRail signals={visibleSignals} activeSignal={activeSignal} onSelect={jumpToSignal} />
      </div>
    </div>
  );
}

// ── One portfolio leg ────────────────────────────────────────────────────
function LegTab({ leg, data, isActive, onSelect }) {
  const pct = data.total ? Math.min(100, Math.round((data.bars.length / data.total) * 100)) : 0;
  return (
    <button
      onClick={() => onSelect(leg.slot_id)}
      title={`${leg.symbol} · ${leg.strategy_id}`}
      style={{
        position: 'relative', overflow: 'hidden',
        padding: '4px 10px', borderRadius: 'var(--radius-xs)',
        border: `1px solid ${isActive ? 'var(--green-dim)' : 'var(--border)'}`,
        background: isActive ? 'var(--green-glow)' : 'transparent',
        color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
        fontSize: '0.7rem', cursor: 'pointer', whiteSpace: 'nowrap',
        fontFamily: 'ui-monospace, monospace',
      }}
    >
      {/* Per-leg progress reads through the tab itself, so which legs are done
          is visible without switching to each one. */}
      {!data.done && pct > 0 && (
        <span style={{
          position: 'absolute', left: 0, bottom: 0, height: 2,
          width: `${pct}%`, background: 'var(--green)', opacity: 0.7,
        }} />
      )}
      {leg.symbol}
      <span style={{ opacity: 0.55, marginLeft: 5 }}>{shortStrategy(leg.strategy_id)}</span>
      {data.signals.length > 0 && (
        <span style={{
          marginLeft: 6, padding: '0 4px', borderRadius: 8,
          background: 'var(--bg-tertiary)', fontSize: '0.62rem',
        }}>{data.signals.length}</span>
      )}
    </button>
  );
}

// ── Replay transport ─────────────────────────────────────────────────────
function ReplayControls({ cursor, total, playing, speed, onSeek, onTogglePlay, onSpeed }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
      <button className="btn btn-sm btn-secondary" onClick={() => onSeek(0)} title="Back to start">
        <SkipBack size={13} />
      </button>
      <button className="btn btn-sm btn-primary" onClick={onTogglePlay}>
        {playing ? <Pause size={13} /> : <Play size={13} />}
      </button>
      <button className="btn btn-sm btn-secondary" onClick={() => onSeek(total)} title="Jump to end">
        <SkipForward size={13} />
      </button>

      <input
        type="range" min={0} max={Math.max(1, total)} value={Math.min(cursor, total)}
        onChange={(e) => onSeek(+e.target.value)}
        style={{ flex: 1, accentColor: 'var(--green)' }}
      />

      <div style={{ display: 'flex', gap: 2 }}>
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => onSpeed(s)}
            style={{
              padding: '2px 6px', borderRadius: 'var(--radius-xs)', cursor: 'pointer',
              border: `1px solid ${s === speed ? 'var(--green-dim)' : 'var(--border)'}`,
              background: s === speed ? 'var(--green-glow)' : 'transparent',
              color: s === speed ? 'var(--text-primary)' : 'var(--text-muted)',
              fontSize: '0.64rem', fontFamily: 'ui-monospace, monospace',
            }}
          >{s}x</button>
        ))}
      </div>

      <span style={{
        fontSize: '0.64rem', color: 'var(--text-muted)',
        fontFamily: 'ui-monospace, monospace', minWidth: 92, textAlign: 'right',
      }}>
        {Math.min(cursor, total).toLocaleString()} / {total.toLocaleString()}
      </span>
    </div>
  );
}

// ── Signal rail: what fired, and what it needed ──────────────────────────
function SignalRail({ signals, activeSignal, onSelect }) {
  const railRef = useRef(null);
  const count = signals.length;

  // Keep the newest signal in view during a live run — the interesting one is
  // always the one that just fired.
  useEffect(() => {
    const el = railRef.current;
    if (el && !activeSignal) el.scrollTop = 0;
  }, [count, activeSignal]);

  const ordered = useMemo(() => signals.slice().reverse(), [signals]);

  return (
    <div style={{
      // Grows to full width once it wraps below the chart, rather than staying
      // a 250px column stranded on one side.
      flex: '1 1 250px', minWidth: 0, maxWidth: '100%',
      borderLeft: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', maxHeight: 540,
    }}>
      <div style={{
        padding: '8px 12px', borderBottom: '1px solid var(--border)',
        fontSize: '0.66rem', letterSpacing: '0.06em', textTransform: 'uppercase',
        color: 'var(--text-secondary)',
      }}>Signals</div>

      <div ref={railRef} style={{ overflowY: 'auto', flex: 1 }}>
        {count === 0 && (
          <div style={{ padding: 16, fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            No signals yet.
          </div>
        )}
        {ordered.map((s, i) => (
          <SignalRow
            key={`${s.time}-${i}`}
            signal={s}
            isActive={!!activeSignal && activeSignal.time === s.time}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}

function SignalRow({ signal: s, isActive, onSelect }) {
  const buy = s.direction === 'BUY';
  const summary = s.confluence_summary || {};
  return (
    <button
      onClick={() => onSelect(s)}
      style={{
        display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
        padding: '8px 12px', border: 'none', borderBottom: '1px solid var(--border)',
        background: isActive ? 'var(--bg-tertiary)' : 'transparent',
        color: 'var(--text-primary)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: buy ? 'var(--green)' : 'var(--red)', fontWeight: 600, fontSize: '0.7rem' }}>
          {s.direction}
        </span>
        <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'ui-monospace, monospace' }}>
          {fmtTime(s.time)}
        </span>
      </div>

      <div style={{
        fontSize: '0.62rem', color: 'var(--text-secondary)', marginTop: 2,
        fontFamily: 'ui-monospace, monospace',
      }}>
        {fmtPrice(s.entry)} · SL {fmtPrice(s.sl)}
        {s.confluence_score != null && <> · {s.confluence_score}</>}
      </div>

      {/* The confluence chain, straight from the strategy that measured it.
          This is the answer to "what did this entry actually require". */}
      {(summary.trigger?.length || summary.confluence?.length) ? (
        <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 3 }}>
          {(summary.trigger || []).map((l) => (
            <Tag key={`t-${l}`} color="var(--yellow)" bg="var(--yellow-dim)">{l}</Tag>
          ))}
          {(summary.confluence || []).map((l) => (
            <Tag key={`c-${l}`} color="var(--blue)" bg="var(--blue-dim)">{l}</Tag>
          ))}
        </div>
      ) : null}
    </button>
  );
}

function Tag({ children, color, bg }) {
  return (
    <span style={{
      fontSize: '0.58rem', padding: '1px 5px', borderRadius: 4,
      background: bg, color, whiteSpace: 'nowrap',
    }}>{children}</span>
  );
}

function shortStrategy(id = '') {
  return id.replace(/_v\d+$/, '');
}

function fmtPrice(v) {
  return typeof v === 'number' ? v.toFixed(5) : '—';
}

function fmtTime(epoch) {
  if (!epoch) return '—';
  const d = new Date(epoch * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}
