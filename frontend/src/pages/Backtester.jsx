import { useState, useEffect } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { FlaskConical, Play, Trash2, Eye, Save, X, ChevronDown, ChevronRight, Loader2, Clock, Target, Shield } from 'lucide-react';
import { runBacktest, getBacktests, deleteBacktest, getBacktest, saveBacktest } from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, BarChart, Bar } from 'recharts';

const SYMBOLS = [
  'XAUUSD','EURUSD','GBPUSD','USDJPY','US30','BTCUSD',
  'Volatility 10 Index','Volatility 25 Index','Volatility 50 Index',
  'Volatility 75 Index','Volatility 100 Index','Volatility 150 Index','Volatility 250 Index',
  'Boom 300 Index','Boom 500 Index','Boom 1000 Index',
  'Crash 300 Index','Crash 500 Index','Crash 1000 Index',
  'Jump 10 Index','Jump 25 Index','Jump 50 Index','Jump 75 Index','Jump 100 Index',
  'Step Index','Range Break 100 Index','Range Break 200 Index',
];

const PIE_COLORS = ['#3fb68b','#58a6ff','#d29922','#f0883e','#bc8cff','#f85149','#8b949e','#79c0ff'];

function formatTime(val) {
  if (!val) return '—';
  if (typeof val === 'string' && val.includes('T')) {
    const d = new Date(val);
    return d.toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
  }
  if (typeof val === 'number' && val > 1e9) {
    const d = new Date(val * 1000);
    return d.toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
  }
  return String(val);
}

function formatDuration(mins) {
  if (!mins || mins <= 0) return '—';
  if (mins < 60) return `${mins.toFixed(0)}m`;
  if (mins < 1440) return `${(mins/60).toFixed(1)}h`;
  return `${(mins/1440).toFixed(1)}d`;
}

function ProgressBar({ progress }) {
  if (!progress || progress.pct === undefined) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 4 }}>
        <span>{progress.message || progress.stage}</span>
        <span>{progress.pct}%</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${progress.pct}%`, background: 'linear-gradient(90deg, var(--blue), var(--green))', borderRadius: 3, transition: 'width 0.3s ease' }} />
      </div>
    </div>
  );
}

function ExpandableTradeRow({ trade, index }) {
  const [open, setOpen] = useState(false);
  const pnl = trade.pnl || 0;
  const isWin = pnl >= 0;

  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: 'pointer' }}>
        <td>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</td>
        <td>{index + 1}</td>
        <td><strong>{trade.symbol}</strong></td>
        <td><span className={`badge ${trade.direction === 'BUY' ? 'badge-green' : 'badge-red'}`}>{trade.direction === 'BUY' ? '▲' : '▼'}</span></td>
        <td>{typeof trade.entry_price === 'number' ? trade.entry_price.toFixed(5) : trade.entry_price}</td>
        <td>{typeof trade.exit_price === 'number' ? trade.exit_price.toFixed(5) : trade.exit_price}</td>
        <td><span className="badge badge-blue">{trade.exit_reason}</span></td>
        <td style={{ color: isWin ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${pnl.toFixed(2)}</td>
        <td>{formatDuration(trade.duration_minutes)}</td>
        <td>{trade.session || '—'}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10} style={{ padding: 0, border: 'none' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '12px 16px', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)', margin: '4px 8px' }}>
              <div>
                <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>Entry Confirmations</div>
                {(trade.entry_confirmations || []).map((c, i) => (
                  <div key={i} style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', padding: '2px 0', borderBottom: '1px solid var(--border)' }}>{c}</div>
                ))}
                {(!trade.entry_confirmations || !trade.entry_confirmations.length) && (
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>No confirmation data</div>
                )}
                <div style={{ marginTop: 8, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  <Clock size={10} style={{ display: 'inline', marginRight: 4 }} />Entry: {formatTime(trade.entry_time_iso || trade.entry_time)}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>Exit Confirmations</div>
                {(trade.exit_confirmations || []).map((c, i) => (
                  <div key={i} style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', padding: '2px 0', borderBottom: '1px solid var(--border)' }}>{c}</div>
                ))}
                {(!trade.exit_confirmations || !trade.exit_confirmations.length) && (
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>No confirmation data</div>
                )}
                <div style={{ marginTop: 8, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  <Clock size={10} style={{ display: 'inline', marginRight: 4 }} />Exit: {formatTime(trade.exit_time_iso || trade.exit_time)}
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function BacktestResults({ result, onSave, onDismiss, isSaving }) {
  const report = result.report || {};
  const trades = result.trades || [];
  const equityData = (result.equity_curve || []).map((val, i) => ({ bar: i, equity: val }));

  const tpDist = Object.entries(report).filter(([k]) => k.endsWith('_hit_rate') || k === 'sl_hit_rate')
    .map(([k, v]) => ({ name: k.replace('_hit_rate','').toUpperCase(), value: Math.round((v||0) * (result.total_trades || 1)) }))
    .filter(d => d.value > 0);

  const sessionData = [
    { session: 'London', rate: (report.london_win_rate || 0) * 100 },
    { session: 'NY', rate: (report.ny_win_rate || 0) * 100 },
    { session: 'Overlap', rate: (report.overlap_win_rate || 0) * 100 },
  ];

  return (
    <div className="card" style={{ marginTop: 20 }}>
      <div className="card-header">
        <span className="card-title">Backtest Results</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary btn-sm" onClick={onSave} disabled={isSaving}>
            <Save size={14} /> {isSaving ? 'Saving...' : 'Save Results'}
          </button>
          <button className="btn btn-danger btn-sm" onClick={onDismiss}>
            <X size={14} /> Dismiss
          </button>
        </div>
      </div>

      {/* Metadata */}
      {(result.invalid_signals > 0 || result.deferred_activations > 0) && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 12, fontSize: '0.8rem' }}>
          {result.invalid_signals > 0 && (
            <span style={{ color: 'var(--yellow)' }}><Shield size={12} style={{ display: 'inline', marginRight: 4 }} />{result.invalid_signals} invalid signals rejected</span>
          )}
          {result.deferred_activations > 0 && (
            <span style={{ color: 'var(--blue)' }}><Target size={12} style={{ display: 'inline', marginRight: 4 }} />{result.deferred_activations} deferred TPs activated</span>
          )}
        </div>
      )}

      {/* Summary Metrics */}
      <div className="metrics-grid" style={{ marginBottom: 16 }}>
        <div className="metric-card"><div className="metric-label">Final Balance</div><div className={`metric-value ${result.final_balance >= result.initial_balance ? 'green' : 'red'}`}>${result.final_balance?.toFixed(2)}</div></div>
        <div className="metric-card"><div className="metric-label">Trades</div><div className="metric-value blue">{result.total_trades}</div></div>
        <div className="metric-card"><div className="metric-label">Win Rate</div><div className={`metric-value ${report.win_rate >= 0.55 ? 'green' : 'yellow'}`}>{((report.win_rate||0)*100).toFixed(1)}%</div></div>
        <div className="metric-card"><div className="metric-label">Sharpe</div><div className="metric-value blue">{(report.sharpe_ratio||0).toFixed(2)}</div></div>
        <div className="metric-card"><div className="metric-label">Profit Factor</div><div className="metric-value green">{(report.profit_factor||0).toFixed(2)}</div></div>
        <div className="metric-card"><div className="metric-label">Max DD</div><div className="metric-value red">{((report.max_drawdown_pct||0)*100).toFixed(1)}%</div></div>
        <div className="metric-card"><div className="metric-label">Expectancy (R)</div><div className="metric-value blue">{(report.expectancy_r||0).toFixed(2)}</div></div>
        <div className="metric-card"><div className="metric-label">Sortino</div><div className="metric-value blue">{(report.sortino_ratio||0).toFixed(2)}</div></div>
      </div>

      {/* Charts Row */}
      <div className="grid-3" style={{ marginBottom: 16 }}>
        {equityData.length > 1 && (
          <div className="card" style={{ padding: 12 }}>
            <h4 style={{ marginBottom: 8 }}>Equity Curve</h4>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={equityData}>
                <XAxis dataKey="bar" hide />
                <YAxis domain={['auto', 'auto']} fontSize={10} />
                <Tooltip formatter={(v) => `$${v.toFixed(2)}`} />
                <Area type="monotone" dataKey="equity" stroke="#3fb68b" fill="#3fb68b20" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
        {tpDist.length > 0 && (
          <div className="card" style={{ padding: 12 }}>
            <h4 style={{ marginBottom: 8 }}>TP Distribution</h4>
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={tpDist} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} label={({ name, value }) => `${name}: ${value}`}>
                  {tpDist.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="card" style={{ padding: 12 }}>
          <h4 style={{ marginBottom: 8 }}>Win Rate by Session</h4>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={sessionData}>
              <XAxis dataKey="session" fontSize={11} />
              <YAxis domain={[0, 100]} fontSize={10} />
              <Tooltip formatter={(v) => `${v.toFixed(1)}%`} />
              <Bar dataKey="rate" fill="#58a6ff" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Trade List — Expandable */}
      {trades.length > 0 && (
        <>
          <div className="card-header" style={{ marginTop: 8 }}>
            <span className="card-title">All Trades ({trades.length})</span>
          </div>
          <div className="table-wrapper" style={{ maxHeight: 500, overflow: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th style={{width:24}}></th><th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th>
                  <th>Result</th><th>P&L</th><th>Duration</th><th>Session</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => <ExpandableTradeRow key={i} trade={t} index={i} />)}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export default function Backtester() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [form, setForm] = useState({
    symbol: 'XAUUSD', timeframe: 'H1', initial_balance: 10000,
    risk_per_trade_pct: 1.0, min_rr: 3.0, tp_count: 3,
    session_filter_enabled: true,
    start_date: '', end_date: '', candle_count: 5000,
  });
  const [result, setResult] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [progress, setProgress] = useState(null);

  // Listen for WebSocket backtest progress events
  useEffect(() => {
    const handleWsMessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'backtest_progress') {
          setProgress(msg);
          if (msg.stage === 'complete') setTimeout(() => setProgress(null), 2000);
        }
      } catch {}
    };
    // Attach to existing WS if available
    if (window._algoEdgeWs) {
      window._algoEdgeWs.addEventListener('message', handleWsMessage);
      return () => window._algoEdgeWs?.removeEventListener('message', handleWsMessage);
    }
  }, []);

  const { data: backtests, refetch } = useQuery({
    queryKey: ['backtests'],
    queryFn: () => getBacktests().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const mutation = useMutation({
    mutationFn: () => runBacktest({
      symbol: form.symbol,
      timeframe: form.timeframe,
      initial_balance: form.initial_balance,
      start_date: form.start_date || undefined,
      end_date: form.end_date || undefined,
      candle_count: form.candle_count,
      tp_count: form.tp_count,
      session_filter_enabled: form.session_filter_enabled,
      risk_config: { risk_per_trade_pct: form.risk_per_trade_pct, min_rr: form.min_rr, tp_count: form.tp_count },
    }),
    onSuccess: (res) => { setResult(res.data); setProgress(null); },
    onError: () => setProgress(null),
  });

  const handleSave = async () => {
    if (!result) return;
    setIsSaving(true);
    try {
      await saveBacktest(result.backtest_id, {
        backtest_data: { ...result, strategy_id: 'SMC_v1', symbol: form.symbol, risk_config: form },
        save_mode: 'FULL',
      });
      setResult(null);
      refetch();
    } finally { setIsSaving(false); }
  };

  const handleDismiss = () => setResult(null);
  const handleDelete = async (id) => { await deleteBacktest(id); refetch(); };
  const handleView = async (id) => { const res = await getBacktest(id); setResult({ ...res.data.run, trades: res.data.trades, equity_curve: res.data.equity_curve, report: res.data.run }); };

  return (
    <>
      <div className="page-header">
        <h2><FlaskConical size={22} style={{ display: 'inline', marginRight: 8 }} />Backtester</h2>
        <p>Test strategies on historical data using the same risk engine as live trading</p>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-header"><span className="card-title">Configuration</span></div>
          <div style={{ display: 'grid', gap: 16 }}>
            <div>
              <label>Symbol</label>
              <select value={form.symbol} onChange={e => setForm({ ...form, symbol: e.target.value })}>
                {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label>Timeframe</label>
              <select value={form.timeframe} onChange={e => setForm({ ...form, timeframe: e.target.value })}>
                {['M5','M15','H1','H4','D1'].map(tf => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </div>

            {/* Date Range */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div><label>Start Date</label><input type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} /></div>
              <div><label>End Date</label><input type="date" value={form.end_date} onChange={e => setForm({ ...form, end_date: e.target.value })} /></div>
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: -8 }}>Leave dates empty to use the last N candles instead.</div>

            <div><label>Candle Count (if no dates)</label><input type="number" value={form.candle_count} onChange={e => setForm({ ...form, candle_count: +e.target.value })} min={100} max={10000} /></div>
            <div><label>Initial Balance ($)</label><input type="number" value={form.initial_balance} onChange={e => setForm({ ...form, initial_balance: +e.target.value })} /></div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div><label>Risk Per Trade (%)</label><input type="number" step="0.1" value={form.risk_per_trade_pct} onChange={e => setForm({ ...form, risk_per_trade_pct: +e.target.value })} /></div>
              <div><label>Minimum R:R</label><input type="number" step="0.5" value={form.min_rr} onChange={e => setForm({ ...form, min_rr: +e.target.value })} /></div>
            </div>

            {/* TP Count + Session Filter */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label>TP Count</label>
                <select value={form.tp_count} onChange={e => setForm({ ...form, tp_count: +e.target.value })}>
                  {[1,2,3,4,5].map(n => <option key={n} value={n}>{n} TP{n > 1 ? 's' : ''}</option>)}
                </select>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 2 }}>
                  {form.tp_count <= 2 ? 'All at entry' : `TP1-2 at entry, TP3${form.tp_count > 3 ? '-'+form.tp_count : ''} deferred`}
                </div>
              </div>
              <div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                  <input type="checkbox" checked={form.session_filter_enabled} onChange={e => setForm({ ...form, session_filter_enabled: e.target.checked })} style={{ width: 14, height: 14 }} />
                  Session Filter
                </label>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 2 }}>
                  {form.session_filter_enabled ? 'London/NY only' : 'All sessions'}
                </div>
              </div>
            </div>

            <button className="btn btn-primary" onClick={() => mutation.mutate()} disabled={mutation.isPending || status !== 'ONLINE'} style={{ width: '100%' }}>
              {mutation.isPending ? <><Loader2 size={16} className="spin" /> Running Backtest...</> : <><Play size={16} /> Run Backtest</>}
            </button>

            {/* Progress Bar */}
            {(mutation.isPending || progress) && <ProgressBar progress={progress} />}

            {mutation.isError && (
              <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>
                Error: {mutation.error?.response?.data?.detail || mutation.error?.message || 'Backtest failed'}
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <span className="card-title">Saved Backtests</span>
            <span className="badge badge-blue">{backtests?.length || 0}</span>
          </div>
          <div className="table-wrapper">
            <table>
              <thead><tr><th>Symbol</th><th>Trades</th><th>Win Rate</th><th>P&L</th><th></th></tr></thead>
              <tbody>
                {backtests?.length ? backtests.map(bt => (
                  <tr key={bt.id}>
                    <td><strong>{bt.symbol}</strong></td>
                    <td>{bt.total_trades}</td>
                    <td>{((bt.win_rate||0)*100).toFixed(0)}%</td>
                    <td style={{ color: bt.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>${(bt.total_pnl||0).toFixed(2)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => handleView(bt.id)}><Eye size={12} /></button>
                        <button className="btn btn-danger btn-sm" onClick={() => handleDelete(bt.id)}><Trash2 size={12} /></button>
                      </div>
                    </td>
                  </tr>
                )) : (
                  <tr><td colSpan={5}><div className="empty-state"><FlaskConical /><h3>No saved backtests</h3></div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {result && <BacktestResults result={result} onSave={handleSave} onDismiss={handleDismiss} isSaving={isSaving} />}
    </>
  );
}
