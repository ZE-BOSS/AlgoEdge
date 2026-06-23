import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { FlaskConical, Play, Trash2, Eye, Save, X, Download, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
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

function BacktestResults({ result, onSave, onDismiss, isSaving }) {
  const [expandedTrade, setExpandedTrade] = useState(new Set());
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

      {/* Summary Metrics */}
      <div className="metrics-grid" style={{ marginBottom: 16 }}>
        <div className="metric-card">
          <div className="metric-label">Final Balance</div>
          <div className={`metric-value ${result.final_balance >= result.initial_balance ? 'green' : 'red'}`}>
            ${result.final_balance?.toFixed(2)}
          </div>
        </div>
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

      {/* Trade List */}
      {trades.length > 0 && (
        <>
          <div className="card-header" style={{ marginTop: 8 }}>
            <span className="card-title">All Trades ({trades.length})</span>
          </div>
          <div className="table-wrapper" style={{ maxHeight: 400, overflow: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th>
                  <th>SL</th><th>TP Hit</th><th>P&L</th><th>Exit</th><th>Session</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i}>
                    <td>{i+1}</td>
                    <td><strong>{t.symbol}</strong></td>
                    <td><span className={`badge ${t.direction==='BUY'?'badge-green':'badge-red'}`}>{t.direction==='BUY'?'▲':'▼'}</span></td>
                    <td>{typeof t.entry_price === 'number' ? t.entry_price.toFixed(2) : t.entry_price}</td>
                    <td>{typeof t.exit_price === 'number' ? t.exit_price.toFixed(2) : t.exit_price}</td>
                    <td>{typeof t.stop_loss === 'number' ? t.stop_loss.toFixed(2) : t.stop_loss}</td>
                    <td>{t.exit_reason}</td>
                    <td style={{ color: (t.pnl||0) >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                      ${(t.pnl||0).toFixed(2)}
                    </td>
                    <td><span className="badge badge-blue">{t.exit_reason}</span></td>
                    <td>{t.session || '—'}</td>
                  </tr>
                ))}
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
    risk_per_trade_pct: 1.0, min_rr: 3.0,
    start_date: '', end_date: '', candle_count: 5000,
  });
  const [result, setResult] = useState(null);
  const [isSaving, setIsSaving] = useState(false);

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
      risk_config: { risk_per_trade_pct: form.risk_per_trade_pct, min_rr: form.min_rr },
    }),
    onSuccess: (res) => setResult(res.data),
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
              <div>
                <label>Start Date</label>
                <input type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} />
              </div>
              <div>
                <label>End Date</label>
                <input type="date" value={form.end_date} onChange={e => setForm({ ...form, end_date: e.target.value })} />
              </div>
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: -8 }}>
              Leave dates empty to use the last N candles instead.
            </div>

            <div><label>Candle Count (if no dates)</label><input type="number" value={form.candle_count} onChange={e => setForm({ ...form, candle_count: +e.target.value })} min={100} max={10000} /></div>
            <div><label>Initial Balance ($)</label><input type="number" value={form.initial_balance} onChange={e => setForm({ ...form, initial_balance: +e.target.value })} /></div>
            <div><label>Risk Per Trade (%)</label><input type="number" step="0.1" value={form.risk_per_trade_pct} onChange={e => setForm({ ...form, risk_per_trade_pct: +e.target.value })} /></div>
            <div><label>Minimum R:R</label><input type="number" step="0.5" value={form.min_rr} onChange={e => setForm({ ...form, min_rr: +e.target.value })} /></div>
            <button className="btn btn-primary" onClick={() => mutation.mutate()} disabled={mutation.isPending || status !== 'ONLINE'} style={{ width: '100%' }}>
              {mutation.isPending ? <><Loader2 size={16} className="spin" /> Running Backtest...</> : <><Play size={16} /> Run Backtest</>}
            </button>
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
