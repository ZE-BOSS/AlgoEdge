import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { decimate } from '../utils/decimate';
import { BarChart3, TrendingUp, Target, Shield, Activity, Clock } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, AreaChart, Area, CartesianGrid } from 'recharts';
import { getStats, getTrades } from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';

const COLORS = ['#3fb68b', '#58a6ff', '#bc8cff', '#f0883e', '#79c0ff', '#f85149', '#d29922', '#8b949e'];

function StatCard({ label, value, color, icon: Icon }) {
  return (
    <div className="metric-card">
      <div className="metric-label"><Icon size={12} style={{ marginRight: 4, display: 'inline' }} />{label}</div>
      <div className={`metric-value ${color}`}>{value}</div>
    </div>
  );
}

export default function Analytics() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: () => getStats().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const { data: trades } = useQuery({
    queryKey: ['trades', 'analytics'],
    queryFn: () => getTrades({ status: 'CLOSED', limit: 200 }).then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const s = stats || {};

  // Build equity curve from stats if available (MT5 verified), else fallback to DB trades.
  // MEMOISED AND DECIMATED. This ran on every render and kept every point: a
  // 72,578-point curve meant 72k toFixed calls plus 72k object allocations per
  // render, and then Recharts drew all 72k - which is what hung this page on
  // large backtests. The chart cannot resolve more than a few hundred points.
  const equityCurve = useMemo(() => {
    if (s.equity_curve && s.equity_curve.length > 0) {
      return decimate(s.equity_curve, 500).map((balance, i) => ({ trade: i, balance: +balance.toFixed(2) }));
    }
    const out = [];
    let balance = (trades && trades.length > 0) ? (trades[0].balance_before || 10000) : 10000;
    (trades || []).forEach((t, i) => {
      balance += (t.pnl || 0);
      out.push({ trade: i + 1, balance: +balance.toFixed(2) });
    });
    return decimate(out, 500);
  }, [s.equity_curve, trades]);

  // TP distribution (TP1-TP5)
  const tpDist = [
    { name: 'TP1', value: +(((s.tp1_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'TP2', value: +(((s.tp2_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'TP3', value: +(((s.tp3_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'TP4', value: +(((s.tp4_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'TP5', value: +(((s.tp5_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'SL', value: +(((s.sl_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'Trail', value: +(((s.trail_hit_rate || 0) * 100).toFixed(1)) || 0 },
    { name: 'BE', value: +(((s.be_hit_rate || 0) * 100).toFixed(1)) || 0 },
  ].filter(d => d.value > 0);

  // P&L per trade
  const pnlBars = (trades || []).slice(-50).map((t, i) => ({
    trade: i + 1,
    pnl: +(t.pnl || 0).toFixed(2),
    fill: t.pnl >= 0 ? '#3fb68b' : '#f85149',
  }));

  return (
    <>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2><BarChart3 size={22} style={{ display: 'inline', marginRight: 8 }} />Analytics</h2>
          <p>Performance metrics, equity curves, and trade distribution analysis</p>
        </div>
        <div className="badge badge-green" title="Data synced directly from MT5 terminal history">
          ✓ MT5 Verified
        </div>
      </div>

      <div className="metrics-grid">
        <StatCard label="Win Rate" value={`${((s.win_rate || 0) * 100).toFixed(1)}%`} color={s.win_rate >= 0.55 ? 'green' : 'yellow'} icon={Target} />
        <StatCard label="Profit Factor" value={(s.profit_factor || 0).toFixed(2)} color="green" icon={TrendingUp} />
        <StatCard label="Sharpe Ratio" value={(s.sharpe_ratio || 0).toFixed(2)} color="blue" icon={Activity} />
        <StatCard label="Max DD (of capital)" value={`${((s.max_drawdown_pct || 0) * 100).toFixed(1)}%`} color="red" icon={Shield} />
        {/* Peak-relative companion — the comparable figure once the account
            has grown. See metrics.calculate_max_drawdown_of_peak. */}
        <StatCard label="Max DD (of peak)" value={`${((s.max_drawdown_pct_of_peak || 0) * 100).toFixed(1)}%`} color="red" icon={Shield} />
        <StatCard label="Expectancy" value={`$${(s.expectancy || 0).toFixed(2)}`} color="green" icon={TrendingUp} />
        <StatCard label="Max Consec Losses" value={s.max_consecutive_losses || 0} color="red" icon={Clock} />
      </div>

      <div className="grid-2" style={{ marginBottom: 24 }}>
        <div className="card">
          <div className="card-header"><span className="card-title">Equity Curve</span></div>
          {equityCurve.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="colorBalance" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3fb68b" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#3fb68b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
                <XAxis dataKey="trade" stroke="#484f58" fontSize={11} />
                <YAxis stroke="#484f58" fontSize={11} />
                <Tooltip
                  contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', fontSize: 12 }}
                />
                <Area type="monotone" dataKey="balance" stroke="#3fb68b" fill="url(#colorBalance)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state"><h3>No trade data yet</h3></div>
          )}
        </div>

        <div className="card">
          <div className="card-header"><span className="card-title">Exit Distribution</span></div>
          {tpDist.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie data={tpDist} cx="50%" cy="50%" innerRadius={70} outerRadius={110} dataKey="value" label={({ name, value }) => `${name}: ${value}%`}>
                  {tpDist.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state"><h3>No exit data yet</h3></div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">P&L Per Trade (Last 50)</span></div>
        {pnlBars.length > 0 ? (
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={pnlBars}>
              <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
              <XAxis dataKey="trade" stroke="#484f58" fontSize={11} />
              <YAxis stroke="#484f58" fontSize={11} />
              <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', fontSize: 12 }} />
              <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                {pnlBars.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="empty-state"><h3>No trade data yet</h3></div>
        )}
      </div>
    </>
  );
}
