import React, { useState, useEffect, useRef, useCallback, memo, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { decimate } from '../utils/decimate';

// The exit settings the live bot applies from strategy_defaults.py. Kept at
// module scope: as a value inside the component it would be a fresh array on
// every render, and it is a dependency of the prefill effect below.
const MEASURED_EXIT_FIELDS = ['tp_count', 'tp1_rr', 'be_mode', 'trail_method_tp1'];

// Live risk settings (Settings -> Risk) the Backtester form has no field for.
// They are sent from the SAVED config in the request's risk_config, so a
// backtest runs the same trailing/sizing rules the live bot runs. Anything the
// form does show is seeded from the saved config instead (see the seeding
// effect), so the user can still change it for a run.
const LIVE_RISK_PASSTHROUGH = [
  'trail_activation_rr', 'atr_trail_multiplier_tp1', 'atr_trail_multiplier_tp2',
  'atr_trail_multiplier_tp3', 'atr_trail_multiplier_tp4', 'atr_trail_multiplier_tp5',
  'trail_pct', 'trail_step_pips', 'trail_structure_bars', 'min_stop_cost_multiple',
  'max_margin_utilisation_pct', 'min_deployable_risk_pct', 'min_stop_spread_multiple',
  'confluence_risk_tiers', 'reject_below_confluence', 'post_split_risk_tolerance_pct',
  'exit_slippage_pips', 'open_risk_weight', 'be_spread_multiple', 'trail_require_be_first',
  'be_trigger_tp_level', 'trail_trigger_tp_level', 'tp_volume_pcts', 'max_cluster_risk_pct',
  'max_net_direction_risk_pct', 'symbol_cluster_overrides', 'strategy_risk_budget_pct',
  'vol_target_annual_pct', 'vol_target_lookback_bars', 'vol_target_min_scale', 'vol_target_max_scale',
];

function savedRiskPassthrough(savedConfig, formKeys) {
  const risk = savedConfig?.risk || {};
  const out = {};
  for (const k of LIVE_RISK_PASSTHROUGH) {
    if (formKeys && k in formKeys) continue;          // the form's own value wins
    if (risk[k] !== undefined && risk[k] !== null) out[k] = risk[k];
  }
  return out;
}
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import SymbolPicker from '../components/SymbolPicker';
import { useSymbolOptions } from '../hooks/useSymbolOptions';
import { FlaskConical, Play, Trash2, Eye, Save, X, ChevronDown, ChevronRight, Loader2, Shield, Terminal, Settings2, LayoutDashboard, PlusCircle, Download } from 'lucide-react';
import { getParameterSchema, runBacktest, runPortfolioBacktest, getBacktests, deleteBacktest, saveBacktest, getBotLogs, getConfig, getBacktestStatus, getLatestBacktestResult, getLatestResultSummary, getLatestResultTrades, getBacktestSummary, getBacktestTrades, stopBacktest, getSavedTradeChart, getUnsavedTradeChart, getSymbolCosts, getStrategyDefaults } from '../services/api';
import TradeChart from '../components/TradeChart';
import BacktestReplay from '../components/BacktestReplay';
import RunReport from '../components/RunReport';
import AnalyzeButton from '../components/AnalyzeButton';
import ParamsPanel from '../components/ParamsPanel';
import SlotEditor from '../components/SlotEditor';
import { STRATEGY_GROUP, STRATEGY_OPTIONS as SLOT_STRATEGY_OPTIONS } from '../components/slotSpec';
import { useConnectionStore, useAuthStore } from '../store';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, CartesianGrid } from 'recharts';
import * as summaryEngine from '../utils/summaryEngine';
import { saveCachedResult, loadCachedResult, clearCachedResult, downloadResult } from '../utils/resultCache';
import { loadResultProgressively, TRADE_PAGE_SIZE } from '../utils/progressiveResult';

// Below this many trades, win-rate/expectancy stats are not statistically
// meaningful (e.g. a 1-trade "100% win rate" result). Used to show a
// low-confidence warning banner on backtest results.
const LOW_SAMPLE_TRADE_THRESHOLD = 20;


/**
 * Reduce a numeric series to at most `limit` points, keeping the first and last.
 *
 * A run's equity curve carries one point per bar — 45,000+ on a multi-timeframe
 * 5,000-candle run. The chart never draws more than 500 of them, and nothing
 * else reads the full series, so every place that stores or re-derives it should
 * decimate first.
 */
function fmt(v) {
  if (!v) return '—';
  if (typeof v === 'string' && v.includes('T')) return new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  if (typeof v === 'number' && v > 1e9) return new Date(v * 1000).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  return String(v);
}
function fmtDur(m) { if (!m || m <= 0) return '—'; if (m < 60) return `${m.toFixed(0)}m`; if (m < 1440) return `${(m / 60).toFixed(1)}h`; return `${(m / 1440).toFixed(1)}d`; }

function MetricCard({ title, value, color }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{title}</div>
      <div className="kpi-value" style={color ? { color } : undefined}>{value}</div>
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

function LiveLogPanel({ events }) {
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s => s.isAuthenticated);
  const { data: logs } = useQuery({ queryKey: ['btLogs'], queryFn: () => getBotLogs(50).then(r => r.data), refetchInterval: 2000, enabled: status === 'ONLINE' && isAuth });

  // Live WebSocket events are kept HERE, batched to at most four renders a
  // second. They used to live in the Backtester page's own state, so every bot
  // scan line and backtest log line re-rendered the entire page — form, results
  // and replay — which is what made it hang while the bot or a run was busy.
  // `events` (the finished run's logs) still comes from the page.
  const [live, setLive] = useState([]);
  useEffect(() => { if (!events.length) setLive([]); }, [events]);
  useEffect(() => {
    let pending = [];
    let timer = null;
    const h = e => {
      const m = e.detail;
      if (m?.type !== 'activity_log' || !m.event) return;
      pending.push(m.event);
      if (timer) return;
      timer = setTimeout(() => {
        const batch = pending.reverse();
        pending = [];
        timer = null;
        setLive(p => [...batch, ...p].slice(0, 2000));
      }, 250);
    };
    window.addEventListener('ws-message', h);
    return () => { window.removeEventListener('ws-message', h); if (timer) clearTimeout(timer); };
  }, []);

  const merged = useMemo(() => {
    const all = [...live, ...events, ...(logs?.events || [])]; const seen = new Set(); const out = [];
    for (const e of all) { const k = `${e.time}|${e.message}`; if (!seen.has(k)) { seen.add(k); out.push(e); } }
    out.sort((a, b) => (b.time || '').localeCompare(a.time || ''));
    const allowed = ['BACKTEST', 'BACKTEST_LOG', 'SIGNAL', 'TRADE', 'SMC'];
    return out.filter(e => allowed.some(a => (e.category || '').includes(a)) || e.level === 'ERROR');
  }, [logs, events, live]);

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

// [17.3] Extract a human message from an axios/FastAPI failure. FastAPI puts
// the reason in `detail`; without this the UI showed nothing at all when a
// request was rejected (e.g. the 400 "a backtest is already running"), so a
// run would silently fail to start with no feedback at all.
function httpErrorMessage(e) {
  return (
    e?.response?.data?.detail ||
    e?.response?.data?.message ||
    e?.message ||
    'Request failed'
  );
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
      const meta = {
        strategy_id: result.params_snapshot?.strategy_id || form.strategy_id,
        // For portfolio saves, `symbol` on the run row is still populated
        // (e.g. as a comma-joined list) so older UI that reads .symbol as
        // a fallback still shows something reasonable — but `title` is
        // what the saved-backtests list should actually display.
        symbol: isPortfolio ? (portfolioSymbols || []).map(s => s.symbol).join(', ') : (result.params_snapshot?.symbol || form.symbol),
        title: trimmedTitle,
        risk_config: result.params_snapshot || form,
        notes: notesInput,
      };
      try {
        // The server saves from its own complete copy of the run, so only the
        // details typed here are sent — the page holds a lean copy, and
        // uploading a 3,000-trade run back was megabytes for nothing.
        await saveBacktest(result.backtest_id, { backtest_data: meta, save_mode: 'SERVER' });
      } catch (err) {
        // 409: the server no longer holds this run (e.g. it restarted) — upload it.
        if (err?.response?.status !== 409) throw err;
        // Uploading needs every trade group; a half-loaded run would save short.
        if (result._trades_loading || result._trades_error) throw new Error('The server no longer holds this run and not all of its trades have loaded — load them first, then save.', { cause: err });
        await saveBacktest(result.backtest_id, { backtest_data: { ...result, ...meta }, save_mode: 'FULL' });
      }
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

// Fetched trade charts, shared across rows and re-opens, bounded so a long
// review session does not keep every chart it ever opened.
const TRADE_CHART_CACHE = new Map();
const TRADE_CHART_CACHE_MAX = 80;
function cacheTradeChart(key, value) {
  TRADE_CHART_CACHE.delete(key);
  TRADE_CHART_CACHE.set(key, value);
  if (TRADE_CHART_CACHE.size > TRADE_CHART_CACHE_MAX) TRADE_CHART_CACHE.delete(TRADE_CHART_CACHE.keys().next().value);
}
const TF_LABEL = { M5: 'Entry · M5', M15: 'Context · M15', H1: 'Structure · H1' };

const ConfirmationLine = ({ c }) => {
  const isHeader = c.startsWith('═') || c.startsWith('──');
  const cls = isHeader ? 'conf-line conf-header' : c.startsWith('✓') ? 'conf-line conf-pass'
    : c.startsWith('✗') ? 'conf-line conf-fail' : c.startsWith('△') ? 'conf-line conf-mixed' : 'conf-line';
  return <div className={cls}>{c}</div>;
};

const GroupedTradeRow = memo(function GroupedTradeRow({ group, index, measureRef, vIndex, backtestId }) {
  const [open, setOpen] = useState(false);
  const [activeChart, setActiveChart] = useState(null);
  // tf -> { candles, meta } | { error }. Only the timeframe being looked at is
  // fetched, trimmed around the trade by the server — expanding a row used to
  // download every timeframe at once.
  const [charts, setCharts] = useState({});
  const [meta, setMeta] = useState(null);
  const pnl = group.combined_pnl || 0;
  const tf = activeChart || (meta?.available?.includes('M5') ? 'M5' : meta?.available?.[0]) || 'M5';

  useEffect(() => {
    if (!open || charts[tf]) return undefined;
    const key = `${backtestId || 'current'}|${group.group_id}|${tf}`;
    const hit = TRADE_CHART_CACHE.get(key);
    if (hit) {
      setCharts(c => ({ ...c, [tf]: hit }));
      setMeta(m => m || hit.meta);
      return undefined;
    }
    let cancelled = false;
    const req = backtestId ? getSavedTradeChart(backtestId, group.group_id, tf) : getUnsavedTradeChart(group.group_id, tf);
    req.then(res => {
      const d = res.data || {};
      const entry = {
        candles: d.candles || [],
        meta: { available: d.available || [], panel: d.panel || {}, sub_trades_panel: d.sub_trades_panel || [] },
      };
      cacheTradeChart(key, entry);
      if (cancelled) return;
      setCharts(c => ({ ...c, [tf]: entry }));
      setMeta(m => m || entry.meta);
    }).catch(() => {
      if (!cancelled) setCharts(c => ({ ...c, [tf]: { error: true, candles: [] } }));
    });
    return () => { cancelled = true; };
  }, [open, tf, charts, backtestId, group.group_id]);

  // Panel fields (confirmations, zones) a paged trade list leaves out, merged
  // under the row's own values once the chart request brings them.
  const view = useMemo(() => {
    if (!meta) return group;
    const subs = (group.sub_trades || []).map((st, i) => ({ ...(meta.sub_trades_panel?.[i] || {}), ...st }));
    return { ...meta.panel, ...group, smc_data: group.smc_data || meta.panel?.smc_data, sub_trades: subs };
  }, [group, meta]);
  const lead = view.sub_trades?.[0] || {};
  const chart = charts[tf];
  const tabs = meta?.available?.length ? meta.available : ['M5'];

  return (
    <tbody ref={measureRef} data-index={vIndex}>
      <tr onClick={() => setOpen(!open)} className={`trade-row ${pnl >= 0 ? 'trade-win' : 'trade-loss'}`} style={{ cursor: 'pointer' }}>
        <td>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</td>
        <td className="num">{index + 1}</td>
        <td><strong>{group.symbol}</strong></td>
        <td><span className={`badge ${group.direction === 'BUY' ? 'badge-green' : 'badge-red'}`}>{group.direction === 'BUY' ? '▲ BUY' : '▼ SELL'}</span></td>
        <td className="num">{typeof group.entry_price === 'number' ? group.entry_price.toFixed(2) : group.entry_price}</td>
        <td className="num">{fmt(group.entry_time_iso)}</td>
        <td className="num">{fmt(group.exit_time_iso)}</td>
        <td className="num">{fmtDur(group.duration_minutes)}</td>
        <td>{group.tp_count} TPs ({group.tp_wins}W/{group.tp_losses}L)</td>
        <td className="num" style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${pnl.toFixed(2)}</td>
        <td className="num" style={{ fontSize: '0.8rem' }}>${group.balance_before != null ? group.balance_before.toFixed(2) : '—'}</td>
        <td className="num" style={{ fontSize: '0.8rem' }}>${group.balance_after != null ? group.balance_after.toFixed(2) : '—'}</td>
        <td><span className="badge badge-blue">{group.entry_session || '—'}</span>{group.exit_session && group.exit_session !== group.entry_session && <span className="badge badge-blue" style={{ marginLeft: 4 }}>→{group.exit_session}</span>}</td>
      </tr>
      {open && group.sub_trades?.map((t, j) => (
        <tr key={j} className="trade-leg">
          <td></td><td></td>
          <td colSpan={2}><span className={`badge ${t.exit_reason?.startsWith('TP') ? 'badge-green' : t.exit_reason === 'BE_SL' ? 'badge-blue' : 'badge-red'}`}>TP{t.tp_level} → {t.exit_reason}</span></td>
          <td colSpan={2} className="num" style={{ fontSize: '0.72rem' }}>{fmt(t.entry_time_iso)} → {fmt(t.exit_time_iso)}</td>
          <td className="num">{fmtDur(t.duration_minutes)}</td>
          <td style={{ fontSize: '0.72rem' }}>Vol: {t.volume} | BE: {t.be_applied ? '✓' : '✗'}{t.trail_applied ? ' | Trail: ✓' : ''}</td>
          <td className="num" style={{ fontSize: '0.72rem' }}>
            MAE: {(t.mae_pips || 0).toFixed(1)}p | MFE: {(t.mfe_pips || 0).toFixed(1)}p
            <br />
            Bal: ${t.balance_before != null ? t.balance_before.toFixed(2) : '—'} → ${t.balance_after != null ? t.balance_after.toFixed(2) : '—'}
          </td>
          <td className="num" style={{ color: (t.pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
            ${(t.pnl || 0).toFixed(2)}
          </td>
          <td>{t.session || '—'}</td>
          <td></td><td></td>
        </tr>
      ))}
      {open && (
        <tr><td colSpan={13} style={{ padding: 0, border: 'none' }}>
          <div className="trade-detail">
            <div className="trade-detail-side">
              {lead.entry_confirmations?.length > 0 && (
                <>
                  <div className="detail-heading">Entry confirmations · score {lead.confluence_score || '—'}</div>
                  {lead.entry_confirmations.map((c, i) => <ConfirmationLine key={i} c={c} />)}
                </>
              )}
              {lead.exit_confirmations?.length > 0 && (
                <>
                  <div className="detail-heading">Exit</div>
                  {lead.exit_confirmations.map((c, i) => <div key={i} className="conf-line">{c}</div>)}
                </>
              )}
              {!meta && !chart && <div className="conf-line" style={{ color: 'var(--text-muted)' }}>Loading trade detail…</div>}

              <div className="detail-heading">Trade analytics</div>
              <div className="detail-grid">
                <div className="detail-cell"><span>Group ID</span><span className="num">{group.group_id}</span></div>
                <div className="detail-cell"><span>Session</span><span style={{ color: 'var(--purple)' }}>{group.entry_session || 'UNKNOWN'}</span></div>
                <div className="detail-cell">
                  <span>Confluence</span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span className="meter"><span style={{ width: `${Math.min(100, Math.max(0, group.confluence_score || 0))}%`, background: (group.confluence_score || 0) >= 80 ? 'var(--green)' : (group.confluence_score || 0) >= 60 ? 'var(--blue)' : 'var(--yellow)' }} /></span>
                    <span className="num">{group.confluence_score || 0}/100</span>
                  </span>
                </div>
                <div className="detail-cell">
                  <span>TP splits</span>
                  <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {group.sub_trades?.map((st, idx) => (
                      <span key={idx} className={`badge badge-${(st.pnl || 0) > 0 ? 'green' : (st.pnl || 0) < 0 ? 'red' : 'blue'}`}>
                        TP{st.tp_level || idx + 1}: {st.volume}L
                      </span>
                    ))}
                  </span>
                </div>
              </div>
            </div>

            <div className="trade-detail-main">
              <div className="chart-tabs">
                {tabs.map(name => (
                  <button key={name} className={`chart-tab${tf === name ? ' active' : ''}`} onClick={() => setActiveChart(name)}>
                    {TF_LABEL[name] || name}
                  </button>
                ))}
              </div>
              {!chart ? (
                <div className="chart-placeholder"><Loader2 size={14} className="spinner" /> Loading {tf} chart…</div>
              ) : chart.candles?.length ? (
                <TradeChart group={view} candles={chart.candles} timeframe={tf} height={340} />
              ) : view.entry_snapshot_b64 ? (
                <img src={`data:image/png;base64,${view.entry_snapshot_b64}`} alt="Entry snapshot" style={{ width: '100%', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }} />
              ) : (
                <div className="chart-placeholder">{chart.error ? 'Chart unavailable for this trade.' : `No ${tf} candles stored for this trade.`}</div>
              )}
              {/* The analysis carries this trade's markings — the levels the
                  strategy actually measured — so "was the strategy implemented
                  correctly here" is answerable per trade. */}
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                <AnalyzeButton
                  targetType="trade"
                  targetId={group.group_id}
                  compact
                  question="Did the strategy fire correctly on this trade? Check the entry against the confluences it recorded, and say whether the stop and target placement follow from them."
                />
              </div>
            </div>
          </div>
        </td></tr>
      )}
    </tbody>);
});

/**
 * [L1] Per-strategy exit defaults.
 *
 * Trailing, break-even and session gating used to be one global setting applied
 * to all seven strategies. Measurement says that is wrong in both directions:
 * the 15-cell trailing sweep improved 10 cells and made 5 WORSE (nearly all the
 * gain in NYOpenRetest, +1,765 PnL, while DriftJumpAlpha lost 1,299 on Crash
 * 1000), and session-gate contribution ranged from -0.170 (HTFFVGFlip, actively
 * harmful) to +0.126 (BiasIFVG, the best gate in the study).
 *
 * So these are shipped per strategy and applied automatically. The panel exists
 * so it is visible WHY a strategy trails and another does not — the user should
 * not have to touch these to get the measured-best behaviour.
 */


/**
 * [T2.2] Why did this run produce no trades?
 *
 * Every zero-trade run has a reason recorded in `rejection_funnel`, but nothing
 * rendered it, so the result page was simply blank. The most common cause by
 * far was the position sizer refusing every signal because `min_sl_pips` (a
 * single global value applied to every asset class) exceeded the strategy's
 * typical stop on that symbol — it logs at ERROR level and returns 0 lots, so
 * the run "completes" with nothing in it.
 */
function EmptyResultDiagnostic({ funnel, blocked }) {
  const strat = funnel.strategy_rejections || {};
  const risk = funnel.risk_rejections || {};
  const fill = funnel.fill_rejections || {};
  const gateStats = funnel.gate_stats || {};
  const evaluated = funnel.total_evaluated ?? funnel.candidates_evaluated ?? 0;
  const approved = funnel.approved ?? 0;
  const errors = funnel.errors ?? 0;
  const sum = o => Object.values(o || {}).reduce((a, b) => a + (b || 0), 0);

  const rows = [
    ['Strategy rejected', sum(strat), strat],
    ['Risk engine rejected', sum(risk), risk],
    ['Rejected at fill', sum(fill), fill],
  ].filter(r => r[1] > 0);

  let topCause = null, topN = 0, topKind = '';
  for (const [kind, obj] of [['risk', risk], ['strategy', strat], ['fill', fill]]) {
    for (const [k, v] of Object.entries(obj || {})) {
      if (v > topN) { topN = v; topCause = k; topKind = kind; }
    }
  }
  if (!topCause) {
    const gates = Object.entries(gateStats)
      .map(([k, g]) => [k, g.blocked_candidates || 0])
      .sort((a, b) => b[1] - a[1]);
    if (gates.length && gates[0][1] > 0) { topCause = gates[0][0]; topN = gates[0][1]; topKind = 'gate'; }
  }

  const ADVICE = {
    stop_below_min_viable:
      "The stop was inside the minimum viable distance, so the sizer returned 0 lots. Lower Min SL (pips) — it applies to every asset class, and 10 pips is large for a low-volatility pair like EURGBP.",
    min_sl_pips:
      "Min SL (pips) is rejecting the strategy's natural stop. Lower it, or set a per-asset-class value.",
    insufficient_rr:
      "TP is too close relative to SL for the configured Min RR. Lower Min RR or widen the TP multiple.",
    session_filter:
      "The session filter excluded every candidate. Widen the session window, or disable it for this strategy.",
    session_exclusion:
      "The session filter excluded every candidate. Widen the session window, or disable it for this strategy.",
    daily_risk_cap:
      "The strategy refused every bar on its OWN daily risk cap: risk per trade alone is above its "
      + "Max daily risk pct. Raise that on the slot's Strategy tab, or lower risk per trade. It is "
      + "checked before any signal is formed, which is why the run found none.",
    daily_trade_cap:
      "The strategy's own Max trades per day was used up (or is 0). Raise it on the slot's Strategy tab.",
    crash_symbol_only:
      "This strategy only trades CRASH symbols. Run it on a Crash index, or pick another strategy.",
  };
  const advice = ADVICE[topCause]
    || (topKind === 'gate'
      ? 'The "' + topCause + '" confluence blocked every candidate. Loosen it, or disable it to see what it was costing.'
      : 'Open the Run Report panel below for the full rejection breakdown.');

  return (
    <div style={{
      margin: '12px 0', padding: 16, borderRadius: 'var(--radius-sm)',
      background: 'rgba(234,179,8,0.06)', border: '1px solid rgba(234,179,8,0.35)',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--yellow)', marginBottom: 8 }}>
        No trades were placed in this run
      </div>

      {evaluated > 0 ? (
        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: '0.78rem', lineHeight: 1.9 }}>
          <div>Signals evaluated: <strong>{evaluated}</strong></div>
          {rows.map(([label, n, obj]) => (
            <div key={label} style={{ paddingLeft: 12 }}>
              &#9500;&#9472; {label}: <strong>{n}</strong>
              <span style={{ color: 'var(--text-muted)' }}>
                {'  ('}
                {Object.entries(obj).sort((a, b) => b[1] - a[1]).slice(0, 3)
                  .map(([k, v]) => k + ': ' + v).join(', ')}
                {')'}
              </span>
            </div>
          ))}
          {errors > 0 && (
            <div style={{ paddingLeft: 12 }}>&#9500;&#9472; Errors: <strong>{errors}</strong></div>
          )}
          <div style={{ paddingLeft: 12 }}>&#9492;&#9472; Approved: <strong>{approved}</strong></div>
        </div>
      ) : (
        <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
          The strategy emitted no signals at all in this window — nothing reached the risk engine.
          {Object.keys(gateStats).length > 0 && ' The confluence breakdown below shows which gate stopped them.'}
        </div>
      )}

      {topCause && (
        <div style={{
          marginTop: 12, padding: 10, borderRadius: 'var(--radius-xs)',
          background: 'rgba(0,0,0,0.25)', fontSize: '0.82rem',
        }}>
          <strong style={{ color: 'var(--yellow)' }}>Most likely cause:</strong>{' '}
          <code>{topCause}</code> ({topN} candidates)
          <div style={{ marginTop: 6, color: 'var(--text-secondary)' }}>{advice}</div>
        </div>
      )}

      {Object.keys(gateStats).length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            Confluence breakdown ({Object.keys(gateStats).length} gates)
          </summary>
          <div style={{ marginTop: 8, fontFamily: "'JetBrains Mono',monospace", fontSize: '0.72rem' }}>
            {Object.entries(gateStats)
              .sort((a, b) => (b[1].blocked_candidates || 0) - (a[1].blocked_candidates || 0))
              .map(([name, g]) => (
                <div key={name} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                  <span style={{ color: g.blocked_candidates ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                    {name}{g.blocked_candidates ? '' : '  (never blocks)'}
                  </span>
                  <span style={{ color: 'var(--text-muted)' }}>
                    {g.evaluated} eval &middot; {(100 * (g.pass_rate ?? 0)).toFixed(1)}% pass &middot; {g.blocked_candidates || 0} blocked
                  </span>
                </div>
              ))}
          </div>
        </details>
      )}

      {blocked?.length > 0 && (
        <div style={{ marginTop: 10, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {blocked.length} individual blocked signal{blocked.length === 1 ? '' : 's'} recorded — see the Run Report panel.
        </div>
      )}
    </div>
  );
}

const BacktestResults = memo(function BacktestResults({ result, onRetryTrades, onSave, onDismiss, onClose, isSaving }) {
  const report = result.report || {};
  // [I1]/[H1]: the engine returns rejection_funnel at the TOP LEVEL of the
  // result (matching the portfolio route), not nested under `report` — the
  // panel below read `report.rejection_funnel` and has never had data for a
  // single-symbol run. Fall back to the old nested path for any already-saved
  // run whose JSON blob predates this fix.
  const rejectionFunnel = result.rejection_funnel || report.rejection_funnel || {};
  const blockedSignals = result.blocked_signals || [];
  // [7.12-7.17] The six diagnostic panels, as one component shared with live.
  // Their backing data (rejection_funnel, blocked_signals, sizing_diagnostics)
  // has been in the response since Phase 0 with nothing rendering it.
  const runReport = <RunReport result={result} backtestId={result.backtest_id || null} />;
  // Memoised so the `|| []` fallback cannot mint a fresh array on every render.
  // That identity feeds filteredGrouped -> filteredNormalized -> displayGroups ->
  // summaryData -> the virtualized list, so an unstable `grouped` would defeat the
  // whole chain exactly as the unmemoised filteredGrouped used to.
  const grouped = useMemo(() => result.grouped_trades || [], [result.grouped_trades]);
  // Was recomputed on EVERY render: a 45,000-element `.map()` building 45,000
  // objects, followed further down by a 45,000-element `.filter()` to downsample
  // for the chart. Both ran again on every filter, sort, tab and group-by
  // change. Memoised and decimated once — the chart draws 500 points either way.
  const eqData = useMemo(
    () => decimate(result.equity_curve || [], 500).map((v, i) => ({ bar: i, equity: v })),
    [result.equity_curve],
  );
  const initialBalance = result.initial_balance || 10000;
  // Which normaliser summaryEngine should use for drawdown %, Sharpe/Sortino
  // and Calmar. Read from the run that produced these trades, not from the
  // live form — an old saved run must keep being scored on ITS basis.
  const sizingBasis = result.params_snapshot?.sizing_basis || 'STATIC';

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

  // MEMOISED, and this is load-bearing. As a plain `let` it produced a new array
  // identity on every render, which silently defeated EVERY memo downstream of it
  // — filteredNormalized, filteredStats, filteredSessionData, displayGroups,
  // summaryData, the virtualized list's flattenedRows, and the memo() on the list
  // component itself. One unmemoised line meant a 2,216-trade run re-ran four
  // filters, a full normalise+sort, both summaryEngine passes and the whole
  // grouping on every keystroke, hover and tab change.
  const filteredGrouped = useMemo(() => {
    let out = grouped;
    if (activeFilter === 'Wins') out = out.filter(g => (g.net_pnl ?? g.combined_pnl ?? g.pnl) > 0);
    if (activeFilter === 'Losses') out = out.filter(g => (g.net_pnl ?? g.combined_pnl ?? g.pnl) <= 0);
    if (symbolFilter !== 'All') out = out.filter(g => g.symbol === symbolFilter);
    if (strategyFilter !== 'All') out = out.filter(g => g.strategy_id === strategyFilter);
    return out;
  }, [grouped, activeFilter, symbolFilter, strategyFilter]);

  // Normalize grouped trades into the shape summaryEngine's math expects,
  // sorted chronologically (its stats functions assume this). Re-derived
  // whenever the symbol/strategy/result filter changes, so switching the
  // Symbol dropdown updates every metric below — not just the trade list —
  // to reflect only that symbol/strategy's own trades.
  const filteredNormalized = useMemo(() => {
    return [...filteredGrouped]
      .map(g => ({
        // group_id keeps summaryEngine's signal count (totalGroups) honest —
        // without it, two symbols entering on the same bar collapse into one.
        group_id: g.group_id,
        pnl: g.net_pnl ?? g.combined_pnl ?? g.pnl ?? 0,
        entry_time: g.entry_time_iso || g.entry_time,
        exit_time: g.exit_time_iso || g.exit_time,
        balance_before: g.balance_before,
        balance_after: g.balance_after,
        direction: g.direction,
        entry_price: g.entry_price,
        stop_loss: g.stop_loss,
        exit_price: g.exit_price,
        // Volume-weighted R across the group's TP legs. Passing it through
        // lets summaryEngine use it instead of re-deriving R from the group's
        // best-leg exit_price, which overstates partially-closed trades.
        realized_rr: g.realized_rr,
        pnl_r: g.pnl_r,
        session: g.entry_session,
        symbol: g.symbol,
      }))
      .sort((a, b) => new Date(a.entry_time || 0) - new Date(b.entry_time || 0));
  }, [filteredGrouped]);

  const filteredStats = useMemo(
    () => summaryEngine.computePeriodStats(filteredNormalized, initialBalance, null, sizingBasis),
    [filteredNormalized, initialBalance, sizingBasis]
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

  const displayGrouped = filteredGrouped; // Virtualized list handles large datasets efficiently

  // Earliest entry time, computed ONCE per dataset. getWeekNumber used to derive
  // this itself on every call — and it is called once per trade while grouping,
  // so a 2,216-trade run did 2,216 full passes (~5M date parses) every time the
  // list re-rendered. The spread in `Math.min(...arr)` was also a latent hard
  // failure: above ~65k elements it throws RangeError, so a large run crashed
  // the tab outright rather than merely being slow. A reduce has no such limit.
  const earliestEntryTime = useMemo(() => {
    let min = Infinity;
    for (const tg of displayGrouped) {
      const t = new Date(tg.entry_time_iso || 0).getTime();
      if (!Number.isNaN(t) && t < min) min = t;
    }
    return min === Infinity ? null : min;
  }, [displayGrouped]);

  const getWeekNumber = useCallback((d) => {
    if (earliestEntryTime == null) return 1;
    return Math.floor((d.getTime() - earliestEntryTime) / (7 * 24 * 60 * 60 * 1000)) + 1;
  }, [earliestEntryTime]);

  const displayGroups = useMemo(() => {
    if (groupBy === 'None') {
      return [{ label: 'All Trades', trades: displayGrouped }];
    }
    const groupsMap = {};
    // Sort on a real timestamp captured per bucket, not on the label. The old
    // `new Date(b) - new Date(a)` re-parsed the display label, and a Week
    // label ("Week 3") is not a parseable date — the comparator returned NaN
    // and left the weekly summary in arbitrary order.
    const groupSortKey = {};
    displayGrouped.forEach(g => {
      if (!g.entry_time_iso) return;
      const d = new Date(g.entry_time_iso);
      if (Number.isNaN(d.getTime())) return;
      let key = '';
      if (groupBy === 'Day') key = d.toLocaleDateString();
      if (groupBy === 'Week') key = `Week ${getWeekNumber(d)}`;
      if (groupBy === 'Month') key = d.toLocaleString('default', { month: 'long', year: 'numeric' });
      if (groupBy === 'Year') key = d.getFullYear().toString();
      if (!groupsMap[key]) groupsMap[key] = [];
      groupsMap[key].push(g);
      const ts = d.getTime();
      if (groupSortKey[key] === undefined || ts < groupSortKey[key]) groupSortKey[key] = ts;
    });
    const sortedKeys = Object.keys(groupsMap).sort((a, b) => groupSortKey[b] - groupSortKey[a]);
    return sortedKeys.map(k => ({ label: k, trades: groupsMap[k] }));
  }, [displayGrouped, groupBy, getWeekNumber]);

  const summaryData = useMemo(() => {
    if (groupBy === 'None') return [];
    return displayGroups.map(group => {
      let startBal = null;
      let endBal = null;
      let isBreached = false;

      const sorted = [...group.trades].sort((a, b) => new Date(a.entry_time_iso || 0) - new Date(b.entry_time_iso || 0));
      sorted.forEach(t => {
        if (startBal === null) startBal = t.balance_before;

        const dStr = (t.entry_time_iso || '').split('T')[0];
        if ((result.prop_firm_breach_days || []).includes(dStr)) isBreached = true;
      });

      // Ending balance is the balance after the last trade to CLOSE. `sorted`
      // is entry-ordered (that's the order the rows render in), and with
      // overlapping positions the last-opened trade is frequently not the
      // last-closed one — reading balance_after off it reported an ending
      // balance that disagreed with startBal + period P&L.
      const lastClosed = [...group.trades].sort(
        (a, b) => new Date(a.exit_time_iso || a.entry_time_iso || 0) - new Date(b.exit_time_iso || b.entry_time_iso || 0)
      ).slice(-1)[0];
      endBal = lastClosed ? lastClosed.balance_after : null;

      // Reuse the exact same stats math as the top-level cards (and as
      // CumulativeSummary) so a period row's Max DD / Sharpe / Sortino /
      // Expectancy are computed consistently, not with a second formula.
      const normalized = sorted.map(t => ({
        group_id: t.group_id,
        pnl: t.net_pnl ?? t.combined_pnl ?? t.pnl ?? 0,
        entry_time: t.entry_time_iso || t.entry_time,
        exit_time: t.exit_time_iso || t.exit_time,
        balance_before: t.balance_before,
        balance_after: t.balance_after,
        direction: t.direction,
        entry_price: t.entry_price,
        stop_loss: t.stop_loss,
        exit_price: t.exit_price,
        realized_rr: t.realized_rr,
        pnl_r: t.pnl_r,
        symbol: t.symbol,
      }));
      const stats = summaryEngine.computePeriodStats(normalized, startBal ?? initialBalance, initialBalance, sizingBasis);

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
        startBal,
        // Older/portfolio-engine records don't always carry balance_after;
        // derive it rather than rendering an em-dash next to a real P&L.
        endBal: endBal != null ? endBal : (startBal != null ? startBal + stats.pnl : null),
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
  }, [displayGroups, groupBy, symbolFilter, initialBalance, sizingBasis]);

  const [expandedPeriods, setExpandedPeriods] = useState(new Set());
  const togglePeriod = (period) => {
    setExpandedPeriods(prev => {
      const next = new Set(prev);
      if (next.has(period)) next.delete(period); else next.add(period);
      return next;
    });
  };

  // `eqData` is already decimated to <=500 points at the memo above, so the
  // second full-series filter that used to live here is gone.
  const chartEqData = eqData;

  return (<div className="card" style={{ marginTop: 20 }}>
    <div className="card-header">
      <span className="card-title">Results — {(result._trades_total ?? result.grouped_trades?.length) || 0} signals, {result.total_trades || 0} sub-positions
        {result._trades_loading && (
          <span className="pill-loading" title="Trade groups arrive in pages. Figures computed from trades settle once every page is in.">
            <Loader2 size={11} className="spinner" /> loading trades {result._trades_loaded || 0}/{result._trades_total}
          </span>
        )}
        {result._trades_error && (
          <span className="pill-loading pill-error" title={`Stopped loading trades: ${result._trades_error}`}>
            trades {result._trades_loaded || 0}/{result._trades_total} · paused
            {onRetryTrades && <button type="button" className="pill-action" onClick={onRetryTrades}>Retry</button>}
          </span>
        )}
      </span>
      <div style={{ display: 'flex', gap: 8 }}>
        {!result.is_saved ? (
          <>
            <button className="btn btn-primary btn-sm" onClick={onSave} disabled={isSaving}><Save size={14} /> {isSaving ? 'Saving...' : 'Save'}</button>
            {/* Writes the COMPLETE in-memory result to a .json file. No quota,
                no stripping — this is the way to hand a whole run to someone
                else, which the cache was being misused for. */}
            <button className="btn btn-secondary btn-sm" onClick={() => downloadResult(result)} disabled={!!(result._trades_loading || result._trades_error)} title="Download the full result as JSON"><Download size={14} /> Export</button>
            <button className="btn btn-danger btn-sm" onClick={onDismiss}><X size={14} /> Dismiss</button>
          </>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={onClose}><X size={14} /> Close</button>
        )}
      </div>
    </div>
    <div className="desk-meta">
      {[
        result.portfolio ? `Portfolio · ${(result.symbols || []).length} legs` : (result.params_snapshot?.strategy_id || result.strategy_id),
        result.portfolio ? (result.symbols || []).join(', ') : (result.params_snapshot?.symbol || result.symbol),
        [result.params_snapshot?.start_date || result.start_date, result.params_snapshot?.end_date || result.end_date]
          .filter(Boolean).map(d => String(d).slice(0, 10)).join(' → '),
        `Capital $${Number(initialBalance).toLocaleString()}`,
      ].filter(Boolean).map((m, i) => <span key={i}>{m}</span>)}
    </div>
    {/* [T2.2] Empty-result diagnosis.
        A run with 0 trades used to render a blank results card with no
        explanation, which is what "the backtest just produces nothing" was.
        The rejection funnel has the answer; this surfaces it and names the
        single most likely cause. */}
    {grouped.length === 0 && !result._trades_loading && !result._trades_error && (
      <EmptyResultDiagnostic funnel={rejectionFunnel} blocked={blockedSignals} result={result} />
    )}
    {filteredStats.tradeCount > 0 && filteredStats.tradeCount < LOW_SAMPLE_TRADE_THRESHOLD && (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, padding: '10px 14px',
        background: 'rgba(234, 179, 8, 0.12)', border: '1px solid var(--yellow)', borderRadius: 'var(--radius-sm)',
      }}>
        <Shield size={16} color="var(--yellow)" style={{ flexShrink: 0 }} />
        <span style={{ fontSize: '0.8rem', color: 'var(--yellow)', fontWeight: 600 }}>
          Low sample size: only {filteredStats.tradeCount} trade{filteredStats.tradeCount === 1 ? '' : 's'} in this result.
          Win rate and other metrics are not statistically meaningful below ~{LOW_SAMPLE_TRADE_THRESHOLD} trades — treat this run as directional, not representative.
        </span>
      </div>
    )}
    {result.invalid_signals > 0 && <div style={{ fontSize: '0.8rem', color: 'var(--yellow)', marginBottom: 8 }}><Shield size={12} style={{ display: 'inline', marginRight: 4 }} />{result.invalid_signals} signals rejected (invalid SL/TP)</div>}

    {result.notes && (
      <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)' }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--blue)', marginBottom: 4 }}>Narration / Notes</div>
        <div style={{ fontSize: '0.85rem', whiteSpace: 'pre-wrap' }}>{result.notes}</div>
      </div>
    )}

    {/* [7.2/H2] Was a flat dump: nested objects via JSON.stringify and unset
        fields as the literal string "null". ParamsPanel groups the nesting and
        folds the unset fields behind a count. */}
    <ParamsPanel params={result.params_snapshot} />

    {/* [7.12-7.17] Signal funnel, risk deployment, exit attribution,
        blocked-signal timeline and cost impact. Shares one component with the
        live run report per task 7.17. */}
    <div style={{ marginBottom: 16 }}>{runReport}</div>

    {rejectionFunnel && Object.keys(rejectionFunnel).length > 0 && (
      <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)' }}>
        <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8, display: 'flex', alignItems: 'center' }}>
          <Shield size={14} style={{ marginRight: 6, color: 'var(--blue)' }} /> Signal Rejection Funnel
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Overview</div>
            <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span>Total Evaluated:</span> <strong>{rejectionFunnel.total_evaluated}</strong></div>
            <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span>Approved:</span> <strong style={{ color: 'var(--green)' }}>{rejectionFunnel.approved}</strong></div>
            <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span>Errors:</span> <strong style={{ color: rejectionFunnel.errors > 0 ? 'var(--red)' : 'var(--text-primary)' }}>{rejectionFunnel.errors}</strong></div>
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Rejection Breakdown</div>
            <div style={{ maxHeight: 100, overflowY: 'auto', paddingRight: 4 }}>
              {Object.entries(rejectionFunnel.strategy_rejections || {}).map(([reason, count]) => (
                <div key={reason} style={{ fontSize: '0.75rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2, borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '80%' }} title={reason}>{reason}</span>
                  <span style={{ color: 'var(--yellow)' }}>{count}</span>
                </div>
              ))}
              {Object.entries(rejectionFunnel.risk_rejections || {}).map(([reason, count]) => (
                <div key={reason} style={{ fontSize: '0.75rem', display: 'flex', justifyContent: 'space-between', marginBottom: 2, borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '80%' }} title={reason}>Risk: {reason}</span>
                  <span style={{ color: 'var(--yellow)' }}>{count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        {blockedSignals.length > 0 && (
          <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>
              Blocked Signals ({blockedSignals.length}{blockedSignals.length >= 500 ? '+, capped' : ''}) — every strategy signal that did not become a trade
            </div>
            <div style={{ maxHeight: 160, overflowY: 'auto', fontSize: '0.72rem' }}>
              {blockedSignals.slice(0, 100).map((b, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{(b.time || '').slice(0, 16)}</span>
                  <span style={{ flexShrink: 0 }}>{b.symbol} {b.direction}</span>
                  <span style={{ color: 'var(--yellow)', flexShrink: 0 }}>{b.gate}</span>
                  <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={b.reason}>{b.reason}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    )}

    <div className="kpi-strip">
      <MetricCard title="Final Balance" value={`$${(initialBalance + filteredStats.pnl).toFixed(2)}`} color={filteredStats.pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
      <MetricCard title="Net P&L" value={`$${filteredStats.pnl.toFixed(2)}`} color={filteredStats.pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
      <MetricCard title="Win Rate" value={`${(filteredStats.winRate * 100).toFixed(1)}%`} color={filteredStats.winRate >= 0.5 ? 'var(--green)' : 'var(--red)'} />
      <MetricCard title="Profit Factor" value={filteredStats.profitFactor >= 999 ? '∞' : filteredStats.profitFactor.toFixed(2)} />
      <MetricCard title="Sharpe" value={filteredStats.sharpe.toFixed(2)} />
      {/* Say which basis this is. The capital basis divides by the STARTING
          balance, so a run that grows a lot can print >100% without ever
          having come close to blowing up — the peak basis is the readable one
          there, and computePeriodStats already switches when sizing compounds. */}
      {/* Unfiltered, the headline is the ENGINE's bar-level drawdown (balance +
          floating P&L on every bar) — the same figure the saved report carries.
          Once a filter removes trades there is no bar-level curve for the
          subset, so it falls back to the closed-trade curve and says so. */}
      {(() => {
        const unfiltered = activeFilter === 'All' && symbolFilter === 'All' && strategyFilter === 'All';
        const compounding = filteredStats.sizingBasis !== 'STATIC';
        const engineDd = compounding ? report.max_drawdown_pct_of_peak : report.max_drawdown_pct;
        const useEngine = unfiltered && engineDd != null;
        return (
          <MetricCard
            title={`Max DD (${compounding ? 'of peak' : 'of capital'}${useEngine ? '' : ', closed trades'})`}
            value={`${((useEngine ? engineDd : filteredStats.maxDdPct) * 100).toFixed(1)}%`}
            color="var(--red)"
          />
        );
      })()}
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
        <ResponsiveContainer width="100%" height={200}><AreaChart data={chartEqData}><XAxis dataKey="bar" hide /><YAxis domain={['auto', 'auto']} fontSize={10} /><Tooltip formatter={v => `$${v.toFixed(2)}`} /><Area type="monotone" dataKey="equity" stroke="#26b98c" fill="#26b98c1f" strokeWidth={2} /></AreaChart></ResponsiveContainer>
      </div>)}
      <div className="card" style={{ padding: 12 }}><h4 style={{ marginBottom: 8 }}>Win Rate by Session</h4>
        <ResponsiveContainer width="100%" height={200}><BarChart data={filteredSessionData}><XAxis dataKey="session" fontSize={11} /><YAxis domain={[0, 100]} fontSize={10} /><Tooltip formatter={v => `${v.toFixed(1)}%`} /><Bar dataKey="rate" fill="#5b9cf6" radius={[2, 2, 0, 0]} /></BarChart></ResponsiveContainer>
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

// [per-slot] A saved live slot's own risk settings, in the shape the backend's
// risk/slot_book.slot_overrides_from() produces. Sending these with a backtest
// is what makes the run reproduce the slot as the live bot trades it: same risk
// %, same daily limits, same targets, same break-even and trailing.
function slotRiskOf(slot) {
  if (!slot) return {};
  const out = { ...(slot.risk || {}) };
  const named = [
    ['risk_per_trade_pct', slot.risk_per_trade_pct],
    ['max_daily_trades', slot.max_trades_per_day],
    ['max_positions_per_symbol', slot.max_positions_per_symbol],
    ['tp1_rr', slot.tp1_rr],
    ['tp_count', slot.tp_count],
  ];
  named.forEach(([k, v]) => { if (v !== null && v !== undefined) out[k] = v; });
  return out;
}


function findLiveSlot(config, symbol, strategyId) {
  const slots = config?.instrument_slots || [];
  return slots.find(sl => String(sl.symbol || '').toUpperCase() === String(symbol || '').toUpperCase()
    && sl.strategy_id === strategyId) || null;
}

const TRAIL_METHODS = [{ v: 'NONE', l: 'None' }, { v: 'ATR_TRAIL', l: 'ATR Trail' }, { v: 'FIXED_PIPS', l: 'Fixed Pips' }, { v: 'STRUCTURE_TRAIL', l: 'Structure Trail' }, { v: 'PCT_TRAIL', l: '% Trail' }];

// One list, shared with Settings' trading book (components/slotSpec.js).
const STRATEGY_OPTIONS = SLOT_STRATEGY_OPTIONS.map(([id, label]) => [id, label]);
const VALID_STRATEGIES = STRATEGY_OPTIONS.map(([id]) => id);

// Builds the backend strategy_params payload for one portfolio symbol/strategy
// pairing. Portfolio backtests previously only forwarded tuned params for
// APA_v1/DriftJumpAlpha_v1/CRT_v1 — any other strategy silently got
// strategy_params: {} (engine hardcoded defaults) regardless of UI
// configuration. These field mappings mirror what the single-symbol
// backtest mutation sends for each strategy, so a portfolio run and a
// single-symbol run of the same strategy use the same tuned parameters.

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

// Simulation-cost fields use '' to mean "not explicitly set — resolve from the
// broker". The backend types these as `float | str | None` and treats null /
// '' / 'auto' as unset, at which point it sources the value from live MT5
// symbol data (falling back to asset-class averages). An explicit 0.0 is a
// deliberate zero-cost run and is honoured as such, which is exactly why the
// two states must stay distinguishable here.
const COST_FIELDS = ['slippage_pips', 'commission_per_lot', 'spread_pips'];
const costOrAuto = (v) => (v === '' || v === null || v === undefined ? null : +v);

// Form slices that are objects. The localStorage restore merges these one level
// deep so a newly-added strategy parameter is not lost behind a stale blob.
const NESTED_FORM_KEYS = ['prop_firm', 'slot_strategy_params', 'slot_risk'];

// Bump this whenever a default below changes in a way a cached blob would
// override. Old keys are purged on load — see the `form` initialiser.
const STORAGE_KEY = 'algoedge_bt_config_v2';
const LEGACY_STORAGE_KEYS = ['algoedge_bt_config'];

// Every value here mirrors the authoritative backend dataclass default
// (RiskParams in backend/core/config_schema.py and each strategy's params.py).
// The frontend sends these EXPLICITLY on every run, so any drift silently
// overrides the backend rather than falling back to it. Field names must match
// the dataclass fields exactly — the backend filters strategy_params through a
// hasattr() check and drops anything it does not recognise.
const DEFAULT_FORM = {
  symbol: 'XAUUSD', initial_balance: 10000,
  start_date: '', end_date: '', candle_count: 5000,
  max_risk_hard_cap_pct: 2.0,
  // Strategy parameters used to live here, one block per strategy, shared by
  // every symbol. They are per slot now (components/SlotEditor.jsx), so this
  // form only carries what belongs to the RUN: dates, balance, costs and the
  // account-level risk a slot falls back to.
  session_filter_enabled: true,
  // Stream bars to a live chart while a run is in flight. Off by default: the
  // results are identical either way, and the stream is what made long
  // portfolio runs feel heavy.
  live_chart: false,
  risk_per_trade_pct: 0.5, min_rr: 3.0,
  max_daily_drawdown_pct: 3.0, max_weekly_drawdown_pct: 6.0,
  max_concurrent_positions: 3, max_daily_trades: 5,
  // [3.8/E5] Now editable — see the Advanced panel. 1 = a second setup on the
  // same symbol is discarded while one is open.
  max_positions_per_symbol: 1,
  allow_pyramiding: false, min_bars_between_entries: 0,
  // [sizing] What each trade's risk is computed against. STATIC (backend
  // default) sizes every trade off initial_balance and never compounds;
  // BALANCE/EQUITY compound with realised/floating P&L. Sent explicitly so a
  // saved run records which basis produced it — the same trade sequence
  // returns wildly different equity curves under the two.
  sizing_basis: 'STATIC',
  min_sl_pips: 10.0, max_account_leverage: 30.0,
  tp_count: 3, tp1_rr: 1.5, tp2_rr: 3.0, tp3_rr: 5.0, tp4_rr: 10.0, tp5_rr: 15.0,
  tp_splits: '50,30,20',
  // Both triggers live: a TP fill OR the R-multiple, whichever comes first.
  // Safe because the thresholds are now SEPARATED (BE/trail at 2.0R, TP1 at
  // 1.5R). Equal thresholds were the bug — a level touch beats a limit fill, so
  // BE pre-empted the partial it was meant to follow (0 TPs across 21 legs).
  // Keeping the R trigger matters: TP_HIT alone would never arm BE at all on a
  // setup whose TP1 never fills. See backend/core/config_schema.py::be_mode.
  be_mode: 'EITHER', trail_mode: 'EITHER',
  be_trigger_rr: 2.0, be_buffer_pips: 0.0, be_buffer_atr_mult: 0.10,
  trail_method_tp1: 'NONE', trail_method_tp2: 'ATR_TRAIL', trail_method_tp3: 'STRUCTURE_TRAIL',
  trail_method_tp4: 'NONE', trail_method_tp5: 'NONE',
  // The R-multiple at which RR-mode trailing arms. Was never in this form at
  // all, so the backend silently used its own default of 1.0.
  trail_trigger_rr: 2.0,
  atr_trail_multiplier: 1.5, trail_pips: 15,
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
  use_strategy_exit_defaults: true,
  // Simulation Costs (BUG-8/BUG-9) — '' means "auto, from broker data".
  slippage_pips: '', commission_per_lot: '', spread_pips: '', simulate_wicks: true,
};

export default function Backtester() {
  const queryClient = useQueryClient();
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s => s.isAuthenticated);
  const [activeTab, setActiveTab] = useState('single'); // 'single' | 'portfolio'

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

  const portfolioMutation = useMutation({
    mutationFn: () => {
      cancelResultLoad();
      applyFreshResult(null);
      setEvents([]);
      setBtError(null);
      return runPortfolioBacktest({
        symbols: portfolioSymbols.map(s => ({
          symbol: s.symbol,
          strategy_id: s.strategy_id,
          // The row's own strategy parameters (not a global block any more).
          strategy_params: s.strategy_params ?? (findLiveSlot(userCfg?.config, s.symbol, s.strategy_id)?.strategy_params_override || {}),
          use_measured_params: s.use_measured_params !== false,
          // [17.1] null = inherit the portfolio-wide tp1_rr. Sent only when the
          // row actually sets one, matching the backend's "None = inherit".
          tp1_rr: s.tp1_rr ?? null,
          // [per-slot] each row runs on its own risk engine, under its slot's
          // own settings — no limit is shared between rows.
          risk: s.risk ?? slotRiskFor(s.symbol, s.strategy_id),
        })),
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
        // [T2.3] trail_method_tp1 was absent from this payload while tp2-tp5
        // were sent. With tp_count=1 that meant the ONLY leg had no trail
        // method, MultiTPManager fell back to "NONE", and RiskEngine's
        // `if trail_method and ...` guard short-circuited every tick. Measured
        // result: trail_method NULL on 3,528/3,528 saved trades and zero
        // TRAIL_SL exits in the entire book.
        trail_method_tp1: form.trail_method_tp1,
        trail_trigger_rr: form.trail_trigger_rr,
        trail_method_tp2: form.trail_method_tp2,
        trail_method_tp3: form.trail_method_tp3,
        trail_method_tp4: form.trail_method_tp4,
        trail_method_tp5: form.trail_method_tp5,
        atr_trail_multiplier: form.atr_trail_multiplier,
        trail_pips: form.trail_pips,
        session_filter_enabled: form.session_filter_enabled,
        prop_firm: form.prop_firm,
        max_risk_hard_cap_pct: form.max_risk_hard_cap_pct ?? 2.0,

        max_concurrent_positions: form.max_concurrent_positions,
        max_daily_drawdown_pct: form.max_daily_drawdown_pct,
        max_weekly_drawdown_pct: form.max_weekly_drawdown_pct,
        max_positions_per_symbol: form.max_positions_per_symbol || 1,
        max_daily_trades: form.max_daily_trades || 5,
        // Exit-ladder ordering. Sent explicitly so a saved run records which
        // rule produced it — the difference between BE-on-touch and BE-on-fill
        // is the difference between 0 and n take-profits.
        be_mode: form.be_mode ?? 'TP_HIT',
        trail_mode: form.trail_mode ?? 'TP_HIT',
        allow_pyramiding: !!form.allow_pyramiding,
        min_bars_between_entries: form.min_bars_between_entries ?? 0,
        // The single-symbol call spreads `...form` so it carries this already;
        // the portfolio payload is explicit, so it has to be named here or a
        // portfolio run silently falls back to the STATIC default.
        sizing_basis: form.sizing_basis ?? 'STATIC',
        replay_enabled: !!form.live_chart,
        target_profit_enabled: form.target_profit_enabled,
        max_daily_profit: form.max_daily_profit,
        max_weekly_profit: form.max_weekly_profit,
        // Simulation costs — must match single-symbol call so portfolio and
        // single results are comparable when the same settings are used.
        // null = unset, so the backend sources the value from broker data.
        slippage_pips: costOrAuto(form.slippage_pips),
        commission_per_lot: costOrAuto(form.commission_per_lot),
        spread_pips: costOrAuto(form.spread_pips),
        simulate_wicks: form.simulate_wicks ?? true,
        risk_config: savedRiskPassthrough(remoteConfig?.config, form),
        // Each row's strategy runs its own measured exits (1 TP / no break-even
        // for ORB and the classic families), as the live bot applies them per
        // signal. A row's own TP1 R:R still wins.
        use_strategy_exit_defaults: exitDefaultsForRun(),
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtests'] }),
    // [17.3] Without this an HTTP rejection never reached the user.
    onError: (e) => setBtError(httpErrorMessage(e)),
  });


  const { data: remoteConfig } = useQuery({
    queryKey: ['config'],
    queryFn: () => getConfig().then(r => r.data),
    enabled: status === 'ONLINE' && isAuth,
  });

  // The symbols already configured rank above the generic list, and the
  // broker's own names (which the hook fetches) rank above both.
  const configuredSymbols = useMemo(() => {
    const cfg = remoteConfig?.config;
    if (!cfg) return [];
    const slots = cfg.instrument_slots?.map(s => s.symbol) || [];
    const legacy = cfg.instrument_settings ? cfg.instrument_settings.map(i => i.symbol) : (cfg.symbols || []);
    return [...new Set([...slots, ...legacy].filter(Boolean))];
  }, [remoteConfig]);
  const backtestSymbols = useSymbolOptions(configuredSymbols);

  // The form this browser last saved, read once at mount. Its presence decides
  // whether the live config may seed the form (first visit only) and whether the
  // measured exits are prefilled on load — see the effects below.
  const [savedFormAtLoad] = useState(() => {
    try {
      // Drop the pre-retune cache. v1 blobs pin the OLD defaults (1% risk,
      // 3% hard cap, 2-pip BE buffer, zeroed simulation costs, …) and the
      // frontend sends every one of them explicitly, so a returning user would
      // silently keep overriding the whole cost-realism retune.
      LEGACY_STORAGE_KEYS.forEach(k => localStorage.removeItem(k));
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved ? (JSON.parse(saved) || null) : null;
    } catch { return null; }
  });

  const [form, setForm] = useState(() => {
    // Anything the saved blob does not carry falls back to DEFAULT_FORM, so
    // parameters added after a user last ran a backtest arrive with the correct
    // backend default rather than as `undefined`.
    if (savedFormAtLoad) {
      const merged = { ...DEFAULT_FORM, ...savedFormAtLoad };
      NESTED_FORM_KEYS.forEach(k => {
        merged[k] = { ...DEFAULT_FORM[k], ...(savedFormAtLoad[k] || {}) };
      });
      return merged;
    }
    return JSON.parse(JSON.stringify(DEFAULT_FORM));
  });

  // The cache is IndexedDB now, which is async — so there is no synchronous
  // initial value. `hasFreshResult` stops a slow cache read from overwriting a
  // result that already arrived from the server or a completed run in the
  // meantime; the cache is only ever allowed to fill an empty slot.
  const [result, setResult] = useState(null);
  const hasFreshResult = useRef(false);
  const applyFreshResult = useCallback((r) => {
    hasFreshResult.current = true;
    setResult(r);
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadCachedResult().then(cached => {
      if (!cancelled && cached && !hasFreshResult.current) setResult(cached);
    });
    // One-time cleanup of the old localStorage key so its multi-MB string
    // stops occupying the quota other features share.
    try { localStorage.removeItem('algoedge_bt_result'); } catch { }
    return () => { cancelled = true; };
  }, []);

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
  const [savedBtFilter, setSavedBtFilter] = useState('All');
  const [savedBtSort, setSavedBtSort] = useState('Date');
  const [configLoaded, setConfigLoaded] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);

  // Persist every change: debounced while typing, and flushed when the page is
  // reloaded, closed or navigated away from — a reload inside the debounce
  // window used to lose the last edit. Reset sets skipPersistRef so the flush
  // does not write the old form straight back.
  const formRef = useRef(form);
  useEffect(() => { formRef.current = form; }, [form]);
  const skipPersistRef = useRef(false);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (skipPersistRef.current) return;
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(form)); } catch { }
    }, 300);
    return () => clearTimeout(timer);
  }, [form]);
  useEffect(() => {
    const flush = () => {
      if (skipPersistRef.current) return;
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(formRef.current)); } catch { }
    };
    window.addEventListener('pagehide', flush);
    window.addEventListener('beforeunload', flush);
    return () => {
      flush();
      window.removeEventListener('pagehide', flush);
      window.removeEventListener('beforeunload', flush);
    };
  }, []);

  // Auto-save result to localStorage (debounced)
  //
  // This used to `JSON.stringify(result)` in full. A finished run is 4-8 MB —
  // a per-bar equity curve (45k+ points on a 5,000-candle multi-timeframe run),
  // every leg, every grouped trade — and stringify plus localStorage.setItem
  // are both synchronous on the main thread, and anything over the ~5 MB quota
  // (billed in UTF-16, so ~2.5 MB of JSON) threw. The tiered version that
  // replaced it shed fields until something fit, which on a 1,300-trade run
  // meant caching the headline and NO trades — useless for the one job the
  // cache has.
  //
  // IndexedDB stores a structured clone, so there is no stringify pass, the
  // write is async and off the paint path, and the quota is large enough that
  // the COMPLETE result goes in untouched. See utils/resultCache.js.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!result) {
        clearCachedResult();
        return;
      }
      if (result._trades_loading || result._trades_error) return; // cache the run once every page is in
      saveCachedResult(result).then(ok => {
        if (!ok) {
          console.warn(
            '[Backtester] Could not cache this run — IndexedDB is unavailable ' +
            '(private window, or site data blocked). The result still loads ' +
            'from the server on reload, and the Export button still works.'
          );
        }
      });
    }, 500);
    return () => clearTimeout(timer);
  }, [result]);

  // Load defaults from user's live config (only on first load if no saved config)
  const { data: userCfg } = useQuery({ queryKey: ['config'], queryFn: () => getConfig().then(r => r.data), enabled: status === 'ONLINE' && isAuth });

  // Parameter schema for the slot editors — the backend generates it from the
  // dataclasses, so a new strategy parameter shows up here on its own.
  const { data: schemaResp } = useQuery({
    queryKey: ['parameter-schema'],
    queryFn: () => getParameterSchema().then(r => r.data),
    enabled: status === 'ONLINE' && isAuth,
    staleTime: 60 * 60 * 1000,
  });
  const schema = schemaResp?.fields;

  // The slot this single run tests. Seeded from the saved slot for this
  // symbol + strategy (so "test what trades" is the default), then edited here
  // without touching Settings.
  const savedSlot = findLiveSlot(userCfg?.config, form.symbol, form.strategy_id);
  // Edits are tagged with the pairing they were made for, so switching symbol or
  // strategy falls back to the slot saved for the NEW pairing instead of carrying
  // the previous one's parameters over.
  const slotKey = `${form.symbol}::${form.strategy_id}`;
  const edited = form.slot_key === slotKey;
  const singleSlot = {
    symbol: form.symbol,
    strategy_id: form.strategy_id,
    strategy_params: (edited ? form.slot_strategy_params : undefined)
      ?? (savedSlot?.strategy_params_override || {}),
    risk: (edited ? form.slot_risk : undefined) ?? slotRiskOf(savedSlot),
    use_measured_params: (edited ? form.use_measured_params : undefined)
      ?? (savedSlot?.use_measured_params !== false),
  };

  // [L1] Measured per-strategy exit/session defaults. Cached indefinitely —
  // they are compiled-in constants derived from the Phase 3 sweeps, not user
  // state, so refetching them is pure noise.
  const { data: strategyDefaultsResp } = useQuery({
    queryKey: ['strategy-defaults'],
    queryFn: () => getStrategyDefaults().then(r => r.data),
    enabled: isAuth,
    staleTime: Infinity,
  });
  // Memoised: the `|| {}` fallback otherwise produces a new object every render,
  // and this value is a dependency of the effect below — so that effect re-ran on
  // every single render instead of only when the defaults actually arrive.
  const allStrategyDefaults = useMemo(
    () => strategyDefaultsResp?.strategy_defaults || {},
    [strategyDefaultsResp],
  );

  // [18.3] Adopt the selected strategy's MEASURED exit settings (research/16,
  // 285 cells / 23,989 trades). Previously these were displayed in the defaults
  // panel but never applied, so every run used the generic values regardless of
  // what the measurement said.
  //
  // EXTENDED from tp1_rr alone to every measured exit field, because applying
  // only one of them is what put backtest and live out of sync: the live bot
  // applies the whole block (tp_count, tp1_rr, be_mode, trail_method_tp1), so a
  // backtest that adopted only tp1_rr ran THREE TP legs per signal against a
  // live bot running one. The same four setups then showed as ~12 backtest
  // trades and 4 live trades, which looked like the bot skipping signals.
  //
  // Pre-filling the form (rather than substituting server-side) means the
  // numbers on screen are the numbers that run, and changing one still wins —
  // the backend only fills in fields the request does not mention.
  //
  // Only fires when the strategy actually changes, so a value the user has
  // typed is not overwritten while they work.
  //
  // Which of them, though, is decided the way LIVE decides it: bot_service lays a
  // measured default on a trade only where the account's saved RiskParams value
  // is still the shipped default. The server says which fields that is
  // (`live_applies`); for the others the saved value is what live trades, so
  // that is what the form gets. Re-runs once the saved config arrives.
  // The slot profile a run should carry, or {} when the switch is off or the
  // symbol+strategy has no saved slot. Defined here, below `userCfg`: both this
  // and the callback under it read it at render, so declaring them earlier threw
  // "Cannot access 'userCfg' before initialization" and blanked the page.
  // The risk saved for this pairing in Settings — what a slot editor starts from.
  const slotRiskFor = (symbol, strategyId) => slotRiskOf(findLiveSlot(userCfg?.config, symbol, strategyId));

  // Which exit rules a run follows, decided the way live decides it: the saved
  // Settings switch when the run is following the slot's live risk, and only
  // otherwise the box on this page. Defined here because it reads `form`.
  // The same switch live reads, so a slot's exits are its exits on both.
  const exitDefaultsForRun = () => (userCfg?.config?.risk?.use_strategy_exit_defaults ?? true);

  const measuredExitPatch = useCallback((sid) => {
    const entry = allStrategyDefaults[sid];
    const measured = entry?.defaults;
    if (!measured) return {};
    const liveApplies = entry?.live_applies;
    const savedRisk = userCfg?.config?.risk || {};
    const patch = {};
    MEASURED_EXIT_FIELDS.forEach(k => {
      if (measured[k] == null) return;
      if (!liveApplies || liveApplies.includes(k)) patch[k] = measured[k];
      else if (savedRisk[k] !== undefined && savedRisk[k] !== null) patch[k] = savedRisk[k];
    });
    return patch;
  }, [allStrategyDefaults, userCfg]);

  // Prefill when the user CHANGES strategy — not on every page load. Keyed on
  // load as well, it re-applied the measured exits on each reload and wiped the
  // exits the user had set and saved. A restored form counts as already
  // prefilled for its own strategy.
  const prefilledStratRef = useRef(savedFormAtLoad ? form.strategy_id : null);
  useEffect(() => {
    const sid = form.strategy_id;
    if (!sid || prefilledStratRef.current === sid) return;
    if (!strategyDefaultsResp) return;                      // wait for the measured table
    if (status === 'ONLINE' && isAuth && !userCfg) return;  // and for live_applies' saved config
    prefilledStratRef.current = sid;
    const patch = measuredExitPatch(sid);
    if (Object.keys(patch).length) setForm(prev => ({ ...prev, ...patch }));
  }, [form.strategy_id, strategyDefaultsResp, userCfg, status, isAuth, measuredExitPatch]);

  // [P1.12] The account's saved LIVE settings, laid over the form — saved config
  // spread LAST, so it wins. Before that fix the form's own fully-populated
  // defaults overwrote every saved key (Settings ran synth 20/20, the Backtester
  // silently ran 6/4.0).
  const withLiveConfig = useCallback((prev, c) => {
    const merged = { ...prev };
    merged.max_risk_hard_cap_pct = c.risk?.max_risk_hard_cap_pct ?? prev.max_risk_hard_cap_pct ?? 3.0;
    // Every flat risk field the form shows takes the account's SAVED value (what
    // the live bot runs). The measured exit fields are resolved separately
    // (live_applies, above), exactly as live resolves them.
    Object.keys(DEFAULT_FORM).forEach(k => {
      if (MEASURED_EXIT_FIELDS.includes(k)) return;
      const v = c.risk?.[k];
      if (v !== undefined && v !== null && typeof v !== 'object') merged[k] = v;
    });
    merged.prop_firm = { ...(prev.prop_firm || {}), ...(c.prop_firm || {}) };
    // Strategy parameters are NOT merged here any more: they belong to the slot
    // being tested, and the slot editor seeds them from the saved slot for that
    // symbol + strategy (see `singleSlot`).
    return merged;
  }, []);

  // Seed from the live settings on a FIRST visit only, so a first backtest runs
  // what the bot runs. Afterwards the Backtester keeps its own saved settings:
  // this used to run on every mount and replaced whatever the user had set with
  // the live config, which is why edits "reset on reload". The "Load live
  // settings" button re-applies the live settings on demand.
  useEffect(() => {
    if (userCfg?.config && !configLoaded && !savedFormAtLoad) {
      const c = userCfg.config;
      setForm(prev => withLiveConfig(prev, c));
    }
    if (userCfg) setConfigLoaded(true);
  }, [userCfg, configLoaded, savedFormAtLoad, withLiveConfig]);

  // ── A finished run, loaded in pages (utils/progressiveResult.js) ─────────
  // A token per load: starting a run, dismissing, or opening another result
  // abandons the load in flight, so its late pages cannot land on the wrong run.
  const loadTokenRef = useRef(0);
  // How the result on screen fetches its trade pages, so Retry can resume it.
  const pagerRef = useRef(null);
  const cancelResultLoad = useCallback(() => { loadTokenRef.current += 1; }, []);
  const loadResultInPages = useCallback(async ({ fetchSummary, fetchPage, isSaved = false, resume = null }) => {
    const token = ++loadTokenRef.current;
    pagerRef.current = { fetchSummary, fetchPage, isSaved };
    let first = !resume;
    if (!resume) setIsLoadingDetail(true);
    try {
      return await loadResultProgressively({
        fetchSummary,
        fetchPage,
        resume,
        pageSize: TRADE_PAGE_SIZE,
        describeError: httpErrorMessage,
        isCancelled: () => token !== loadTokenRef.current,
        onUpdate: (r) => {
          if (first) {
            first = false;
            setIsLoadingDetail(false);
            if (r.run_logs?.length) setEvents(r.run_logs);
          }
          applyFreshResult(isSaved ? { ...r, is_saved: true } : r);
        },
      });
    } finally {
      if (!resume && token === loadTokenRef.current) setIsLoadingDetail(false);
    }
  }, [applyFreshResult]);

  // Continue a trade list that stopped part-way, from the groups already shown.
  const retryTrades = useCallback((r) => {
    const pager = pagerRef.current;
    if (!pager || !r) return;
    const summary = { ...r, trades_paged: true, trade_groups_total: r._trades_total };
    for (const k of ['grouped_trades', '_trades_loading', '_trades_loaded', '_trades_total', '_trades_error']) delete summary[k];
    loadResultInPages({ ...pager, resume: { summary, groups: r.grouped_trades || [] } })
      .catch(e => setBtError(`Trades could not be loaded (${httpErrorMessage(e)}).`));
  }, [loadResultInPages]);

  const loadLatestInPages = useCallback(() => loadResultInPages({
    // A backend without the summary route answers 404: use the one-payload result.
    fetchSummary: () => getLatestResultSummary().then(r => r.data).catch(e => {
      if (e?.response?.status === 404) return getLatestBacktestResult().then(r => r.data);
      throw e;
    }),
    fetchPage: (offset, limit) => getLatestResultTrades(offset, limit).then(r => r.data),
  }), [loadResultInPages]);

  // Progress frames re-render the whole page, so apply at most five a second;
  // 'complete' and errors are applied at once and cancel anything pending.
  const progressTimerRef = useRef(null);
  const pendingProgressRef = useRef(null);
  const cancelThrottledProgress = useCallback(() => {
    if (progressTimerRef.current) clearTimeout(progressTimerRef.current);
    progressTimerRef.current = null;
    pendingProgressRef.current = null;
  }, []);
  const throttledProgress = useCallback((m) => {
    pendingProgressRef.current = m;
    if (progressTimerRef.current) return;
    progressTimerRef.current = setTimeout(() => {
      progressTimerRef.current = null;
      if (pendingProgressRef.current) setProgress(pendingProgressRef.current);
      pendingProgressRef.current = null;
    }, 200);
  }, []);
  useEffect(() => cancelThrottledProgress, [cancelThrottledProgress]);

  useEffect(() => {
    const h = e => {
      try {
        const m = e.detail; // 'ws-message' event from useBackendConnection.js passes parsed data in detail
        if (m.type === 'backtest_progress') {
          if (m.stage !== 'complete') { throttledProgress(m); return; }
          cancelThrottledProgress();
          setProgress(m);
          if (m.stage === 'complete') {
            if (m.result) {
              // Legacy path: an older backend still inlines the whole run.
              applyFreshResult(m.result);
              if (m.result.run_logs) setEvents(m.result.run_logs);
              setTimeout(() => setProgress(null), 2000);
            } else {
              // The backend now announces completion with a headline envelope
              // and leaves the body to be fetched. Pushing a finished run
              // (megabytes: per-bar equity curve, every leg, every group, the
              // log tail) through one WebSocket frame froze this tab on parse
              // and re-render, and when the frame was too big to send at all
              // the backend silently dropped the socket — which is why results
              // only appeared after a manual page refresh. The refresh worked
              // because it fetched over REST; so does this.
              loadLatestInPages()
                .catch(e => setBtError(
                  `Run finished but its results could not be loaded (${httpErrorMessage(e)}). Reload to retry.`))
                .finally(() => setProgress(null));
            }
          }
        } else if (m.type === 'backtest_error') {
          // Backend task failed (e.g. an exception inside the engine) —
          // the request itself already returned "started" successfully,
          // so mutation.isError never fires for this. Clear progress so
          // isRunning drops back to false and show the failure message.
          cancelThrottledProgress();
          setProgress(null);
          setBtError(m.message || 'Backtest failed. Check the activity log for details.');
        }
      } catch { }
    };
    window.addEventListener('ws-message', h);
    return () => window.removeEventListener('ws-message', h);
  }, [applyFreshResult, throttledProgress, cancelThrottledProgress, loadLatestInPages]);

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
          loadLatestInPages().catch(e => setBtError(
            `Run finished but its results could not be loaded (${httpErrorMessage(e)}). Reload to retry.`));
        }
      }).catch(() => { });
    }
  }, [status, isAuth]);

  const { data: backtests, refetch } = useQuery({ queryKey: ['backtests'], queryFn: () => getBacktests().then(r => r.data), enabled: status === 'ONLINE' && isAuth });

  const mutation = useMutation({
    mutationFn: () => {
      cancelResultLoad();
      setResult(null);
      setEvents([]);
      setBtError(null);
      const validStrats = VALID_STRATEGIES;
      const payload_strategy = validStrats.includes(form.strategy_id) ? form.strategy_id : 'APA_v1';
      const sp = singleSlot.strategy_params || {};

      // Account-level risk defaults come from Settings, not from this page: the
      // slot's own values (slot_risk) are layered on top by the backend.
      const acct = userCfg?.config?.risk || {};
      const payload = {
        ...form,
        risk_per_trade_pct: acct.risk_per_trade_pct ?? form.risk_per_trade_pct,
        max_risk_hard_cap_pct: acct.max_risk_hard_cap_pct ?? form.max_risk_hard_cap_pct,
        min_rr: acct.min_rr ?? form.min_rr,
        tp_count: acct.tp_count ?? form.tp_count,
        strategy_id: payload_strategy,
        start_date: form.start_date || undefined,
        end_date: form.end_date || undefined,
        // BacktestRequest has no top-level field for these three, but it spreads
        // `risk_config` last into the engine's merged risk config, so this is the
        // supported channel for RiskParams fields the request model does not name.
        risk_config: {
          ...savedRiskPassthrough(userCfg?.config, form),
          prop_firm: form.prop_firm,
          min_sl_pips: form.min_sl_pips,
          max_account_leverage: form.max_account_leverage,
          be_buffer_atr_mult: form.be_buffer_atr_mult,
        },
        strategy_params: sp,
        // This slot's own risk: what the editor shows, which starts from the slot
        // saved in Settings for this symbol + strategy.
        slot_risk: singleSlot.risk || {},
        // Same switch live uses, so a slot's exits are the slot's exits on both.
        use_strategy_exit_defaults: exitDefaultsForRun(),
        replay_enabled: !!form.live_chart,
      };
      if (form.manual_bias && form.manual_bias !== 'NONE') {
        payload.manual_bias_overrides = { [form.symbol]: form.manual_bias };
      }

      // Every strategy's params now come from its own nested form slice via
      // buildPortfolioStrategyParams(), so single-symbol and portfolio runs build
      // strategy_params through exactly one code path. The previous per-strategy
      // chain here re-read flat top-level fields, which meant HTFFVGFlip_v1 and
      // BiasIFVG_v1 shared one `session_start`/`session_cutoff`/`target_rr`
      // between them — configuring one silently reconfigured the other.

      // Simulation costs (BUG-8/BUG-9). null = "not set by the user": the engine
      // then sources spread/commission/slippage from live MT5 symbol data rather
      // than modelling a zero-cost market.
      payload.slippage_pips = costOrAuto(form.slippage_pips);
      payload.commission_per_lot = costOrAuto(form.commission_per_lot);
      payload.spread_pips = costOrAuto(form.spread_pips);
      payload.simulate_wicks = form.simulate_wicks ?? true;

      return runBacktest(payload);
    },
    onSuccess: res => {
      // Background task started, result will be pushed via WebSocket or polling
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
    },
    // [17.3] Without this an HTTP rejection never reached the user.
    onError: (e) => setBtError(httpErrorMessage(e)),
  });

  const handleSave = () => {
    if (!result) return;
    setShowSaveModal(true);
  };

  const handleSaveSuccess = () => {
    setShowSaveModal(false);
    setResult(null);
    clearCachedResult();
    queryClient.invalidateQueries({ queryKey: ['backtests'] });
    refetch();
    setTimeout(() => {
      document.getElementById('saved-backtests')?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
  };

  const handleDismiss = () => {
    cancelResultLoad();
    setResult(null);
    clearCachedResult();
  };
  const handleDelete = async id => { await deleteBacktest(id); refetch(); };
  const handleView = async id => {
    setIsLoadingDetail(true);
    // Scroll immediately to show loader
    setTimeout(() => {
      if (resultsRef.current) resultsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);

    try {
      await loadResultInPages({
        isSaved: true,
        fetchSummary: () => getBacktestSummary(id).then(r => {
          const d = r.data || {};
          return {
            ...d.run, equity_curve: d.equity_curve, report: d.run, run_logs: d.run_logs || [],
            grouped_trades: d.grouped_trades || [],
            trades_paged: !!d.trades_paged, trade_groups_total: d.trade_groups_total,
          };
        }),
        fetchPage: (offset, limit) => getBacktestTrades(id, offset, limit).then(r => r.data),
      });
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

  return (<>
    <div className="page-header"><h2><FlaskConical size={22} style={{ display: 'inline', marginRight: 8 }} />Backtester</h2><p>Test strategies with independent risk parameters</p></div>
    <div className="grid-2">
      <div className="card">
        <div className="card-header" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: "100%" }}>
            <div><span className="card-title">Configuration</span></div>
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
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div><label>Strategy Engine</label><select value={VALID_STRATEGIES.includes(form.strategy_id) ? form.strategy_id : 'APA_v1'} onChange={e => setForm({ ...form, strategy_id: e.target.value })}>{STRATEGY_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></div>
                <div>
                  <label>Symbol</label>
                  <SymbolPicker
                    value={form.symbol}
                    onChange={val => setForm({ ...form, symbol: val })}
                    options={backtestSymbols}
                    placeholder="Type to find symbol"
                  />
                </div>
              </div>
              {/* This slot's own parameters and risk, in the same editor Settings
                  uses — so the slot you test is the slot that trades. */}
              <SlotEditor
                slot={singleSlot}
                schema={schema}
                accountRisk={userCfg?.config?.risk}
                strategyDefaults={userCfg?.config?.[STRATEGY_GROUP[form.strategy_id]] || {}}
                showEnabled={false}
                showSymbol={false}
                collapsible
                defaultOpen={false}
                onChange={next => setForm(prev => ({
                  ...prev,
                  strategy_id: next.strategy_id,
                  slot_key: `${prev.symbol}::${next.strategy_id}`,
                  slot_strategy_params: next.strategy_params || {},
                  slot_risk: next.risk || {},
                  use_measured_params: next.use_measured_params !== false,
                }))}
              />
            </>
          ) : (
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 12, background: 'var(--bg-secondary)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>Portfolio Symbols ({portfolioSymbols.length})</span>
                <button className="btn btn-sm btn-ghost" onClick={addPortfolioSymbol}><PlusCircle size={14} style={{ marginRight: 4 }} /> Add</button>
              </div>
              <div className="slot-list">
                {portfolioSymbols.map((item, idx) => (
                  <SlotEditor
                    key={idx}
                    slot={{
                      symbol: item.symbol,
                      strategy_id: item.strategy_id,
                      strategy_params: item.strategy_params || {},
                      risk: item.risk || {},
                      use_measured_params: item.use_measured_params !== false,
                    }}
                    schema={schema}
                    accountRisk={userCfg?.config?.risk}
                    strategyDefaults={userCfg?.config?.[STRATEGY_GROUP[item.strategy_id]] || {}}
                    symbols={backtestSymbols}
                    showEnabled={false}
                    collapsible
                    defaultOpen={false}
                    onChange={next => setPortfolioSymbols(rows => rows.map((r, i) => (i === idx ? {
                      ...r,
                      symbol: next.symbol,
                      strategy_id: next.strategy_id,
                      strategy_params: next.strategy_params || {},
                      risk: next.risk || {},
                      use_measured_params: next.use_measured_params !== false,
                    } : r)))}
                    onRemove={portfolioSymbols.length > 1 ? () => removePortfolioSymbol(idx) : undefined}
                    onDuplicate={() => setPortfolioSymbols(rows => [...rows, { ...rows[idx] }])}
                  />
                ))}
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 12 }}>
                Every row is its own slot: its own strategy parameters, its own risk engine and its
                own limits. Nothing a row sets can use up or trip another row&apos;s budget. Rows
                start from the slot saved in Settings for that symbol and strategy.
              </p>
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
          {/* Risk, targets and exits are the SLOT's — edit them in the slot above
              (single) or on the row (portfolio). What a slot does not set falls back
              to the account defaults on Settings > Risk, exactly as live resolves it. */}
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            Risk, targets, break-even and trailing come from the slot{activeTab === 'single' ? ' above' : ' on each row'};
            anything it does not set uses the account defaults in Settings &gt; Risk.
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', textTransform: 'none' }}>
              <input type="checkbox" checked={form.prop_firm?.account_mode === 'prop_firm'} onChange={e => u('prop_firm', { ...form.prop_firm, account_mode: e.target.checked ? 'prop_firm' : 'personal' })} style={{ width: 14, height: 14 }} />
              Enable Prop Firm Rules
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', textTransform: 'none' }}
              title="Stream bars to a live chart while the run is in flight. Off keeps long runs light; the replay is still there afterwards.">
              <input type="checkbox" checked={!!form.live_chart} onChange={e => u('live_chart', e.target.checked)} style={{ width: 14, height: 14 }} />
              Live chart while running
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


          {/* Risk, targets, break-even and trailing are the SLOT's, and are
              edited in the editor above (single) or on each row (portfolio).
              They are deliberately not repeated here: a second global copy is
              overridden by the slot for every portfolio row, so it would look
              live while changing nothing. Account-level settings live in
              Settings > Defaults and are sent with the run as they are saved. */}

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
                    // Routed through the shared axios instance (services/api.js)
                    // rather than a raw fetch() so this picks up the configurable
                    // backend URL, the correct auth token key, and the
                    // auto-refresh interceptor instead of always sending
                    // "Authorization: Bearer null" against a hardcoded /api path.
                    const { data } = await getSymbolCosts(sym);
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
            <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 8 }}>
              Leave a field empty to have the backend source it from your broker (live MT5 spread, swap and stops level; commission derived from deal history).
              Entering a number — <strong>including 0</strong> — overrides that and is treated as a deliberate choice, so an explicit 0 models a zero-cost market.
              {COST_FIELDS.some(f => form[f] !== '' && form[f] !== null && form[f] !== undefined) && (
                <span style={{ color: 'var(--yellow)' }}> Some costs are currently overridden manually.</span>
              )}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginTop: 6 }}>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Slippage (pips)</label>
                <input type="number" step="0.1" min="0" placeholder="Auto (from broker)" value={form.slippage_pips ?? ''} onChange={e => u('slippage_pips', e.target.value === '' ? '' : +e.target.value)} />
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Extra pips added to entry price against the trade direction (simulates broker fill slippage). 1–3 pips typical for Forex. Empty = auto.</div>
              </div>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Commission / Lot (account currency)</label>
                <input type="number" step="0.5" min="0" placeholder="Auto (from broker)" value={form.commission_per_lot ?? ''} onChange={e => u('commission_per_lot', e.target.value === '' ? '' : +e.target.value)} />
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Round-turn commission per lot deducted from PnL. Typical: $3.50–$7.00/lot for ECN brokers. Empty = auto.</div>
              </div>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Spread (pips)</label>
                <input type="number" step="0.1" min="0" placeholder="Auto (from broker)" value={form.spread_pips ?? ''} onChange={e => u('spread_pips', e.target.value === '' ? '' : +e.target.value)} />
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 2 }}>Fixed bid/ask spread cost deducted at entry. Typical: 0.5–2 pips Forex, wider on synthetics/metals. Empty = auto.</div>
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

            <button className="btn btn-secondary btn-sm" onClick={() => { skipPersistRef.current = true; localStorage.removeItem(STORAGE_KEY); location.reload(); }} title="Reset to defaults" style={{ whiteSpace: 'nowrap' }}>
              <X size={14} /> Reset
            </button>
          </div>
          {(isRunning || progress) && <ProgressBar progress={progress} />}
          {mutation.isError && <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>Error: {mutation.error?.response?.data?.detail || mutation.error?.message || 'Failed'}</div>}
          {btError && (
            <div style={{ color: 'var(--red)', fontSize: '0.8rem', marginTop: 6 }}>
              Backtest failed: {btError}
              {/* [17.3] Bug B2 recovery. A stale "running" flag rejects every
                  subsequent request for up to an hour. `handleStop` already
                  clears it, but the Stop button only renders while isRunning
                  is true — and a 400 makes the mutation fail, so isRunning goes
                  false and the button vanishes exactly when it is needed. This
                  surfaces the same action on the error itself. */}
              {/already running/i.test(String(btError)) && (
                <div style={{ marginTop: 6 }}>
                  <button
                    className="btn btn-sm"
                    onClick={async () => { await handleStop(); setBtError(null); }}
                    title="Clears the stuck 'running' flag left by a backtest whose client or server died mid-run."
                  >
                    Clear stuck run
                  </button>
                  <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>
                    A previous run did not finish cleanly. Clearing lets you start again.
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title"><Terminal size={14} style={{ display: 'inline', marginRight: 6 }} />Live Logs</span></div>
        <LiveLogPanel events={events} />
      </div>
    </div>

    <div ref={resultsRef} style={{ marginTop: 40 }}>
      {isLoadingDetail && (
        <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
          <Loader2 size={32} className="spinner" style={{ marginBottom: 16 }} />
          <div>Loading backtest details...</div>
        </div>
      )}
      {/* [Phase 13 C.1] The replay chart replaces the skeleton loader that used
          to occupy this slot. It stays mounted after the run finishes so the
          same view can be scrubbed, rather than being swapped for a spinner and
          then thrown away. */}
      {(isRunning || result) && (
        <BacktestReplay progress={progress} result={result} isRunning={isRunning} live={!!form.live_chart} />
      )}
      {result && !isLoadingDetail && !isRunning && <BacktestResults result={result} onSave={handleSave} onDismiss={handleDismiss} onClose={() => { cancelResultLoad(); setResult(null); }} onRetryTrades={() => retryTrades(result)} isSaving={isSaving} />}
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