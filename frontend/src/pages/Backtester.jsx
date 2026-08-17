import React, { useState, useEffect, useRef, useCallback, memo, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Play, Trash2, Eye, Save, X, ChevronDown, ChevronRight, Loader2, Clock, Target, Shield, Terminal, Settings2, Zap, LayoutDashboard, PlusCircle, MinusCircle } from 'lucide-react';
import { runBacktest, runPortfolioBacktest, getBacktests, deleteBacktest, getBacktest, saveBacktest, getBotLogs, getConfig, getBacktestStatus, getLatestBacktestResult, stopBacktest, getSavedTradeChart, getUnsavedTradeChart } from '../services/api';
import TradeChart from '../components/TradeChart';
import { useConnectionStore, useAuthStore } from '../store';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, CartesianGrid } from 'recharts';
import * as summaryEngine from '../utils/summaryEngine';

const SYMBOLS = [
  'XAUUSD', 'Gold', 'XAU', 'XAGUSD', 'Silver', 'XAG', 'XPTUSD', 'Platinum', 'XPT',
  'EURUSD', 'GBPUSD', 'AUDUSD', 'Aussie', 'GBPJPY', 'Geppy', 'GJ',
  'GBPNZD', 'GBPAUD', 'GBPCHF', 'EURJPY', 'EURAUD', 'USDJPY',
  'USDCHF', 'USDCAD', 'NZDUSD', 'Kiwi', 'AUDJPY', 'CADJPY', 'GBPCAD', 'EURGBP',
  'US30', 'Wall Street 30', 'WS30', 'DJI', 'DOW', 'YM',
  'NAS100', 'US100', 'USTEC', 'NDX', 'NQ',
  'SPX500', 'US500', 'SPX', 'SP500', 'S&P500', 'ES',
  'GER40', 'DAX', 'DE40', 'GER30', 'HK50', 'Hang Seng', 'HSI',
  'US2000', 'RUT', 'UK100', 'FTSE100', 'FRA40', 'CAC40',
  'EU50', 'EUSTX50', 'NTH25', 'AEX25', 'SWI20', 'SMI20',
  'AUS200', 'ASX200', 'JP225', 'Nikkei',
  'USOIL', 'WTI', 'Crude Oil', 'OIL', 'XTIUSD', 'US Oil',
  'UKOIL', 'Brent', 'UK Brent Oil', 'XCUUSD', 'Copper',
  'NG', 'XNGUSD', 'Natural Gas',
  'BTCUSD', 'Bitcoin', 'BTC', 'ETHUSD', 'ETH', 'Ethereum',
  'DOGUSD', 'Dogecoin', 'DOGE', 'SOLUSD', 'Solana', 'SOL',
  'XRPUSD', 'Ripple', 'XRP', 'LTCUSD', 'Litecoin', 'LTC',
  'Volatility 10 Index', 'Volatility 25 Index', 'Volatility 50 Index',
  'Volatility 75 Index', 'Volatility 100 Index', 'Volatility 150 Index', 'Volatility 250 Index',
  'Volatility 10 (1s) Index', 'Volatility 25 (1s) Index', 'Volatility 50 (1s) Index',
  'Volatility 75 (1s) Index', 'Volatility 100 (1s) Index', 'Volatility 150 (1s) Index', 'Volatility 250 (1s) Index',
  'Boom 300 Index', 'Boom 500 Index', 'Boom 1000 Index',
  'Crash 300 Index', 'Crash 500 Index', 'Crash 1000 Index',
  'Jump 10 Index', 'Jump 25 Index', 'Jump 50 Index', 'Jump 75 Index', 'Jump 100 Index',
  'Step Index', 'Range Break 100 Index', 'Range Break 200 Index',
];

const StrategyParamsEditor = ({ strategyId, form, setForm, u }) => {
  const smc = form.smc || {};
  const updateSmc = (k, v) => setForm({ ...form, smc: { ...smc, [k]: v } });
  const dja = form.drift_jump_alpha || {};
  const updateDja = (k, v) => setForm({ ...form, drift_jump_alpha: { ...dja, [k]: v } });
  const crt = form.crt || {};
  const updateCrt = (k, v) => setForm({ ...form, crt: { ...crt, [k]: v } });

  if (strategyId === 'APA_v1') {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
        <div><label style={{ fontSize: '0.7rem' }}>Confluence Threshold</label><input type="number" value={smc.confluence_threshold} onChange={e => updateSmc('confluence_threshold', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>HTF Swing Length</label><input type="number" value={smc.swing_length_htf} onChange={e => updateSmc('swing_length_htf', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>LTF Swing Length</label><input type="number" value={smc.swing_length_ltf} onChange={e => updateSmc('swing_length_ltf', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>OB Impulse Ratio</label><input type="number" step="0.1" value={smc.ob_impulse_ratio} onChange={e => updateSmc('ob_impulse_ratio', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>FVG Min Gap (pips)</label><input type="number" step="0.5" value={smc.fvg_min_gap_pips} onChange={e => updateSmc('fvg_min_gap_pips', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Sweep Min Pips</label><input type="number" step="0.5" value={smc.liq_sweep_min_pips} onChange={e => updateSmc('liq_sweep_min_pips', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Max Spread (pips)</label><input type="number" step="0.1" value={smc.max_spread_pips} onChange={e => updateSmc('max_spread_pips', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Manual HTF Bias</label><select value={form.manual_bias || 'NONE'} onChange={e => u('manual_bias', e.target.value)}><option value="NONE">Auto</option><option value="BULLISH">Bullish Only</option><option value="BEARISH">Bearish Only</option></select></div>
        <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 12, marginTop: 8 }}>
        </div>
      </div>
    );
  }
  if (strategyId === 'DriftJumpAlpha_v1') {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
        <div><label style={{ fontSize: '0.7rem' }}>Spike Lookback Bars</label><input type="number" value={dja.spike_lookback_bars} onChange={e => updateDja('spike_lookback_bars', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Drift EMA Fast</label><input type="number" value={dja.drift_ema_fast} onChange={e => updateDja('drift_ema_fast', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Drift EMA Slow</label><input type="number" value={dja.drift_ema_slow} onChange={e => updateDja('drift_ema_slow', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Min ADX to Trade</label><input type="number" value={dja.min_adx_to_trade} onChange={e => updateDja('min_adx_to_trade', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Jump Threshold (%)</label><input type="number" step="0.5" value={dja.jump_entry_percentile_threshold} onChange={e => updateDja('jump_entry_percentile_threshold', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Max Lots per Symbol</label><input type="number" step="0.1" value={dja.aggregate_max_lots_per_symbol} onChange={e => updateDja('aggregate_max_lots_per_symbol', +e.target.value)} /></div>
        <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 12, marginTop: 8 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.75rem' }}><input type="checkbox" checked={dja.trade_jumps_enabled} onChange={e => updateDja('trade_jumps_enabled', e.target.checked)} style={{ width: 14, height: 14 }} /> Trade Jumps Enabled</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.75rem' }}><input type="checkbox" checked={dja.control_test_passed} onChange={e => updateDja('control_test_passed', e.target.checked)} style={{ width: 14, height: 14 }} /> Control Test Passed</label>
        </div>
      </div>
    );
  }
  if (strategyId === 'CRT_v1') {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
        <div><label style={{ fontSize: '0.7rem' }}>HTF Timeframe</label><select value={crt.htf_timeframe} onChange={e => updateCrt('htf_timeframe', e.target.value)}>{['M15', 'M30', 'H1', 'H4', 'D1'].map(t => <option key={t} value={t}>{t}</option>)}</select></div>
        <div><label style={{ fontSize: '0.7rem' }}>LTF Timeframe</label><select value={crt.ltf_timeframe} onChange={e => updateCrt('ltf_timeframe', e.target.value)}>{['M1', 'M5', 'M15'].map(t => <option key={t} value={t}>{t}</option>)}</select></div>
        <div><label style={{ fontSize: '0.7rem' }}>Target R-Multiple</label><input type="number" step="0.1" value={crt.target_r_multiple} onChange={e => updateCrt('target_r_multiple', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Max Trades / Session</label><input type="number" value={crt.max_trades_per_session} onChange={e => updateCrt('max_trades_per_session', +e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Session Start (ET)</label><input type="text" value={crt.session_start} onChange={e => updateCrt('session_start', e.target.value)} /></div>
        <div><label style={{ fontSize: '0.7rem' }}>Session Cutoff (ET)</label><input type="text" value={crt.session_cutoff} onChange={e => updateCrt('session_cutoff', e.target.value)} /></div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12, fontSize: '0.75rem' }}>
            <input type="checkbox" checked={crt.bypass_session_synthetics ?? true} onChange={e => updateCrt('bypass_session_synthetics', e.target.checked)} />
            Bypass Session Filter (Synthetics)
          </label>
        </div>
      </div>
    );
  }
  if (strategyId === 'VWAP_v1') return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', gap: 8 }}>
      <div><label style={{ fontSize: '0.7rem' }}>VWAP Anchor (min)</label><input type="number" value={form.vwap_anchor_minutes ?? 15} onChange={e => u('vwap_anchor_minutes', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Momentum Lookback</label><input type="number" value={form.momentum_lookback_bars ?? 4} onChange={e => u('momentum_lookback_bars', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Momentum Threshold (%)</label><input type="number" step="0.01" value={form.momentum_threshold_pct ?? 0.1} onChange={e => u('momentum_threshold_pct', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>SL Points</label><input type="number" step="1" min="10" value={form.sl_points ?? 80} onChange={e => u('sl_points', +e.target.value)} /><div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Primary SL distance in points (e.g. 170 for USDCHF = 17 pips)</div></div>
      <div><label style={{ fontSize: '0.7rem' }}>SL ATR Multiplier</label><input type="number" step="0.1" min="0" value={form.vwap_sl_atr_multiplier ?? 0} onChange={e => u('vwap_sl_atr_multiplier', +e.target.value)} /><div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>ATR floor: SL = max(SL Points, ATR × this). Set 0 for pure fixed SL.</div></div>
    </div>
  );
  if (strategyId === 'HTFFVGFlip_v1') return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
      <div><label style={{ fontSize: '0.7rem' }}>HTF Timeframe</label><select value={form.htf_timeframe} onChange={e => u('htf_timeframe', e.target.value)}>{['M15', 'M30', 'H1', 'H4', 'D1'].map(t => <option key={t} value={t}>{t}</option>)}</select></div>
      <div><label style={{ fontSize: '0.7rem' }}>Entry Confirm TF</label><select value={form.entry_confirmation_tf} onChange={e => u('entry_confirmation_tf', e.target.value)}>{['M1', 'M5', 'M15'].map(t => <option key={t} value={t}>{t}</option>)}</select></div>
      <div><label style={{ fontSize: '0.7rem' }}>Target RR</label><input type="number" step="0.1" value={form.target_rr} onChange={e => u('target_rr', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Session Start</label><input type="text" value={form.session_start} onChange={e => u('session_start', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Session Cutoff</label><input type="text" value={form.session_cutoff} onChange={e => u('session_cutoff', e.target.value)} /></div>
      <div style={{ gridColumn: '1 / -1' }}><label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12, fontSize: '0.75rem' }}><input type="checkbox" checked={form.require_unfilled_htf_fvg ?? true} onChange={e => u('require_unfilled_htf_fvg', e.target.checked)} /> Require Unfilled HTF FVG</label></div>
    </div>
  );
  if (strategyId === 'BiasIFVG_v1') return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
      <div><label style={{ fontSize: '0.7rem' }}>Stop Method</label><input type="text" value={form.stop_method} onChange={e => u('stop_method', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Target RR Range Min</label><input type="number" step="0.1" value={form.target_rr_range_min} onChange={e => u('target_rr_range_min', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Target RR Range Max</label><input type="number" step="0.1" value={form.target_rr_range_max} onChange={e => u('target_rr_range_max', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Max Trades / Day</label><input type="number" value={form.max_trades_per_day} onChange={e => u('max_trades_per_day', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Session Start</label><input type="text" value={form.session_start} onChange={e => u('session_start', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Session Cutoff</label><input type="text" value={form.session_cutoff} onChange={e => u('session_cutoff', e.target.value)} /></div>
    </div>
  );
  if (strategyId === 'NYOpenRetest_v1') return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
      <div><label style={{ fontSize: '0.7rem' }}>Range Start</label><input type="text" value={form.range_window_start} onChange={e => u('range_window_start', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Range End</label><input type="text" value={form.range_window_end} onChange={e => u('range_window_end', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Earliest Break Time</label><input type="text" value={form.earliest_valid_break_time} onChange={e => u('earliest_valid_break_time', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Session End</label><input type="text" value={form.session_end} onChange={e => u('session_end', e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Stop Buffer (pts)</label><input type="number" step="0.1" value={form.stop_buffer_points} onChange={e => u('stop_buffer_points', +e.target.value)} /></div>
      <div><label style={{ fontSize: '0.7rem' }}>Fixed Target (pts)</label><input type="number" step="0.1" value={form.fixed_target_points} onChange={e => u('fixed_target_points', +e.target.value)} /></div>
      <div style={{ gridColumn: '1 / -1' }}><label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12, fontSize: '0.75rem' }}><input type="checkbox" checked={form.dynamic_target_override ?? true} onChange={e => u('dynamic_target_override', e.target.checked)} /> Dynamic Target Override</label></div>
    </div>
  );
  return null;
}

// Custom styled autocomplete — replaces the native <input list="..."> /
// <datalist> approach, which renders as the browser's unstyled native
// suggestion popup (inconsistent across browsers, no highlighting, shows
// every option rather than filtering cleanly on some engines). This keeps
// the same "type to filter, click or Enter to pick" flow but with a
// dropdown that matches the rest of the app's dark theme, keyboard nav,
// and a visible "no matches" state.
const SymbolAutocomplete = memo(function SymbolAutocomplete({ value, onChange, options, placeholder = 'Type to find symbol', autoFocus = false }) {
  const [query, setQuery] = useState(value || '');
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const wrapRef = useRef(null);

  useEffect(() => { setQuery(value || ''); }, [value]);

  useEffect(() => {
    const onClickOutside = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options.slice(0, 50);
    const starts = options.filter(o => o.toLowerCase().startsWith(q));
    const contains = options.filter(o => !o.toLowerCase().startsWith(q) && o.toLowerCase().includes(q));
    return [...starts, ...contains].slice(0, 50);
  }, [query, options]);

  const commit = (val) => {
    setQuery(val);
    onChange(val);
    setOpen(false);
  };

  const handleKeyDown = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return; }
    if (!open) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setHighlight(h => Math.min(h + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight(h => Math.max(h - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (filtered[highlight]) commit(filtered[highlight]); else { onChange(query); setOpen(false); } }
    else if (e.key === 'Escape') { setOpen(false); }
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <input
        type="text"
        autoFocus={autoFocus}
        value={query}
        placeholder={placeholder}
        onChange={e => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); setHighlight(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        style={{ width: '100%' }}
      />
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 50,
          background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
          maxHeight: 220, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
        }}>
          {filtered.length === 0 ? (
            <div style={{ padding: '8px 10px', fontSize: '0.8rem', color: 'var(--text-muted)' }}>No matching symbols</div>
          ) : filtered.map((opt, i) => (
            <div
              key={opt}
              onMouseDown={(e) => { e.preventDefault(); commit(opt); }}
              onMouseEnter={() => setHighlight(i)}
              style={{
                padding: '6px 10px', fontSize: '0.8rem', cursor: 'pointer',
                background: i === highlight ? 'var(--bg-hover, rgba(255,255,255,0.06))' : 'transparent',
                color: 'var(--text-primary)',
              }}
            >
              {opt}
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

function fmt(v) {
  if (!v) return '—';
  if (typeof v === 'string' && v.includes('T')) return new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  if (typeof v === 'number' && v > 1e9) return new Date(v * 1000).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  return String(v);
}
function fmtDur(m) { if (!m || m <= 0) return '—'; if (m < 60) return `${m.toFixed(0)}m`; if (m < 1440) return `${(m / 60).toFixed(1)}h`; return `${(m / 1440).toFixed(1)}d`; }

function MetricCard({ title, value, color }) {
  return (
    <div style={{ padding: '12px 16px', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: '1.2rem', fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}

function ProgressBar({ progress }) {
  if (!progress || progress.pct === undefined) return null;
  return (<div style={{ marginTop: 12 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 4 }}>
      <span>{progress.message || progress.stage}</span><span>{progress.pct}%</span>
    </div>
    <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
      <div style={{ height: '100%', width: `${progress.pct}%`, background: 'linear-gradient(90deg,var(--blue),var(--green))', borderRadius: 3, transition: 'width 0.3s ease' }} />
    </div>
  </div>);
}

function LiveLogPanel({ events, setEvents }) {
  const ref = useRef(null);
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s => s.isAuthenticated);
  const { data: logs } = useQuery({ queryKey: ['btLogs'], queryFn: () => getBotLogs(50).then(r => r.data), refetchInterval: 2000, enabled: status === 'ONLINE' && isAuth });

  useEffect(() => {
    const h = e => { try { const m = e.detail; if (m.type === 'activity_log' && m.event) setEvents(p => [m.event, ...p].slice(0, 2000)); } catch { } };
    window.addEventListener('ws-message', h);
    return () => window.removeEventListener('ws-message', h);
  }, []);

  const merged = useMemo(() => {
    const all = [...events, ...(logs?.events || [])]; const seen = new Set(); const out = [];
    for (const e of all) { const k = `${e.time}|${e.message}`; if (!seen.has(k)) { seen.add(k); out.push(e); } }
    out.sort((a, b) => (b.time || '').localeCompare(a.time || ''));
    const allowed = ['BACKTEST', 'BACKTEST_LOG', 'SIGNAL', 'TRADE', 'SMC'];
    return out.filter(e => allowed.some(a => (e.category || '').includes(a)) || e.level === 'ERROR');
  }, [logs, events]);

  return (<div style={{ maxHeight: 340, overflow: 'auto', background: '#0d1117', borderRadius: 'var(--radius-xs)', padding: '8px 12px', fontFamily: "'JetBrains Mono',monospace", fontSize: '0.72rem', lineHeight: 1.7, border: '1px solid var(--border)' }}>
    {merged.length ? merged.map((e, i) => (
      <div key={`${e.time}-${i}`} style={{ padding: '2px 0', borderBottom: '1px solid #21262d', display: 'flex', gap: 8 }}>
        <span style={{ color: '#484f58', flexShrink: 0, minWidth: 65 }}>{e.time ? new Date(e.time).toLocaleTimeString() : ''}</span>
        <span style={{ color: e.level === 'ERROR' ? '#f85149' : e.category === 'SIGNAL' ? '#f0883e' : '#58a6ff', fontWeight: 600, flexShrink: 0, minWidth: 65, textTransform: 'uppercase', fontSize: '0.65rem' }}>[{e.category || e.level}]</span>
        <span style={{ color: e.level === 'ERROR' ? '#f85149' : '#c9d1d9', wordBreak: 'break-word' }}>{e.message}</span>
      </div>
    )) : (<div style={{ padding: 20, textAlign: 'center', color: '#484f58' }}><Terminal size={20} style={{ marginBottom: 8, opacity: 0.3 }} /><div>Waiting for backtest events...</div></div>)}
  </div>);
}

function SaveModal({ result, form, isPortfolio, portfolioSymbols, onClose, onSuccess }) {
  const defaultTitle = isPortfolio
    ? `Portfolio (${portfolioSymbols?.length || 0} symbols) — ${new Date().toLocaleDateString()}`
    : (form.symbol || '');
  const [titleInput, setTitleInput] = useState(defaultTitle);
  const [notesInput, setNotesInput] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);

  const confirmSave = async () => {
    if (!result) return;
    const trimmedTitle = titleInput.trim();
    if (!trimmedTitle) {
      setError('Please give this backtest a title.');
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await saveBacktest(result.backtest_id, {
        backtest_data: {
          ...result,
          strategy_id: result.params_snapshot?.strategy_id || form.strategy_id,
          // For portfolio saves, `symbol` on the run row is still populated
          // (e.g. as a comma-joined list) so older UI that reads .symbol as
          // a fallback still shows something reasonable — but `title` is
          // what the saved-backtests list should actually display.
          symbol: isPortfolio ? (portfolioSymbols || []).map(s => s.symbol).join(', ') : (result.params_snapshot?.symbol || form.symbol),
          title: trimmedTitle,
          risk_config: result.params_snapshot || form,
          notes: notesInput
        },
        save_mode: 'FULL'
      });
      onSuccess();
    } catch (e) {
      console.error("Save failed", e);
      setError(e?.response?.data?.detail || e.message || 'Save failed');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="modal-overlay" style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.7)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="card" style={{ width: '90%', maxWidth: 500, padding: 20 }}>
        <h3 style={{ marginTop: 0 }}>Save Backtest</h3>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
          {isPortfolio
            ? 'This is a portfolio run across multiple symbols — give it a title so you can find it later.'
            : 'Give this run a title, and add narration and notes for future review. The current parameters will be saved automatically.'}
        </p>

        <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginTop: 12, marginBottom: 4 }}>Title</label>
        <input
          type="text"
          value={titleInput}
          onChange={e => setTitleInput(e.target.value)}
          placeholder="E.g., SMC v1 — London killzone, tight SL"
          style={{ width: '100%', padding: 10, background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}
        />

        <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginTop: 12, marginBottom: 4 }}>Description / Notes</label>
        <textarea
          value={notesInput}
          onChange={e => setNotesInput(e.target.value)}
          placeholder="E.g., Added Killzones to avoid Asian range chop. Improved WR by 5%..."
          style={{ width: '100%', height: 100, marginBottom: 15, padding: 10, background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', resize: 'vertical' }}
        />
        {error && <div style={{ color: 'var(--red)', fontSize: '0.8rem', marginBottom: 15 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={confirmSave} disabled={isSaving}>
            {isSaving ? 'Saving...' : 'Save & Close'}
          </button>
        </div>
      </div>
    </div>
  );
}

const GroupedTradeRow = memo(function GroupedTradeRow({ group, index, measureRef, vIndex, backtestId }) {
  const [open, setOpen] = useState(false);
  const [activeChart, setActiveChart] = useState('M5');
  const [fetchedCharts, setFetchedCharts] = useState(null);
  const [isLoadingChart, setIsLoadingChart] = useState(false);
  const pnl = group.combined_pnl || 0;

  const hasEmbeddedChart = (group.chart_data?.length > 0) || (group.chart_data_m15?.length > 0) || (group.chart_data_m5?.length > 0);

  useEffect(() => {
    if (open && !hasEmbeddedChart && !fetchedCharts && !isLoadingChart) {
      setIsLoadingChart(true);
      const req = backtestId ? getSavedTradeChart(backtestId, group.group_id) : getUnsavedTradeChart(group.group_id);
      req.then(res => setFetchedCharts(res.data)).catch(() => { }).finally(() => setIsLoadingChart(false));
    }
  }, [open, hasEmbeddedChart, fetchedCharts, isLoadingChart, backtestId, group.group_id]);

  // Use fetched charts, or fallback to any embedded data
  const mergedGroup = {
    ...group,
    chart_data: fetchedCharts?.chart_data || group.chart_data,
    chart_data_m15: fetchedCharts?.chart_data_m15 || group.chart_data_m15,
    chart_data_m5: fetchedCharts?.chart_data_m5 || group.chart_data_m5
  };


  return (
    <tbody ref={measureRef} data-index={vIndex}>
      <tr onClick={() => setOpen(!open)} style={{ cursor: 'pointer', background: pnl >= 0 ? 'rgba(63,182,139,0.04)' : 'rgba(248,81,73,0.04)' }}>
        <td>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</td>
        <td>{index + 1}</td>
        <td><strong>{group.symbol}</strong></td>
        <td><span className={`badge ${group.direction === 'BUY' ? 'badge-green' : 'badge-red'}`}>{group.direction === 'BUY' ? '▲ BUY' : '▼ SELL'}</span></td>
        <td>{typeof group.entry_price === 'number' ? group.entry_price.toFixed(2) : group.entry_price}</td>
        <td>{fmt(group.entry_time_iso)}</td>
        <td>{fmt(group.exit_time_iso)}</td>
        <td>{fmtDur(group.duration_minutes)}</td>
        <td>{group.tp_count} TPs ({group.tp_wins}W/{group.tp_losses}L)</td>
        <td style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${pnl.toFixed(2)}</td>
        <td style={{ fontSize: '0.8rem' }}>${group.balance_before != null ? group.balance_before.toFixed(2) : '—'}</td>
        <td style={{ fontSize: '0.8rem' }}>${group.balance_after != null ? group.balance_after.toFixed(2) : '—'}</td>
        <td><span className="badge badge-blue">{group.entry_session || '—'}</span>{group.exit_session && group.exit_session !== group.entry_session && <span className="badge badge-blue" style={{ marginLeft: 4 }}>→{group.exit_session}</span>}</td>
      </tr>
      {open && group.sub_trades?.map((t, j) => (
        <tr key={j} style={{ background: 'var(--bg-tertiary)', width: '100%', fontSize: '0.8rem' }}>
          <td></td><td></td>
          <td colSpan={2}><span className={`badge ${t.exit_reason?.startsWith('TP') ? 'badge-green' : t.exit_reason === 'BE_SL' ? 'badge-blue' : 'badge-red'}`}>TP{t.tp_level} → {t.exit_reason}</span></td>
          <td colSpan={2} style={{ fontSize: '0.72rem' }}>{fmt(t.entry_time_iso)} → {fmt(t.exit_time_iso)}</td>
          <td>{fmtDur(t.duration_minutes)}</td>
          <td style={{ fontSize: '0.72rem' }}>Vol: {t.volume} | BE: {t.be_applied ? '✓' : '✗'}{t.trail_applied ? ' | Trail: ✓' : ''}</td>
          <td style={{ fontSize: '0.72rem' }}>
            MAE: {(t.mae_pips || 0).toFixed(1)}p | MFE: {(t.mfe_pips || 0).toFixed(1)}p
            <br />
            Bal: ${t.balance_before != null ? t.balance_before.toFixed(2) : '—'} → ${t.balance_after != null ? t.balance_after.toFixed(2) : '—'}
          </td>
          <td style={{ color: (t.pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
            ${(t.pnl || 0).toFixed(2)}
          </td>
          <td>{t.session || '—'}</td>
          <td></td><td></td>
        </tr>
      ))}
      {open && mergedGroup.sub_trades?.[0]?.entry_confirmations && (
        <tr><td colSpan={13} style={{ padding: 0, border: 'none' }}>
          <div style={{ display: 'flex', gap: '20px', flexDirection: 'row', padding: '16px', background: 'var(--bg-tertiary)', margin: '4px 0px', borderRadius: 'var(--radius-xs)' }}>
            <div style={{ width: '300px', flexShrink: 0, maxHeight: '800px', overflowY: 'auto' }}>
              <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>Entry Confirmations (Score: {group.sub_trades[0].confluence_score || '—'})</div>
              {(group.sub_trades[0].entry_confirmations || []).map((c, i) => {
                const isHeader = c.startsWith('═') || c.startsWith('──');
                const isPass = c.startsWith('✓');
                const isFail = c.startsWith('✗');
                const isMixed = c.startsWith('△');
                return <div key={i} style={{
                  fontSize: isHeader ? '0.72rem' : '0.75rem',
                  fontWeight: isHeader ? 700 : 400,
                  color: isHeader ? 'var(--blue)' : isPass ? 'var(--green)' : isFail ? 'var(--text-muted)' : isMixed ? 'var(--yellow)' : 'var(--text-secondary)',
                  padding: '3px 0',
                  borderBottom: isHeader ? 'none' : '1px solid var(--border)',
                  marginTop: isHeader ? 8 : 0,
                }}>{c}</div>;
              })}
              {group.sub_trades[0].exit_confirmations && (
                <>
                  <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginTop: 16, marginBottom: 6, fontWeight: 600 }}>Exit Info</div>
                  {group.sub_trades[0].exit_confirmations.map((c, i) =>
                    <div key={i} style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', padding: '3px 0', borderBottom: '1px solid var(--border)' }}>{c}</div>
                  )}
                </>
              )}

              <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginTop: 16, marginBottom: 8, fontWeight: 600 }}>Trade Analytics</div>
              <div style={{ display: 'grid', gap: '8px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Group ID</span>
                  <span style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>{group.group_id}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Session</span>
                  <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--purple)' }}>{group.entry_session || 'UNKNOWN'}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Confluence Score</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <div style={{ width: '40px', height: '4px', background: 'rgba(255,255,255,0.1)', borderRadius: '2px', overflow: 'hidden' }}>
                      <div style={{ width: `${Math.min(100, Math.max(0, group.confluence_score || 0))}%`, height: '100%', background: (group.confluence_score || 0) >= 80 ? 'var(--green)' : (group.confluence_score || 0) >= 60 ? 'var(--blue)' : 'var(--yellow)' }} />
                    </div>
                    <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>{group.confluence_score || 0}/100</span>
                  </div>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Risk / TP Splits</span>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    {group.sub_trades?.map((st, idx) => (
                      <span key={idx} className={`badge badge-${(st.pnl || 0) > 0 ? 'green' : (st.pnl || 0) < 0 ? 'red' : 'secondary'}`} style={{ fontSize: '0.65rem' }}>
                        TP{st.tp_level || idx + 1}: {st.volume}L
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', flexGrow: 1, minWidth: 0 }}>
              {isLoadingChart ? (
                <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>Loading charts...</div>
              ) : (mergedGroup.chart_data?.length > 0 || mergedGroup.chart_data_m15?.length > 0 || mergedGroup.chart_data_m5?.length > 0) ? (
                <div style={{ background: 'rgba(0,0,0,0.1)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
                  <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'rgba(0,0,0,0.2)' }}>
                    {['M15', 'M5'].map(tf => {
                      const hasData = tf === 'M15' ? mergedGroup.chart_data_m15?.length > 0 : mergedGroup.chart_data_m5?.length > 0;
                      if (!hasData && !(tf === 'M5' && mergedGroup.chart_data?.length > 0)) return null;
                      return (
                        <button
                          key={tf}
                          onClick={() => setActiveChart(tf)}
                          style={{
                            padding: '8px 16px', background: 'none', border: 'none',
                            color: activeChart === tf ? 'var(--text-primary)' : 'var(--text-muted)',
                            fontWeight: activeChart === tf ? 600 : 400,
                            borderBottom: activeChart === tf ? '2px solid var(--blue)' : '2px solid transparent',
                            cursor: 'pointer', fontSize: '0.8rem'
                          }}
                        >
                          {tf === 'M15' ? 'HTF Context (M15)' : 'Entry (M5)'}
                        </button>
                      )
                    })}
                  </div>
                  <div style={{ padding: '8px' }}>
                    {activeChart === 'M15' && <TradeChart group={mergedGroup} timeframe="M15" height={400} />}
                    {activeChart === 'M5' && <TradeChart group={mergedGroup} timeframe="M5" height={400} />}
                  </div>
                </div>
              ) : mergedGroup.entry_snapshot_b64 ? (
                <>
                  <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>Entry Snapshot</div>
                  <img src={`data:image/png;base64,${mergedGroup.entry_snapshot_b64}`} alt="Chart" style={{ width: '100%', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }} />
                </>
              ) : null}
            </div>
          </div>
        </td></tr>
      )}
    </tbody>);
});

const BacktestResults = memo(function BacktestResults({ result, onSave, onDismiss, onClose, isSaving }) {
  const report = result.report || {};
  const grouped = result.grouped_trades || [];
  const eqData = (result.equity_curve || []).map((v, i) => ({ bar: i, equity: v }));
  const initialBalance = result.initial_balance || 10000;

  const [groupBy, setGroupBy] = useState('Month'); // Default to month
  const [viewMode, setViewMode] = useState('TRADES'); // 'TRADES' or 'SUMMARY'
  const [activeFilter, setActiveFilter] = useState('All');
  const [symbolFilter, setSymbolFilter] = useState('All');
  const [strategyFilter, setStrategyFilter] = useState('All');

  const uniqueSymbols = useMemo(() => {
    const s = new Set(grouped.map(g => g.symbol).filter(Boolean));
    return Array.from(s).sort();
  }, [grouped]);

  const uniqueStrategies = useMemo(() => {
    const s = new Set(grouped.map(g => g.strategy_id).filter(Boolean));
    return Array.from(s).sort();
  }, [grouped]);

  let filteredGrouped = grouped;
  if (activeFilter === 'Wins') filteredGrouped = filteredGrouped.filter(g => (g.net_pnl ?? g.combined_pnl ?? g.pnl) > 0);
  if (activeFilter === 'Losses') filteredGrouped = filteredGrouped.filter(g => (g.net_pnl ?? g.combined_pnl ?? g.pnl) <= 0);
  if (symbolFilter !== 'All') filteredGrouped = filteredGrouped.filter(g => g.symbol === symbolFilter);
  if (strategyFilter !== 'All') filteredGrouped = filteredGrouped.filter(g => g.strategy_id === strategyFilter);

  // Normalize grouped trades into the shape summaryEngine's math expects,
  // sorted chronologically (its stats functions assume this). Re-derived
  // whenever the symbol/strategy/result filter changes, so switching the
  // Symbol dropdown updates every metric below — not just the trade list —
  // to reflect only that symbol/strategy's own trades.
  const filteredNormalized = useMemo(() => {
    return [...filteredGrouped]
      .map(g => ({
        pnl: g.net_pnl ?? g.combined_pnl ?? g.pnl ?? 0,
        entry_time: g.entry_time_iso || g.entry_time,
        exit_time: g.exit_time_iso || g.exit_time,
        balance_before: g.balance_before,
        direction: g.direction,
        entry_price: g.entry_price,
        stop_loss: g.stop_loss,
        exit_price: g.exit_price,
        session: g.entry_session,
        symbol: g.symbol,
      }))
      .sort((a, b) => new Date(a.entry_time || 0) - new Date(b.entry_time || 0));
  }, [filteredGrouped]);

  const filteredStats = useMemo(
    () => summaryEngine.computePeriodStats(filteredNormalized, initialBalance),
    [filteredNormalized, initialBalance]
  );

  const filteredSessionData = useMemo(() => {
    const rates = summaryEngine.computeSessionStats(filteredNormalized);
    return [
      { session: 'London', rate: (rates.LONDON?.winRate || 0) * 100 },
      { session: 'NY', rate: (rates.NY?.winRate || 0) * 100 },
      { session: 'London/NY', rate: (rates.OVERLAP?.winRate || 0) * 100 },
      { session: 'Asian', rate: (rates.ASIAN?.winRate || 0) * 100 },
      { session: 'Other', rate: (rates.UNKNOWN?.winRate || 0) * 100 },
    ];
  }, [filteredNormalized]);

  let displayGroups = [];
  const displayGrouped = filteredGrouped; // Virtualized list handles large datasets efficiently

  if (groupBy === 'None') {
    displayGroups = [{ label: 'All Trades', trades: displayGrouped }];
  } else {
    const groupsMap = {};
    displayGrouped.forEach(g => {
      if (!g.entry_time_iso) return;
      const d = new Date(g.entry_time_iso);
      let key = '';
      if (groupBy === 'Day') key = d.toLocaleDateString();
      if (groupBy === 'Week') key = `Week ${getWeekNumber(d)}`;
      if (groupBy === 'Month') key = d.toLocaleString('default', { month: 'long', year: 'numeric' });
      if (groupBy === 'Year') key = d.getFullYear().toString();
      if (!groupsMap[key]) groupsMap[key] = [];
      groupsMap[key].push(g);
    });
    const sortedKeys = Object.keys(groupsMap).sort((a, b) => new Date(b) - new Date(a));
    displayGroups = sortedKeys.map(k => ({ label: k, trades: groupsMap[k] }));
  }

  function getWeekNumber(d) {
    if (!displayGrouped || displayGrouped.length === 0) return 1;
    const earliestTime = Math.min(...displayGrouped.map(tg => new Date(tg.entry_time_iso || 0).getTime()));
    return Math.floor((d.getTime() - earliestTime) / (7 * 24 * 60 * 60 * 1000)) + 1;
  }

  const summaryData = useMemo(() => {
    if (groupBy === 'None') return [];
    return displayGroups.map(group => {
      let startBal = null;
      let endBal = null;
      let isBreached = false;

      const sorted = [...group.trades].sort((a, b) => new Date(a.entry_time_iso || 0) - new Date(b.entry_time_iso || 0));
      sorted.forEach(t => {
        if (startBal === null) startBal = t.balance_before;
        endBal = t.balance_after;

        const dStr = (t.entry_time_iso || '').split('T')[0];
        if ((result.prop_firm_breach_days || []).includes(dStr)) isBreached = true;
      });

      // Reuse the exact same stats math as the top-level cards (and as
      // CumulativeSummary) so a period row's Max DD / Sharpe / Sortino /
      // Expectancy are computed consistently, not with a second formula.
      const normalized = sorted.map(t => ({
        pnl: t.net_pnl ?? t.combined_pnl ?? t.pnl ?? 0,
        entry_time: t.entry_time_iso || t.entry_time,
        exit_time: t.exit_time_iso || t.exit_time,
        balance_before: t.balance_before,
        direction: t.direction,
        entry_price: t.entry_price,
        stop_loss: t.stop_loss,
        exit_price: t.exit_price,
      }));
      const stats = summaryEngine.computePeriodStats(normalized, startBal ?? initialBalance, initialBalance);

      // TP-level / exit-reason breakdown across every leg in this period —
      // this is what the row's expand panel drills into.
      const tpBreakdown = {};
      sorted.forEach(g => {
        (g.sub_trades || []).forEach(st => {
          const reason = st.exit_reason || 'UNKNOWN';
          if (!tpBreakdown[reason]) tpBreakdown[reason] = { count: 0, wins: 0, pnl: 0 };
          tpBreakdown[reason].count++;
          tpBreakdown[reason].pnl += st.pnl || 0;
          if ((st.pnl || 0) > 0) tpBreakdown[reason].wins++;
        });
      });

      // Per-symbol breakdown within this period — only meaningful when
      // viewing multiple symbols at once (the Symbol filter is 'All').
      let symbolBreakdown = null;
      if (symbolFilter === 'All') {
        const bySymbol = {};
        sorted.forEach(t => {
          const sym = t.symbol || 'UNKNOWN';
          if (!bySymbol[sym]) bySymbol[sym] = [];
          bySymbol[sym].push(t);
        });
        symbolBreakdown = Object.entries(bySymbol).map(([sym, trades]) => {
          const symPnl = trades.reduce((a, t) => a + (t.net_pnl ?? t.combined_pnl ?? t.pnl ?? 0), 0);
          const symWins = trades.filter(t => (t.net_pnl ?? t.combined_pnl ?? t.pnl ?? 0) > 0).length;
          return { symbol: sym, trades: trades.length, wins: symWins, losses: trades.length - symWins, pnl: symPnl };
        }).sort((a, b) => b.trades - a.trades);
      }

      return {
        period: group.label,
        tradeCount: stats.totalGroups,
        startBal, endBal,
        pnl: stats.pnl,
        winRate: stats.winRate,
        maxDdPct: stats.maxDdPct,
        sharpe: stats.sharpe,
        sortino: stats.sortino,
        expectancyR: stats.expectancyR,
        avgDurationMin: stats.avgDurationMin,
        tpBreakdown,
        symbolBreakdown,
        isBreached
      };
    });
  }, [displayGroups, groupBy, symbolFilter, initialBalance]);

  const [expandedPeriods, setExpandedPeriods] = useState(new Set());
  const togglePeriod = (period) => {
    setExpandedPeriods(prev => {
      const next = new Set(prev);
      if (next.has(period)) next.delete(period); else next.add(period);
      return next;
    });
  };

  // Downsample equity curve to prevent Recharts from freezing the browser
  let chartEqData = eqData;
  if (eqData.length > 500) {
    const step = Math.ceil(eqData.length / 500);
    chartEqData = eqData.filter((_, i) => i % step === 0 || i === eqData.length - 1);
  }

  return (<div className="card" style={{ marginTop: 20 }}>
    <div className="card-header">
      <span className="card-title">Results — {result.grouped_trades?.length || 0} signals, {result.total_trades || 0} sub-positions</span>
      <div style={{ display: 'flex', gap: 8 }}>
        {!result.is_saved ? (
          <>
            <button className="btn btn-primary btn-sm" onClick={onSave} disabled={isSaving}><Save size={14} /> {isSaving ? 'Saving...' : 'Save'}</button>
            <button className="btn btn-danger btn-sm" onClick={onDismiss}><X size={14} /> Dismiss</button>
          </>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={onClose}><X size={14} /> Close</button>
        )}
      </div>
    </div>
    {result.invalid_signals > 0 && <div style={{ fontSize: '0.8rem', color: 'var(--yellow)', marginBottom: 8 }}><Shield size={12} style={{ display: 'inline', marginRight: 4 }} />{result.invalid_signals} signals rejected (invalid SL/TP)</div>}

    {result.notes && (
      <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)' }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--blue)', marginBottom: 4 }}>Narration / Notes</div>
        <div style={{ fontSize: '0.85rem', whiteSpace: 'pre-wrap' }}>{result.notes}</div>
      </div>
    )}

    {result.params_snapshot && Object.keys(result.params_snapshot).length > 0 && (
      <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)' }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--purple)', marginBottom: 4 }}>Configuration Parameters</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 4 }}>
          {Object.entries(result.params_snapshot).map(([k, v]) => (
            <div key={k} style={{ fontSize: '0.75rem', display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-muted)' }}>{k}:</span>
              <span style={{ textAlign: 'right', wordBreak: 'break-all', maxWidth: '70%' }}>
                {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}
              </span>
            </div>
          ))}
        </div>
      </div>
    )}

    {report.rejection_funnel && Object.keys(report.rejection_funnel).length > 0 && (
      <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)' }}>
        <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8, display: 'flex', alignItems: 'center' }}>
          <Shield size={14} style={{ marginRight: 6, color: 'var(--blue)' }} /> Signal Rejection Funnel
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Overview</div>
            <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span>Total Evaluated:</span> <strong>{report.rejection_funnel.total_evaluated}</strong></div>
            <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span>Approved:</span> <strong style={{ color: 'var(--green)' }}>{report.rejection_funnel.approved}</strong></div>
            <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span>Errors:</span> <strong style={{ color: report.rejection_funnel.errors > 0 ? 'var(--red)' : 'var(--text-primary)' }}>{report.rejection_funnel.errors}</strong></div>
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Rejection Breakdown</div>
            <div style={{ maxHeight: 100, overflowY: 'auto', paddingRight: 4 }}>
              {Object.entries(report.rejection_funnel.strategy_rejections || {}).map(([reason, count]) => (
                <div key={reason} style={{ fontSize: '0.75rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2, borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '80%' }} title={reason}>{reason}</span>
                  <span style={{ color: 'var(--yellow)' }}>{count}</span>
                </div>
              ))}
              {Object.entries(report.rejection_funnel.risk_rejections || {}).map(([reason, count]) => (
                <div key={reason} style={{ fontSize: '0.75rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2, borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '80%' }} title={reason}>Risk: {reason}</span>
                  <span style={{ color: 'var(--yellow)' }}>{count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    )}

    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 16 }}>
      <MetricCard title="Final Balance" value={`$${(initialBalance + filteredStats.pnl).toFixed(2)}`} color={filteredStats.pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
      <MetricCard title="Net P&L" value={`$${filteredStats.pnl.toFixed(2)}`} color={filteredStats.pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
      <MetricCard title="Win Rate" value={`${(filteredStats.winRate * 100).toFixed(1)}%`} color={filteredStats.winRate >= 0.5 ? 'var(--green)' : 'var(--red)'} />
      <MetricCard title="Profit Factor" value={filteredStats.profitFactor >= 999 ? '∞' : filteredStats.profitFactor.toFixed(2)} />
      <MetricCard title="Sharpe" value={filteredStats.sharpe.toFixed(2)} />
      <MetricCard title="Max DD" value={`${(filteredStats.maxDdPct * 100).toFixed(1)}%`} color="var(--red)" />
      <MetricCard title="Expectancy (R)" value={filteredStats.expectancyR.toFixed(2)} />
      <MetricCard title="Sortino" value={filteredStats.sortino >= 999 ? '∞' : filteredStats.sortino.toFixed(2)} />
    </div>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
      {[1, 2, 3, 4, 5].map(n => { const rate = report[`tp${n}_hit_rate`]; return rate != null && rate > 0 ? <div key={n} className="badge badge-green" style={{ padding: '4px 10px', fontSize: '0.75rem' }}>TP{n}: {(rate * 100).toFixed(0)}%</div> : null; })}
      {report.sl_hit_rate != null && <div className="badge badge-red" style={{ padding: '4px 10px', fontSize: '0.75rem' }}>SL: {(report.sl_hit_rate * 100).toFixed(0)}%</div>}
      {report.trail_hit_rate != null && report.trail_hit_rate > 0 && <div className="badge badge-blue" style={{ padding: '4px 10px', fontSize: '0.75rem' }}>Trail Exit: {(report.trail_hit_rate * 100).toFixed(0)}%</div>}
    </div>
    <div className="grid-2" style={{ marginBottom: 16 }}>
      {eqData.length > 1 && (<div className="card" style={{ padding: 12 }}><h4 style={{ marginBottom: 8 }}>Equity Curve</h4>
        <ResponsiveContainer width="100%" height={200}><AreaChart data={chartEqData}><XAxis dataKey="bar" hide /><YAxis domain={['auto', 'auto']} fontSize={10} /><Tooltip formatter={v => `$${v.toFixed(2)}`} /><Area type="monotone" dataKey="equity" stroke="#3fb68b" fill="#3fb68b20" strokeWidth={2} /></AreaChart></ResponsiveContainer>
      </div>)}
      <div className="card" style={{ padding: 12 }}><h4 style={{ marginBottom: 8 }}>Win Rate by Session</h4>
        <ResponsiveContainer width="100%" height={200}><BarChart data={filteredSessionData}><XAxis dataKey="session" fontSize={11} /><YAxis domain={[0, 100]} fontSize={10} /><Tooltip formatter={v => `${v.toFixed(1)}%`} /><Bar dataKey="rate" fill="#58a6ff" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer>
      </div>
    </div>
    {(report.confluence_stats || report.bias_stats) && (
      <div className="grid-3" style={{ marginBottom: 16 }}>
        <div className="card" style={{ padding: 12 }}>
          <h4 style={{ marginBottom: 8 }}>Win Rate by Score</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {Object.entries(report.confluence_stats.by_score || {}).sort((a, b) => Number(b[0]) - Number(a[0])).map(([score, data]) => (
              <div key={score} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                <span>Score {score}</span>
                <span style={{ color: data.win_rate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>
                  {(data.win_rate * 100).toFixed(1)}% ({data.wins}/{data.trades})
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="card" style={{ padding: 12 }}>
          <h4 style={{ marginBottom: 8 }}>Win Rate by Confirmation</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {Object.entries(report.confluence_stats.by_confirmation || {}).sort((a, b) => b[1].trades - a[1].trades).map(([conf, data]) => (
              <div key={conf} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                <span style={{ textTransform: 'capitalize' }}>{conf.replace('_', ' ')}</span>
                <span style={{ color: data.win_rate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>
                  {(data.win_rate * 100).toFixed(1)}% ({data.wins}/{data.trades})
                </span>
              </div>
            ))}
          </div>
        </div>
        {report.bias_stats && (
          <div className="card" style={{ padding: 12 }}>
            <h4 style={{ marginBottom: 8 }}>Win Rate by Bias</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {Object.entries(report.bias_stats).map(([bias, data]) => (
                <div key={bias} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                  <span style={{ textTransform: 'capitalize' }}>{bias}</span>
                  <span style={{ color: data.win_rate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>
                    {(data.win_rate * 100).toFixed(1)}% ({data.wins}/{data.trades})
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    )}
    {grouped.length > 0 && (<>
      <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <span className="card-title">Trade Groups ({filteredGrouped.length})</span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          {uniqueSymbols.length > 1 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <label style={{ fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-muted)', fontWeight: 600 }}>Symbol</label>
              <select value={symbolFilter} onChange={e => setSymbolFilter(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}>
                <option value="All">All Symbols ({uniqueSymbols.length})</option>
                {uniqueSymbols.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          )}
          {uniqueStrategies.length > 1 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <label style={{ fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-muted)', fontWeight: 600 }}>Strategy</label>
              <select value={strategyFilter} onChange={e => setStrategyFilter(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}>
                <option value="All">All Strategies ({uniqueStrategies.length})</option>
                {uniqueStrategies.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          )}
          {(symbolFilter !== 'All' || strategyFilter !== 'All' || activeFilter !== 'All') && (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => { setSymbolFilter('All'); setStrategyFilter('All'); setActiveFilter('All'); }}
              style={{ fontSize: '0.75rem' }}
              title="Clear all filters"
            >
              <X size={12} /> Clear
            </button>
          )}
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--border)', margin: '0 2px' }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <label style={{ fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-muted)', fontWeight: 600 }}>Result</label>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className={`btn btn-sm ${activeFilter === 'All' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setActiveFilter('All')}>All</button>
              <button className={`btn btn-sm ${activeFilter === 'Wins' ? 'btn-green' : 'btn-secondary'}`} onClick={() => setActiveFilter('Wins')}>Wins</button>
              <button className={`btn btn-sm ${activeFilter === 'Losses' ? 'btn-red' : 'btn-secondary'}`} onClick={() => setActiveFilter('Losses')}>Losses</button>
            </div>
          </div>
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--border)', margin: '0 2px' }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <label style={{ fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-muted)', fontWeight: 600 }}>View</label>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className={`btn btn-sm ${viewMode === 'TRADES' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setViewMode('TRADES')}>List</button>
              <button className={`btn btn-sm ${viewMode === 'SUMMARY' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => { setViewMode('SUMMARY'); if (groupBy === 'None') setGroupBy('Month'); }}>Summary</button>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <label style={{ fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.03em', color: 'var(--text-muted)', fontWeight: 600 }}>Group By</label>
            <select value={groupBy} onChange={e => setGroupBy(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}>
              <option value="None">No Grouping</option>
              <option value="Day">Day</option>
              <option value="Week">Week</option>
              <option value="Month">Month</option>
              <option value="Year">Year</option>
            </select>
          </div>
        </div>
      </div>
      {viewMode === 'SUMMARY' ? (
        <div className="table-wrapper" style={{ maxHeight: 600, overflow: 'auto' }}>
          <table>
            <thead style={{ position: 'sticky', top: 0, zIndex: 1, background: 'var(--bg-secondary)' }}>
              <tr>
                <th style={{ width: 24 }}></th>
                <th>Period</th>
                <th>Trades</th>
                <th>Win Rate</th>
                <th>Starting Balance</th>
                <th>Ending Balance</th>
                <th>Period P&L</th>
                <th>Max DD %</th>
                <th>Sharpe</th>
                <th>Sortino</th>
                <th>Expectancy (R)</th>
              </tr>
            </thead>
            <tbody>
              {summaryData.length > 0 ? summaryData.map((row, i) => {
                const isExpanded = expandedPeriods.has(row.period);
                const tpEntries = Object.entries(row.tpBreakdown || {}).sort((a, b) => b[1].count - a[1].count);
                return (
                  <React.Fragment key={row.period || i}>
                    <tr onClick={() => togglePeriod(row.period)} style={{ cursor: 'pointer', background: row.isBreached ? 'rgba(248, 81, 73, 0.1)' : (isExpanded ? 'rgba(255,255,255,0.03)' : 'transparent'), borderLeft: row.isBreached ? '3px solid var(--red)' : 'none' }}>
                      <td style={{ color: 'var(--text-muted)' }}>{isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                      <td>
                        <strong>{row.period}</strong>
                        {row.isBreached && <span style={{ marginLeft: 8, fontSize: '0.65rem', background: 'var(--red)', color: '#fff', padding: '2px 6px', borderRadius: 4, textTransform: 'uppercase' }}>Prop Firm Breach</span>}
                      </td>
                      <td>{row.tradeCount}</td>
                      <td style={{ color: row.winRate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>{(row.winRate * 100).toFixed(1)}%</td>
                      <td>${row.startBal != null ? row.startBal.toFixed(2) : '—'}</td>
                      <td>${row.endBal != null ? row.endBal.toFixed(2) : '—'}</td>
                      <td style={{ color: row.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                        ${row.pnl.toFixed(2)}
                      </td>
                      <td style={{ color: 'var(--red)' }}>{(row.maxDdPct * 100).toFixed(2)}%</td>
                      <td>{row.sharpe.toFixed(2)}</td>
                      <td>{row.sortino >= 999 ? '∞' : row.sortino.toFixed(2)}</td>
                      <td style={{ color: row.expectancyR > 0 ? 'var(--green)' : 'var(--red)' }}>{row.expectancyR.toFixed(2)}</td>
                    </tr>
                    {isExpanded && (
                      <tr style={{ background: 'var(--bg-tertiary)' }}>
                        <td></td>
                        <td colSpan={10} style={{ padding: '12px 16px' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: row.symbolBreakdown ? '1fr 1fr' : '1fr', gap: 20 }}>
                            <div>
                              <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 6 }}>
                                By TP / Exit Reason
                              </div>
                              {tpEntries.length > 0 ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                  {tpEntries.map(([reason, d]) => (
                                    <div key={reason} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                                      <span>{reason} <span style={{ color: 'var(--text-muted)' }}>({d.count})</span></span>
                                      <span>
                                        <span style={{ color: d.wins / d.count >= 0.5 ? 'var(--green)' : 'var(--red)', marginRight: 8 }}>
                                          {((d.wins / d.count) * 100).toFixed(0)}% WR
                                        </span>
                                        <span style={{ color: d.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${d.pnl.toFixed(2)}</span>
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              ) : <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>No leg-level data</div>}
                              <div style={{ marginTop: 10, fontSize: '0.75rem', color: 'var(--text-muted)' }}>Avg Duration: {fmtDur(row.avgDurationMin)}</div>
                            </div>
                            {row.symbolBreakdown && (
                              <div>
                                <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 6 }}>
                                  By Symbol
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                  {row.symbolBreakdown.map(s => (
                                    <div key={s.symbol} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                                      <span>{s.symbol} <span style={{ color: 'var(--text-muted)' }}>({s.trades})</span></span>
                                      <span>
                                        <span style={{ color: 'var(--text-muted)', marginRight: 8 }}>{s.wins}W / {s.losses}L</span>
                                        <span style={{ color: s.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${s.pnl.toFixed(2)}</span>
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              }) : (
                <tr><td colSpan={11} style={{ textAlign: 'center', padding: '40px 0' }}>No summary data available. Select a grouping mode.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <VirtualizedTradeList displayGroups={displayGroups} groupBy={groupBy} backtestId={result.id} />
      )}
    </>)}
  </div>);
});

const TRAIL_METHODS = [{ v: 'NONE', l: 'None' }, { v: 'ATR_TRAIL', l: 'ATR Trail' }, { v: 'FIXED_PIPS', l: 'Fixed Pips' }, { v: 'STRUCTURE_TRAIL', l: 'Structure Trail' }, { v: 'PCT_TRAIL', l: '% Trail' }];

const VirtualizedTradeList = memo(function VirtualizedTradeList({ displayGroups, groupBy, backtestId }) {
  const parentRef = useRef(null);

  const flattenedRows = useMemo(() => {
    const arr = [];
    displayGroups.forEach((grouping, gIdx) => {
      if (groupBy !== 'None') {
        arr.push({ type: 'header', label: grouping.label, count: grouping.trades.length, isFirst: gIdx === 0 });
      }
      grouping.trades.forEach((g, i) => {
        arr.push({ type: 'trade', group: g, index: i });
      });
    });
    return arr;
  }, [displayGroups, groupBy]);

  const rowVirtualizer = useVirtualizer({
    count: flattenedRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => flattenedRows[index].type === 'header' ? 35 : 42,
  });

  const virtualItems = rowVirtualizer.getVirtualItems();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom = virtualItems.length > 0 ? rowVirtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end : 0;

  return (
    <div className="table-wrapper" ref={parentRef} style={{ maxHeight: 500, overflow: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={{ width: 24 }}></th>
            <th>#</th>
            <th>Symbol</th>
            <th>Dir</th>
            <th>Entry</th>
            <th>Entry Time</th>
            <th>Exit Time</th>
            <th>Duration</th>
            <th>TPs</th>
            <th>Net P&L</th>
            <th>Bal. Before</th>
            <th>Bal. After</th>
            <th>Session</th>
          </tr>
        </thead>
        {paddingTop > 0 && <tbody><tr><td colSpan={13} style={{ height: paddingTop, padding: 0, border: 'none' }} /></tr></tbody>}
        {virtualItems.map((virtualRow) => {
          const row = flattenedRows[virtualRow.index];
          if (row.type === 'header') {
            return (
              <tbody key={virtualRow.index} ref={rowVirtualizer.measureElement} data-index={virtualRow.index}>
                <tr>
                  <td colSpan={13} style={{ padding: '8px 12px', background: 'var(--bg-tertiary)', fontWeight: 600, fontSize: '0.85rem', color: 'var(--text-primary)' }}>
                    {row.label} ({row.count})
                  </td>
                </tr>
              </tbody>
            );
          }
          return <GroupedTradeRow key={virtualRow.index} group={row.group} index={row.index} measureRef={rowVirtualizer.measureElement} vIndex={virtualRow.index} backtestId={backtestId} />;
        })}
        {paddingBottom > 0 && <tbody><tr><td colSpan={13} style={{ height: paddingBottom, padding: 0, border: 'none' }} /></tr></tbody>}
      </table>
    </div>
  );
});

export default function Backtester() {
  const queryClient = useQueryClient();
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s => s.isAuthenticated);
  const [activeTab, setActiveTab] = useState('single'); // 'single' | 'portfolio'
  const STORAGE_KEY = 'algoedge_bt_config';
  const PORTFOLIO_KEY = 'algoedge_portfolio_config';

  // ── Portfolio state ──
  const [portfolioSymbols, setPortfolioSymbols] = useState(() => {
    try {
      const saved = localStorage.getItem(PORTFOLIO_KEY);
      if (saved) return JSON.parse(saved);
    } catch { }
    return [{ symbol: 'XAUUSD', strategy_id: 'APA_v1' }];
  });

  useEffect(() => {
    try { localStorage.setItem(PORTFOLIO_KEY, JSON.stringify(portfolioSymbols)); } catch { }
  }, [portfolioSymbols]);

  const addPortfolioSymbol = () => setPortfolioSymbols(p => [...p, { symbol: 'EURUSD', strategy_id: 'APA_v1' }]);
  const removePortfolioSymbol = (i) => setPortfolioSymbols(p => p.filter((_, idx) => idx !== i));
  const updatePortfolioSymbol = (i, field, val) => setPortfolioSymbols(p => p.map((s, idx) => idx === i ? { ...s, [field]: val } : s));

  const portfolioMutation = useMutation({
    mutationFn: () => {
      setResult(null);
      setEvents([]);
      setBtError(null);
      return runPortfolioBacktest({
        symbols: portfolioSymbols.map(s => {
          let sp = {};
          if (s.strategy_id === 'APA_v1') sp = form.smc || {};
          if (s.strategy_id === 'DriftJumpAlpha_v1') sp = form.drift_jump_alpha || {};
          if (s.strategy_id === 'CRT_v1') sp = form.crt || {};
          return { symbol: s.symbol, strategy_id: s.strategy_id, strategy_params: sp };
        }),
        start_date: form.start_date || undefined,
        end_date: form.end_date || undefined,
        candle_count: form.candle_count,
        initial_balance: form.initial_balance,
        risk_per_trade_pct: form.risk_per_trade_pct,
        min_rr: form.min_rr,
        tp_count: form.tp_count,
        tp1_rr: form.tp1_rr, tp2_rr: form.tp2_rr, tp3_rr: form.tp3_rr,
        tp4_rr: form.tp4_rr, tp5_rr: form.tp5_rr,
        tp_splits: form.tp_splits,
        be_trigger_rr: form.be_trigger_rr,
        be_buffer_pips: form.be_buffer_pips,
        trail_method_tp2: form.trail_method_tp2,
        trail_method_tp3: form.trail_method_tp3,
        trail_method_tp4: form.trail_method_tp4,
        trail_method_tp5: form.trail_method_tp5,
        atr_trail_multiplier: form.atr_trail_multiplier,
        trail_pips: form.trail_pips,
        session_filter_enabled: form.session_filter_enabled,
        prop_firm: form.prop_firm,
        max_risk_hard_cap_pct: form.max_risk_hard_cap_pct ?? 3.0,

        max_concurrent_positions: form.max_concurrent_positions,
        max_daily_drawdown_pct: form.max_daily_drawdown_pct,
        max_weekly_drawdown_pct: form.max_weekly_drawdown_pct,
        max_positions_per_symbol: form.max_positions_per_symbol || 1,
        max_daily_trades: form.max_daily_trades || 5,
        target_profit_enabled: form.target_profit_enabled,
        max_daily_profit: form.max_daily_profit,
        max_weekly_profit: form.max_weekly_profit,
        // Simulation costs — must match single-symbol call so portfolio and
        // single results are comparable when the same settings are used.
        slippage_pips: form.slippage_pips ?? 0.0,
        commission_per_lot: form.commission_per_lot ?? 0.0,
        spread_pips: form.spread_pips ?? 0.0,
        simulate_wicks: form.simulate_wicks ?? true,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtests'] }),
  });


  const { data: remoteConfig } = useQuery({
    queryKey: ['config'],
    queryFn: () => getConfig().then(r => r.data),
    enabled: status === 'ONLINE' && isAuth,
  });

  const backtestSymbols = useMemo(() => {
    if (!remoteConfig?.config) return SYMBOLS;
    const cfg = remoteConfig.config;
    const configured = cfg.instrument_settings ? cfg.instrument_settings.map(i => i.symbol) : (cfg.symbols || []);
    return [...new Set([...SYMBOLS, ...configured])];
  }, [remoteConfig]);

  const [form, setForm] = useState(() => {
    // Restore last saved config from localStorage
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) return JSON.parse(saved);
    } catch { }
    return {
      symbol: 'XAUUSD', initial_balance: 10000,
      start_date: '', end_date: '', candle_count: 5000,
      max_risk_hard_cap_pct: 3.0,
      smc: {
        confluence_threshold: 60, swing_length_htf: 5, swing_length_ltf: 3, ob_impulse_ratio: 2.0,
        fvg_min_gap_pips: 5.0, liq_sweep_min_pips: 5.0, max_spread_pips: 3.0,
        session_filter_enabled: true, news_filter_enabled: true,
        enforce_htf_pd: true, enforce_fvg_displacement: false, enforce_asian_range_sweep: false,
      },
      drift_jump_alpha: {
        spike_lookback_bars: 50, drift_ema_fast: 20, drift_ema_slow: 50, min_adx_to_trade: 20,
        jump_entry_percentile_threshold: 95.0, trade_jumps_enabled: false, control_test_passed: false, aggregate_max_lots_per_symbol: 6.0,
      },
      crt: {
        htf_timeframe: '1H', ltf_timeframe: 'M1', target_r_multiple: 1.5, max_trades_per_session: 1, session_start: '09:30', session_cutoff: '12:00', bypass_session_synthetics: true,
      },
      // Keep old fields just in case
      confluence_threshold: 55, swing_length: 5, ob_impulse_ratio: 1.5,
      fvg_min_gap_pips: 3.0, liq_sweep_min_pips: 2.0, max_spread_pips: 3.0,
      session_filter_enabled: true, news_filter_enabled: true,
      risk_per_trade_pct: 1.0, min_rr: 3.0,
      max_daily_drawdown_pct: 3.0, max_weekly_drawdown_pct: 6.0,
      max_concurrent_positions: 3, max_daily_trades: 5,
      tp_count: 3, tp1_rr: 1.0, tp2_rr: 3.0, tp3_rr: 5.0, tp4_rr: 10.0, tp5_rr: 15.0,
      tp_splits: '30,25,20,15,10',
      be_trigger_rr: 1.0, be_buffer_pips: 2.0,
      trail_method_tp1: 'NONE', trail_method_tp2: 'ATR_TRAIL', trail_method_tp3: 'STRUCTURE_TRAIL',
      trail_method_tp4: 'NONE', trail_method_tp5: 'NONE',
      atr_trail_multiplier: 1.5, trail_pips: 15,
      compounding_enabled: false,
      prop_firm: {
        account_mode: 'personal',
        challenge_type: 'none',
        account_size: 10000.0,
        initial_balance: 10000.0,
        max_lot_sizes: {}
      },
      target_profit_enabled: false, max_daily_profit: 500.0, max_weekly_profit: 2000.0,
      manual_bias: 'NONE',
      strategy_id: 'APA_v1',
      drift_ema_fast: 20, drift_ema_slow: 50, min_adx_to_trade: 20, jump_entry_percentile_threshold: 95.0, trade_jumps_enabled: false, control_test_passed: false, aggregate_max_lots_per_symbol: 6.0,
      htf_timeframe: 'H1', ltf_timeframe: 'M5', target_r_multiple: 1.5, max_trades_per_session: 1, session_start: '09:30', session_cutoff: '12:00', bypass_session_synthetics: true,
      entry_confirmation_tf: 'M5', target_rr: 1.0, require_unfilled_htf_fvg: true,
      stop_method: 'swing_high_low', target_rr_range_min: 1.0, target_rr_range_max: 3.0, max_trades_per_day: 2,
      range_window_start: '08:00', range_window_end: '08:15', earliest_valid_break_time: '09:30', session_end: '11:00', stop_buffer_points: 5.0, fixed_target_points: 15.0, dynamic_target_override: true,
      // Simulation Costs (BUG-8/BUG-9)
      slippage_pips: 0.0, commission_per_lot: 0.0, spread_pips: 0.0, simulate_wicks: true,
      vwap_sl_points: 80.0,
    };
  });

  const RESULT_STORAGE_KEY = 'algoedge_bt_result';
  const [result, setResult] = useState(() => {
    try {
      const saved = localStorage.getItem(RESULT_STORAGE_KEY);
      if (saved) return JSON.parse(saved);
    } catch { }
    return null;
  });

  const [events, setEvents] = useState([]);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const resultsRef = useRef(null);

  useEffect(() => {
    if (result && resultsRef.current) {
      setTimeout(() => {
        resultsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 100);
    }
  }, [result]);

  const [isSaving, setIsSaving] = useState(false);
  const [progress, setProgress] = useState(null);
  // Surfaces backend-side failures (the 'backtest_error' WS event / polled
  // status:'error') directly in this page. Previously these were only
  // logged to the persistent dashboard activity log via
  // bot_service.log_system_event — nothing here ever cleared `progress`
  // or showed a message, so a failed run just left the UI stuck on the
  // last "running" stage forever with no visible error.
  const [btError, setBtError] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [savedBtFilter, setSavedBtFilter] = useState('All');
  const [savedBtSort, setSavedBtSort] = useState('Date');
  const [configLoaded, setConfigLoaded] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);

  // Auto-save config to localStorage on every change (debounced)
  useEffect(() => {
    const timer = setTimeout(() => {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(form)); } catch { }
    }, 500);
    return () => clearTimeout(timer);
  }, [form]);

  // Auto-save result to localStorage (debounced)
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        if (result) {
          localStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(result));
        } else {
          localStorage.removeItem(RESULT_STORAGE_KEY);
        }
      } catch (e) {
        // If quota exceeded (run_logs can be large), save without logs
        try {
          const trimmed = { ...result, run_logs: [] };
          localStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(trimmed));
        } catch { }
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [result]);

  // Load defaults from user's live config (only on first load if no saved config)
  const { data: userCfg } = useQuery({ queryKey: ['config'], queryFn: () => getConfig().then(r => r.data), enabled: status === 'ONLINE' && isAuth });
  useEffect(() => {
      if (userCfg?.config && !configLoaded) {
        const c = userCfg.config;
        setForm(prev => {
          const merged = { ...prev };
          merged.max_risk_hard_cap_pct = prev.max_risk_hard_cap_pct ?? c.risk?.max_risk_hard_cap_pct ?? 3.0;
          merged.prop_firm = { ...(c.prop_firm || {}), ...(prev.prop_firm || {}) };
          merged.smc = { ...(c.apa || {}), ...(prev.smc || {}) };
          merged.drift_jump_alpha = { ...(c.drift_jump_alpha || {}), ...(prev.drift_jump_alpha || {}) };
          merged.crt = { ...(c.crt || {}), ...(prev.crt || {}) };
          merged.vwap = { ...(c.vwap || {}), ...(prev.vwap || {}) };
          merged.compounding_enabled = prev.compounding_enabled ?? c.compounding?.compounding_enabled;
          
          return merged;
        });
      }
      if (userCfg) setConfigLoaded(true);
  }, [userCfg, configLoaded]);

  useEffect(() => {
    const h = e => {
      try {
        const m = e.detail; // 'ws-message' event from useBackendConnection.js passes parsed data in detail
        if (m.type === 'backtest_progress') {
          setProgress(m);
          if (m.stage === 'complete') {
            if (m.result) {
              setResult(m.result);
              if (m.result.run_logs) setEvents(m.result.run_logs);
            }
            setTimeout(() => setProgress(null), 2000);
          }
        } else if (m.type === 'backtest_error') {
          // Backend task failed (e.g. an exception inside the engine) —
          // the request itself already returned "started" successfully,
          // so mutation.isError never fires for this. Clear progress so
          // isRunning drops back to false and show the failure message.
          setProgress(null);
          setBtError(m.message || 'Backtest failed. Check the activity log for details.');
        }
      } catch { }
    };
    window.addEventListener('ws-message', h);
    return () => window.removeEventListener('ws-message', h);
  }, []);

  // Fetch backend status on mount or reconnect
  useEffect(() => {
    if (status === 'ONLINE' && isAuth) {
      getBacktestStatus().then(res => {
        if (res.data.status === 'running') {
          setProgress(res.data.progress || { stage: 'Restoring...', pct: 0 });
        } else if (res.data.status === 'error') {
          setProgress(null);
          setBtError(res.data.progress?.message || 'Backtest failed. Check the activity log for details.');
        } else if (res.data.status === 'complete' && !result) {
          setIsLoadingDetail(true);
          getLatestBacktestResult().then(r => {
            if (r.data && Object.keys(r.data).length > 0) {
              setResult(r.data);
              if (r.data.run_logs) setEvents(r.data.run_logs);
            }
          }).catch(() => { })
            .finally(() => setIsLoadingDetail(false));
        }
      }).catch(() => { });
    }
  }, [status, isAuth]);

  const { data: backtests, refetch } = useQuery({ queryKey: ['backtests'], queryFn: () => getBacktests().then(r => r.data), enabled: status === 'ONLINE' && isAuth });

  const mutation = useMutation({
    mutationFn: () => {
      setResult(null);
      setEvents([]);
      setBtError(null);
      const validStrats = ['APA_v1', 'VWAP_v1', 'DriftJumpAlpha_v1', 'CRT_v1', 'HTFFVGFlip_v1', 'BiasIFVG_v1', 'NYOpenRetest_v1'];
      const payload_strategy = validStrats.includes(form.strategy_id) ? form.strategy_id : 'APA_v1';
      let sp = {};
      if (payload_strategy === 'APA_v1') sp = form.smc || {};
      if (payload_strategy === 'DriftJumpAlpha_v1') sp = form.drift_jump_alpha || {};
      if (payload_strategy === 'CRT_v1') sp = form.crt || {};

      const payload = { ...form, strategy_id: payload_strategy, start_date: form.start_date || undefined, end_date: form.end_date || undefined, risk_config: { prop_firm: form.prop_firm }, max_risk_hard_cap_pct: form.max_risk_hard_cap_pct ?? 3.0, strategy_params: sp };
      if (form.manual_bias && form.manual_bias !== 'NONE') {
        payload.manual_bias_overrides = { [form.symbol]: form.manual_bias };
      }

      if (payload_strategy === 'APA_v1') {
        payload.strategy_params = {
          structure_timeframe: form.structure_timeframe || "M15",
          entry_timeframe: form.entry_timeframe || "M5",
          minor_fractal_m: form.minor_fractal_m || 3,
          major_fractal_n: form.major_fractal_n || 5,
          enforce_strong_choch: form.enforce_strong_choch !== undefined ? form.enforce_strong_choch : true
        };
      } else if (form.strategy_id === 'VWAP_v1') {
        payload.strategy_params = {
          vwap_anchor_minutes: form.vwap_anchor_minutes || 15,
          momentum_lookback_bars: form.momentum_lookback_bars || 4,
          momentum_threshold_pct: form.momentum_threshold_pct || 0.1,
          sl_points: form.sl_points || 80.0,
          sl_atr_multiplier: form.vwap_sl_atr_multiplier ?? 0,
        };
      } else if (form.strategy_id === 'DriftJumpAlpha_v1') {
        payload.strategy_params = {
          drift_ema_fast: form.drift_ema_fast,
          drift_ema_slow: form.drift_ema_slow,
          min_adx_to_trade: form.min_adx_to_trade,
          jump_entry_percentile_threshold: form.jump_entry_percentile_threshold,
          trade_jumps_enabled: form.trade_jumps_enabled,
          control_test_passed: form.control_test_passed,
          aggregate_max_lots_per_symbol: form.aggregate_max_lots_per_symbol
        };
      } else if (form.strategy_id === 'CRT_v1') {
        payload.strategy_params = {
          htf_timeframe: form.htf_timeframe,
          ltf_timeframe: form.ltf_timeframe,
          target_r_multiple: form.target_r_multiple,
          max_trades_per_session: form.max_trades_per_session,
          session_start: form.session_start,
          session_cutoff: form.session_cutoff,
          bypass_session_synthetics: form.bypass_session_synthetics
        };
      } else if (form.strategy_id === 'HTFFVGFlip_v1') {
        payload.strategy_params = {
          htf_timeframe: form.htf_timeframe,
          entry_confirmation_tf: form.entry_confirmation_tf,
          target_rr: form.target_rr,
          require_unfilled_htf_fvg: form.require_unfilled_htf_fvg,
          session_filter_enabled: form.session_filter_enabled,
          session_start: form.session_start,
          session_cutoff: form.session_cutoff
        };
      } else if (form.strategy_id === 'BiasIFVG_v1') {
        payload.strategy_params = {
          stop_method: form.stop_method,
          target_rr_range_min: form.target_rr_range_min,
          target_rr_range_max: form.target_rr_range_max,
          max_trades_per_day: form.max_trades_per_day,
          session_start: form.session_start,
          session_cutoff: form.session_cutoff
        };
      } else if (form.strategy_id === 'NYOpenRetest_v1') {
        payload.strategy_params = {
          range_window_start: form.range_window_start,
          range_window_end: form.range_window_end,
          earliest_valid_break_time: form.earliest_valid_break_time,
          session_end: form.session_end,
          stop_buffer_points: form.stop_buffer_points,
          fixed_target_points: form.fixed_target_points,
          dynamic_target_override: form.dynamic_target_override
        };
      }

      // Add simulation costs to every backtest payload (BUG-8/BUG-9)
      payload.slippage_pips = form.slippage_pips ?? 0.0;
      payload.commission_per_lot = form.commission_per_lot ?? 0.0;
      payload.spread_pips = form.spread_pips ?? 0.0;
      payload.simulate_wicks = form.simulate_wicks ?? true;

      return runBacktest(payload);
    },
    onSuccess: res => {
      // Background task started, result will be pushed via WebSocket or polling
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
    }
  });

  const handleSave = () => {
    if (!result) return;
    setShowSaveModal(true);
  };

  const handleSaveSuccess = () => {
    setShowSaveModal(false);
    setResult(null);
    localStorage.removeItem(RESULT_STORAGE_KEY);
    queryClient.invalidateQueries({ queryKey: ['backtests'] });
    refetch();
    setTimeout(() => {
      document.getElementById('saved-backtests')?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
  };

  const handleDismiss = () => {
    setResult(null);
    localStorage.removeItem(RESULT_STORAGE_KEY);
  };
  const handleDelete = async id => { await deleteBacktest(id); refetch(); };
  const handleView = async id => {
    setIsLoadingDetail(true);
    // Scroll immediately to show loader
    setTimeout(() => {
      if (resultsRef.current) resultsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);

    try {
      const res = await getBacktest(id);
      setResult({ ...res.data.run, trades: res.data.trades, equity_curve: res.data.equity_curve, report: res.data.run, grouped_trades: res.data.grouped_trades || [], is_saved: true });
      if (res.data.run_logs) setEvents(res.data.run_logs);
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const handleStop = async () => {
    try {
      await stopBacktest();
      setProgress(null);
    } catch (e) {
      console.error(e);
    }
  };

  const isRunning = mutation.isPending || portfolioMutation.isPending || (progress !== null && progress.stage !== 'complete');
  const u = (k, v) => setForm({ ...form, [k]: v });

  const riskWarnings = useMemo(() => {
    const w = [];
    if (form.risk_per_trade_pct > form.max_risk_hard_cap_pct) {
      w.push(`Risk Per Trade (${form.risk_per_trade_pct}%) > Max Risk Hard Cap (${form.max_risk_hard_cap_pct}%). Trades will be capped.`);
    }
    if (form.max_daily_drawdown_pct < form.max_risk_hard_cap_pct) {
      w.push(`Max Daily Drawdown (${form.max_daily_drawdown_pct}%) < Max Risk Hard Cap (${form.max_risk_hard_cap_pct}%). This may cause immediate daily halts.`);
    }
    if (form.prop_firm?.account_mode === 'prop_firm') {
      const mode = form.prop_firm.challenge_type;
      const target = form.target_profit_enabled ? form.max_daily_profit : null;
      if (mode === '1-step' && form.max_daily_drawdown_pct > 4.0) w.push('1-Step Challenge Max Daily DD > 4.0% may violate generic trailing DD rules.');
      if (mode === '2-step' && form.max_daily_drawdown_pct > 5.0) w.push('2-Step Challenge Max Daily DD > 5.0% may violate generic static DD rules.');
    }
    return w;
  }, [form]);

  const selectedStrats = activeTab === 'single' ? [form.strategy_id] : [...new Set(portfolioSymbols.map(s => s.strategy_id))];

  return (<>
    <div className="page-header"><h2><FlaskConical size={22} style={{ display: 'inline', marginRight: 8 }} />Backtester</h2><p>Test strategies with independent risk parameters</p></div>
    <div className="grid-2">
      <div className="card">
        <div className="card-header" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: "100%" }}>
            <dv><span className="card-title">Configuration</span></dv>
            <div style={{ display: 'flex', gap: 4, background: 'var(--bg-tertiary)', padding: 4, borderRadius: 'var(--radius-sm)' }}>
              <button className={`btn btn-sm ${activeTab === 'single' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setActiveTab('single')}>Single</button>
              <button className={`btn btn-sm ${activeTab === 'portfolio' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setActiveTab('portfolio')}><LayoutDashboard size={14} style={{ marginRight: 4 }} /> Portfolio</button>
            </div>
          </div>
          {riskWarnings.length > 0 && (
            <div style={{ background: 'var(--bg-warning)', border: '1px solid var(--border-warning)', borderRadius: 'var(--radius-sm)', padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ color: 'var(--yellow)', fontSize: '0.75rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Shield size={14} /> Risk Constraints Detected
              </div>
              {riskWarnings.map((msg, i) => (
                <div key={i} style={{ color: 'var(--yellow)', fontSize: '0.7rem', display: 'flex', gap: 6 }}>
                  <span>•</span><span>{msg}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={{ display: 'grid', gap: 14 }}>
          {activeTab === 'single' ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div><label>Strategy Engine</label><select value={['APA_v1', 'VWAP_v1', 'DriftJumpAlpha_v1', 'CRT_v1', 'HTFFVGFlip_v1', 'BiasIFVG_v1', 'NYOpenRetest_v1'].includes(form.strategy_id) ? form.strategy_id : 'APA_v1'} onChange={e => setForm({ ...form, strategy_id: e.target.value })}><option value="APA_v1">APA (Adv. Price Action)</option><option value="VWAP_v1">VWAP Institutional</option><option value="DriftJumpAlpha_v1">Drift & Jump Alpha</option><option value="CRT_v1">CRT Strategy</option><option value="HTFFVGFlip_v1">HTF FVG Flip</option><option value="BiasIFVG_v1">Bias KeyLevel IFVG</option><option value="NYOpenRetest_v1">NY Open Break Retest</option></select></div>
              <div>
                <label>Symbol</label>
                <SymbolAutocomplete
                  value={form.symbol}
                  onChange={val => setForm({ ...form, symbol: val })}
                  options={backtestSymbols}
                  placeholder="Type to find symbol"
                />
              </div>
            </div>
          ) : (
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 12, background: 'var(--bg-secondary)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>Portfolio Symbols ({portfolioSymbols.length})</span>
                <button className="btn btn-sm btn-ghost" onClick={addPortfolioSymbol}><PlusCircle size={14} style={{ marginRight: 4 }} /> Add</button>
              </div>
              <div style={{ display: 'grid', gap: 8, maxHeight: 200, overflowY: 'auto', paddingRight: 4 }}>
                {portfolioSymbols.map((item, idx) => (
                  <div key={idx} style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: '0.7rem' }}>Symbol</label>
                      <SymbolAutocomplete
                        value={item.symbol}
                        onChange={val => updatePortfolioSymbol(idx, 'symbol', val)}
                        options={backtestSymbols}
                        placeholder="Type to find symbol"
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: '0.7rem' }}>Strategy</label>
                      <select value={item.strategy_id} onChange={e => updatePortfolioSymbol(idx, 'strategy_id', e.target.value)}>
                        <option value="APA_v1">APA (Adv. Price Action)</option><option value="VWAP_v1">VWAP Institutional</option><option value="DriftJumpAlpha_v1">Drift & Jump Alpha</option><option value="CRT_v1">CRT Strategy</option><option value="HTFFVGFlip_v1">HTF FVG Flip</option><option value="BiasIFVG_v1">Bias KeyLevel IFVG</option><option value="NYOpenRetest_v1">NY Open Break Retest</option>
                      </select>
                    </div>
                    <button className="btn btn-icon btn-ghost" style={{ color: 'var(--red)', marginBottom: 2 }} onClick={() => removePortfolioSymbol(idx)} disabled={portfolioSymbols.length <= 1}><MinusCircle size={16} /></button>
                  </div>
                ))}
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 12 }}>Advanced parameters below will apply globally across the portfolio.</p>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div><label>Start Date</label><input type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} /></div>
            <div><label>End Date</label><input type="date" value={form.end_date} onChange={e => setForm({ ...form, end_date: e.target.value })} /></div>
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: -8 }}>Leave empty to use last N candles.</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div><label>Candles (if no dates)</label><input type="number" value={form.candle_count} onChange={e => setForm({ ...form, candle_count: +e.target.value })} min={100} max={50000} /></div>
            <div><label>Balance ($)</label><input type="number" value={form.initial_balance} onChange={e => setForm({ ...form, initial_balance: +e.target.value })} /></div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12 }}>
            <div><label>Risk %</label><input type="number" step="0.1" value={form.risk_per_trade_pct} onChange={e => setForm({ ...form, risk_per_trade_pct: +e.target.value })} /></div>
            <div><label>Hard Cap %</label><input type="number" step="0.1" value={form.max_risk_hard_cap_pct} onChange={e => setForm({ ...form, max_risk_hard_cap_pct: +e.target.value })} /></div>
            <div><label>Min R:R</label><input type="number" step="0.5" value={form.min_rr} onChange={e => setForm({ ...form, min_rr: +e.target.value })} /></div>
            <div><label>TP Count</label><select value={form.tp_count} onChange={e => setForm({ ...form, tp_count: +e.target.value })}>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} TP{n > 1 ? 's' : ''}</option>)}</select></div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', textTransform: 'none' }}>
              <input type="checkbox" checked={form.prop_firm?.account_mode === 'prop_firm'} onChange={e => u('prop_firm', { ...form.prop_firm, account_mode: e.target.checked ? 'prop_firm' : 'personal' })} style={{ width: 14, height: 14 }} />
              Enable Prop Firm Rules
            </label>
          </div>
          {form.prop_firm?.account_mode === 'prop_firm' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)' }}>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Challenge Type</label>
                <select value={form.prop_firm.challenge_type} onChange={e => u('prop_firm', { ...form.prop_firm, challenge_type: e.target.value })}>
                  <option value="none">None / Funded</option>
                  <option value="1-step">1-Step / Flex (Trailing DD)</option>
                  <option value="2-step">2-Step (Static DD)</option>
                </select>
              </div>
              <div><label style={{ fontSize: '0.7rem' }}>Account Size</label><input type="number" step="1000" value={form.prop_firm.account_size} onChange={e => u('prop_firm', { ...form.prop_firm, account_size: +e.target.value })} /></div>
              <div><label style={{ fontSize: '0.7rem' }}>Initial Balance</label><input type="number" step="1000" value={form.prop_firm.initial_balance} onChange={e => u('prop_firm', { ...form.prop_firm, initial_balance: +e.target.value })} /></div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={{ fontSize: '0.7rem' }}>Max Lot Sizes per Symbol (JSON)</label>
                <input
                  type="text"
                  placeholder='{"Volatility 75 Index": 10, "Volatility 25 (1s) Index": 0.5}'
                  value={typeof form.prop_firm.max_lot_sizes === 'object' ? JSON.stringify(form.prop_firm.max_lot_sizes) : (form.prop_firm.max_lot_sizes || '{}')}
                  onChange={e => {
                    try {
                      const parsed = JSON.parse(e.target.value || '{}');
                      u('prop_firm', { ...form.prop_firm, max_lot_sizes: parsed });
                    } catch { /* allow partial typing — don't update until valid JSON */ }
                  }}
                  style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '0.72rem' }}
                />
                <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: 2 }}>JSON map of symbol name → max lots. Leave as {'{}'} for no cap.</div>
              </div>
            </div>
          )}


          <button className="btn btn-secondary btn-sm" onClick={() => setShowAdvanced(!showAdvanced)} style={{ width: '100%' }}><Settings2 size={14} /> {showAdvanced ? 'Hide' : 'Show'} Advanced Parameters</button>
          {showAdvanced && (<div style={{ display: 'grid', gap: 14, padding: 14, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--blue)' }}>Strategy Engines</div>
            {selectedStrats.map(strat => (
              <div key={strat} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: '0.7rem', fontWeight: 600, marginBottom: 4, color: 'var(--text-muted)' }}>{strat}</div>
                <StrategyParamsEditor strategyId={strat} form={form} setForm={setForm} u={u} />
              </div>
            ))}

            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--yellow)' }}>━ Circuit Breakers</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8 }}>
              <div><label style={{ fontSize: '0.7rem' }}>Max Daily Drawdown (%)</label><input type="number" step="0.1" min="0.1" value={form.max_daily_drawdown_pct} onChange={e => u('max_daily_drawdown_pct', +e.target.value)} /></div>
              <div><label style={{ fontSize: '0.7rem' }}>Max Weekly Drawdown (%)</label><input type="number" step="0.1" min="0.1" value={form.max_weekly_drawdown_pct} onChange={e => u('max_weekly_drawdown_pct', +e.target.value)} /></div>
              <div><label style={{ fontSize: '0.7rem' }}>Max Daily Trades</label><input type="number" value={form.max_daily_trades} onChange={e => u('max_daily_trades', +e.target.value)} /></div>
              <div><label style={{ fontSize: '0.7rem' }}>Max Open Positions</label><input type="number" value={form.max_concurrent_positions} onChange={e => u('max_concurrent_positions', +e.target.value)} /></div>
              <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 12, alignItems: 'center', marginTop: 4 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.75rem' }}><input type="checkbox" checked={form.target_profit_enabled} onChange={e => u('target_profit_enabled', e.target.checked)} style={{ width: 14, height: 14 }} /> Target Profit Halts</label>
                {form.target_profit_enabled && (
                  <>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}><label style={{ fontSize: '0.7rem', margin: 0 }}>Daily $</label><input type="number" step="10" value={form.max_daily_profit} onChange={e => u('max_daily_profit', +e.target.value)} style={{ width: 80, padding: '2px 4px' }} /></div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}><label style={{ fontSize: '0.7rem', margin: 0 }}>Weekly $</label><input type="number" step="10" value={form.max_weekly_profit} onChange={e => u('max_weekly_profit', +e.target.value)} style={{ width: 80, padding: '2px 4px' }} /></div>
                  </>
                )}
              </div>
            </div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--green)' }}>━ Take Profit</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 8 }}>
              {[1, 2, 3, 4, 5].filter(n => n <= form.tp_count).map(n => (
                <div key={n}><label style={{ fontSize: '0.7rem' }}>TP{n} R:R</label><input type="number" step="0.5" value={form[`tp${n}_rr`]} onChange={e => u(`tp${n}_rr`, +e.target.value)} /></div>
              ))}
            </div>
            <div><label style={{ fontSize: '0.7rem' }}>TP Volume Split (%)</label><input type="text" value={form.tp_splits} onChange={e => u('tp_splits', e.target.value)} placeholder="30,25,20,15,10" /><div style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>Comma-separated % per TP (must sum to 100)</div></div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--purple)' }}>━ Break-Even</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div><label style={{ fontSize: '0.7rem' }}>BE Trigger (R)</label><input type="number" step="0.1" value={form.be_trigger_rr} onChange={e => u('be_trigger_rr', +e.target.value)} /></div>
              <div><label style={{ fontSize: '0.7rem' }}>BE Buffer (pips)</label><input type="number" step="0.5" value={form.be_buffer_pips} onChange={e => u('be_buffer_pips', +e.target.value)} /></div>
            </div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--red)' }}>━ Trailing Stops</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {[1, 2, 3, 4, 5].filter(n => n <= form.tp_count).map(n => (
                <div key={n}><label style={{ fontSize: '0.7rem' }}>TP{n} Trail</label><select value={form[`trail_method_tp${n}`]} onChange={e => u(`trail_method_tp${n}`, e.target.value)}>{TRAIL_METHODS.map(m => <option key={m.v} value={m.v}>{m.l}</option>)}</select></div>
              ))}
              <div><label style={{ fontSize: '0.7rem' }}>ATR Multiplier</label><input type="number" step="0.1" value={form.atr_trail_multiplier} onChange={e => u('atr_trail_multiplier', +e.target.value)} /></div>
              <div><label style={{ fontSize: '0.7rem' }}>Fixed Trail Pips</label><input type="number" value={form.trail_pips} onChange={e => u('trail_pips', +e.target.value)} /></div>
            </div>
          </div>)}

          {/* ── FEAT-2: Simulation Costs Section ── */}
          <details style={{ background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)', padding: '10px 14px' }}>
            <summary style={{ cursor: 'pointer', fontSize: '0.78rem', fontWeight: 700, color: 'var(--yellow)', userSelect: 'none' }}>
              ⚙ Simulation Costs & Realism
            </summary>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, marginBottom: 6 }}>
              <button type="button" style={{ fontSize: '0.7rem', padding: '4px 10px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-xs)', cursor: 'pointer' }}
                onClick={async () => {
                  const sym = form.symbol || 'EURUSD';
                  try {
                    const token = localStorage.getItem('token');
                    const res = await fetch(`/api/mt5_test/symbol-costs/${sym}`, { headers: { Authorization: `Bearer ${token}` } });
                    const data = await res.json();
                    if (data.success) {
                      setForm(prev => ({ ...prev,
                        spread_pips: data.spread_pips || prev.spread_pips,
                        commission_per_lot: data.commission || prev.commission_per_lot,
                        _broker_spread_info: `${sym}: ${data.spread_pips} pips spread | ${data.commission || 0} commission/lot | Stops level: ${data.stops_level} pts`
                      }));
                    }
                  } catch (e) { console.warn('Failed to fetch symbol costs:', e); }
                }}>
                📡 Auto-fill from Broker ({form.symbol || 'EURUSD'})
              </button>
              {form._broker_spread_info && <span style={{ fontSize: '0.6rem', color: 'var(--green)' }}>{form._broker_spread_info}</span>}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginTop: 6 }}>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Slippage (pips)</label>
                <input type="number" step="0.1" min="0" value={form.slippage_pips ?? 0} onChange={e => u('slippage_pips', +e.target.value)} />
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Extra pips added to entry price against the trade direction (simulates broker fill slippage). 1–3 pips typical for Forex.</div>
              </div>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Commission / Lot (account currency)</label>
                <input type="number" step="0.5" min="0" value={form.commission_per_lot ?? 0} onChange={e => u('commission_per_lot', +e.target.value)} />
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Round-turn commission per lot deducted from PnL. Typical: $3.50–$7.00/lot for ECN brokers.</div>
              </div>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Spread (pips)</label>
                <input type="number" step="0.1" min="0" value={form.spread_pips ?? 0} onChange={e => u('spread_pips', +e.target.value)} />
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Fixed bid/ask spread cost deducted at entry. Typical: 0.5–2 pips Forex, wider on synthetics/metals.</div>
              </div>
              <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 0', borderTop: '1px solid var(--border-subtle)' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: '0.75rem', flex: '0 0 auto' }}>
                  <input type="checkbox" checked={form.simulate_wicks ?? true} onChange={e => u('simulate_wicks', e.target.checked)} style={{ width: 14, height: 14 }} />
                  Wick Simulation
                </label>
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)' }}>
                  When enabled, uses an OHLC shadow-weighted model to resolve bars where both SL and TP are wicked in the same candle.
                  The SL-side wick depth and distance from open are used to conservatively assign which was hit first.
                  Recommended ON for synthetics (V75, Boom/Crash) with tight SLs.
                </div>
              </div>
            </div>
          </details>

          <div style={{ display: 'flex', gap: 8 }}>
            {!isRunning ? (
              <button className="btn btn-primary" onClick={() => activeTab === 'single' ? mutation.mutate() : portfolioMutation.mutate()} disabled={status !== 'ONLINE'} style={{ flex: 1 }}>
                <Play size={16} /> Run {activeTab === 'single' ? 'Backtest' : 'Portfolio Backtest'}
              </button>
            ) : (
              <button className="btn btn-danger" onClick={handleStop} style={{ flex: 1 }}>
                <X size={16} /> Stop Backtest
              </button>
            )}
            <button className="btn btn-secondary btn-sm" onClick={() => { localStorage.removeItem(STORAGE_KEY); location.reload(); }} title="Reset to defaults" style={{ whiteSpace: 'nowrap' }}>
              <X size={14} /> Reset
            </button>
          </div>
          {(isRunning || progress) && <ProgressBar progress={progress} />}
          {mutation.isError && <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>Error: {mutation.error?.response?.data?.detail || mutation.error?.message || 'Failed'}</div>}
          {btError && <div style={{ color: 'var(--red)', fontSize: '0.8rem', marginTop: 6 }}>Backtest failed: {btError}</div>}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title"><Terminal size={14} style={{ display: 'inline', marginRight: 6 }} />Live Logs</span></div>
        <LiveLogPanel events={events} setEvents={setEvents} />
      </div>
    </div>

    <div ref={resultsRef} style={{ marginTop: 40 }}>
      {isLoadingDetail && (
        <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
          <Loader2 size={32} className="spinner" style={{ marginBottom: 16 }} />
          <div>Loading backtest details...</div>
        </div>
      )}
      {isRunning && (
        <div className="card" style={{ padding: 40, color: 'var(--text-muted)' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div style={{ height: 32, background: 'var(--bg-tertiary)', borderRadius: 4, width: '40%', animation: 'pulse 1.5s infinite ease-in-out' }} />
            <div style={{ height: 200, background: 'var(--bg-tertiary)', borderRadius: 4, width: '100%', animation: 'pulse 1.5s infinite ease-in-out' }} />
            <div style={{ display: 'flex', gap: 20 }}>
              <div style={{ height: 100, background: 'var(--bg-tertiary)', borderRadius: 4, width: '50%', animation: 'pulse 1.5s infinite ease-in-out' }} />
              <div style={{ height: 100, background: 'var(--bg-tertiary)', borderRadius: 4, width: '50%', animation: 'pulse 1.5s infinite ease-in-out' }} />
            </div>
          </div>
          <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }`}</style>
        </div>
      )}
      {result && !isLoadingDetail && !isRunning && <BacktestResults result={result} onSave={handleSave} onDismiss={handleDismiss} onClose={() => setResult(null)} isSaving={isSaving} />}
    </div>

    <div id="saved-backtests" className="card" style={{ marginTop: 20 }}>
      <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div style={{ marginBottom: 4 }}>
          <span className="card-title">Saved Backtests</span>
          <span className="badge badge-blue" style={{ marginLeft: 8 }}>{backtests?.length || 0}</span>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            <button className={`btn btn-sm ${savedBtFilter === 'All' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSavedBtFilter('All')}>All</button>
            <button className={`btn btn-sm ${savedBtFilter === 'Profitable' ? 'btn-green' : 'btn-secondary'}`} onClick={() => setSavedBtFilter('Profitable')}>Profitable</button>
            <button className={`btn btn-sm ${savedBtFilter === 'HighWinRate' ? 'btn-blue' : 'btn-secondary'}`} onClick={() => setSavedBtFilter('HighWinRate')}>WR &gt; 50%</button>
          </div>
          <select value={savedBtSort} onChange={e => setSavedBtSort(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}>
            <option value="Date">Sort by Date</option>
            <option value="PNL">Sort by P&L</option>
            <option value="WinRate">Sort by Win Rate</option>
          </select>
        </div>
      </div>
      <div className="table-wrapper">
        <table>
          <thead><tr>
            <th>Date</th><th>Symbol</th><th>Trades</th><th>Win Rate</th><th>P&L</th><th>Notes</th><th></th>
          </tr></thead>
          <tbody>
            {(() => {
              if (!backtests?.length) return <tr><td colSpan="6" style={{ textAlign: 'center', padding: 20 }}>No saved backtests</td></tr>;

              let filtered = [...backtests];
              if (savedBtFilter === 'Profitable') filtered = filtered.filter(b => b.total_pnl > 0);
              if (savedBtFilter === 'HighWinRate') filtered = filtered.filter(b => b.win_rate >= 0.5);

              filtered.sort((a, b) => {
                if (savedBtSort === 'Date') return new Date(b.created_at || 0) - new Date(a.created_at || 0);
                if (savedBtSort === 'PNL') return (b.total_pnl || 0) - (a.total_pnl || 0);
                if (savedBtSort === 'WinRate') return (b.win_rate || 0) - (a.win_rate || 0);
                return 0;
              });

              return filtered.map(bt => {
                return (
                  <tr key={bt.id}>
                    <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{bt.created_at ? new Date(bt.created_at).toLocaleString() : 'N/A'}</td>
                    <td><strong>{bt.title || bt.symbol}</strong></td>
                    <td>{bt.total_trades}</td>
                    <td className={bt.win_rate >= 0.5 ? 'green' : 'yellow'}>{((bt.win_rate || 0) * 100).toFixed(0)}%</td>
                    <td style={{ color: bt.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>${(bt.total_pnl || 0).toFixed(2)}</td>
                    <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{bt.notes_preview || ''}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => handleView(bt.id)}><Eye size={12} /></button>
                        <button className="btn btn-danger btn-sm" onClick={() => handleDelete(bt.id)}><Trash2 size={12} /></button>
                      </div>
                    </td>
                  </tr>
                );
              });
            })()}
          </tbody>
        </table>
      </div>
    </div>

    {showSaveModal && (
      <SaveModal
        result={result}
        form={form}
        isPortfolio={activeTab === 'portfolio'}
        portfolioSymbols={portfolioSymbols}
        onClose={() => setShowSaveModal(false)}
        onSuccess={handleSaveSuccess}
      />
    )}
  </>);
}