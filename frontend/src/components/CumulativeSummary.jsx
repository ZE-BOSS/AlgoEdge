import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, CartesianGrid } from 'recharts';
import { X, Loader2 } from 'lucide-react';
import { getBulkBacktests } from '../services/api';
import * as summaryEngine from '../utils/summaryEngine';

function fmtDur(m) { 
  if (!m || m <= 0) return '—'; 
  if (m < 60) return `${m.toFixed(0)}m`; 
  if (m < 1440) return `${(m / 60).toFixed(1)}h`; 
  return `${(m / 1440).toFixed(1)}d`; 
}

export default function CumulativeSummary({ selectedIds, onClose }) {
  const [period, setPeriod] = useState('day'); // 'day', 'week', 'month'
  const [expandedRows, setExpandedRows] = useState(new Set());
  
  const { data: bulkData, isLoading, isError } = useQuery({
    queryKey: ['bulkBacktests', Array.from(selectedIds).sort().join(',')],
    queryFn: () => getBulkBacktests(Array.from(selectedIds)).then(r => r.data.data),
    enabled: selectedIds.size > 0
  });

  const { trades, overallStats, symbolStats, sessionStats, equityCurve, periodMatrix } = useMemo(() => {
    if (!bulkData || !bulkData.length) return {};
    
    // 1. Merge all trades
    const merged = summaryEngine.mergeTrades(bulkData);
    
    // 2. Compute Top-Level Stats
    // We use the initial_balance of the first backtest for the portfolio starting balance
    const initialBalance = bulkData[0].initial_balance || 10000;
    const overallStats = summaryEngine.computePeriodStats(merged, initialBalance);
    
    // 3. Compute Symbol Stats
    const symbolStats = summaryEngine.computePerSymbolStats(merged);
    
    // 4. Compute Session Stats
    const sessionStats = summaryEngine.computeSessionStats(merged);
    
    // 5. Build Equity Curve
    const equityCurve = summaryEngine.buildEquityCurve(merged, initialBalance);
    
    // 6. Period Breakdown
    const buckets = summaryEngine.bucketByPeriod(merged, period);
    const periodMatrix = summaryEngine.computePeriodSymbolMatrix(buckets, initialBalance);
    
    return { trades: merged, overallStats, symbolStats, sessionStats, equityCurve, periodMatrix };
  }, [bulkData, period]);

  const toggleExpand = (rowPeriod) => {
    const next = new Set(expandedRows);
    if (next.has(rowPeriod)) next.delete(rowPeriod);
    else next.add(rowPeriod);
    setExpandedRows(next);
  };

  if (isLoading) {
    return (
      <div className="card" style={{ marginTop: 20, padding: 40, textAlign: 'center' }}>
        <Loader2 size={32} className="spinner" style={{ marginBottom: 16 }} />
        <div>Loading backtest details for {selectedIds.size} runs...</div>
      </div>
    );
  }

  if (isError || !overallStats) {
    return (
      <div className="card" style={{ marginTop: 20, padding: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <h3 style={{ margin: 0 }}>Error Loading Summary</h3>
          <button className="btn btn-secondary btn-sm" onClick={onClose}><X size={16} /></button>
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{ marginTop: 20 }}>
      <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <span className="card-title">Cumulative Summary</span>
          <span className="badge badge-blue" style={{ marginLeft: 8 }}>{selectedIds.size} Backtests</span>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <select value={period} onChange={e => setPeriod(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}>
            <option value="day">Daily Breakdown</option>
            <option value="week">Weekly Breakdown</option>
            <option value="month">Monthly Breakdown</option>
          </select>
          <button className="btn btn-secondary btn-sm" onClick={onClose}><X size={16} /> Close</button>
        </div>
      </div>
      
      <div style={{ padding: 20 }}>
        {/* TOP LEVEL METRICS */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 24 }}>
          <MetricCard title="Total Trades" value={overallStats.totalTrades} />
          <MetricCard title="Win Rate" value={`${(overallStats.winRate * 100).toFixed(1)}%`} color={overallStats.winRate >= 0.5 ? 'var(--green)' : 'var(--red)'} />
          <MetricCard title="Net P&L" value={`$${overallStats.pnl.toFixed(2)}`} color={overallStats.pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
          <MetricCard title="Profit Factor" value={overallStats.profitFactor >= 999 ? '∞' : overallStats.profitFactor.toFixed(2)} />
          <MetricCard title="Max Drawdown" value={`${(overallStats.maxDdPct * 100).toFixed(2)}%`} color="var(--red)" />
          <MetricCard title="Sharpe Ratio" value={overallStats.sharpe.toFixed(2)} />
          <MetricCard title="Expectancy (R)" value={overallStats.expectancyR.toFixed(2)} color={overallStats.expectancyR > 0 ? 'var(--green)' : 'var(--red)'} />
          <MetricCard title="Avg Duration" value={fmtDur(overallStats.avgDurationMin)} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 20, marginBottom: 24 }}>
          {/* EQUITY CURVE */}
          <div className="card" style={{ padding: 16 }}>
            <h4 style={{ margin: '0 0 16px 0', fontSize: '0.9rem' }}>Combined Equity Curve</h4>
            <div style={{ height: 250 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityCurve} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqColor" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--blue)" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="var(--blue)" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="index" tick={false} axisLine={false} />
                  <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
                  <Tooltip 
                    contentStyle={{ background: '#1c2128', border: '1px solid var(--border)', borderRadius: 4, fontSize: '0.8rem' }}
                    labelFormatter={() => ''}
                    formatter={(val, name, props) => [`$${val.toFixed(2)}`, `${props.payload.symbol} - ${new Date(props.payload.date).toLocaleDateString()}`]}
                  />
                  <Area type="monotone" dataKey="equity" stroke="var(--blue)" fillOpacity={1} fill="url(#eqColor)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          
          {/* SESSION STATS */}
          <div className="card" style={{ padding: 16 }}>
            <h4 style={{ margin: '0 0 16px 0', fontSize: '0.9rem' }}>Session Win Rates</h4>
            <div style={{ height: 250 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={Object.entries(sessionStats).map(([k,v]) => ({ name: k, winRate: v.winRate * 100, trades: v.trades })).filter(d => d.trades > 0)} layout="vertical" margin={{ top: 0, right: 0, left: 10, bottom: 0 }}>
                  <XAxis type="number" domain={[0, 100]} hide />
                  <YAxis dataKey="name" type="category" tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{ fill: 'rgba(255,255,255,0.05)' }} contentStyle={{ background: '#1c2128', border: '1px solid var(--border)', borderRadius: 4, fontSize: '0.8rem' }} formatter={v => `${Number(v).toFixed(1)}%`} />
                  <Bar dataKey="winRate" fill="var(--blue)" radius={[0, 4, 4, 0]} barSize={20} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
        
        {/* PER-SYMBOL TABLE */}
        <div style={{ marginBottom: 24 }}>
          <h4 style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>Per-Symbol Performance</h4>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Symbol (Strategy)</th>
                  <th>Trades</th>
                  <th>Win Rate</th>
                  <th>P&L</th>
                  <th>Avg Win</th>
                  <th>Avg Loss</th>
                  <th>Profit Factor</th>
                  <th>Max DD %</th>
                  <th>Sharpe</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(symbolStats).map(([sym, stats]) => (
                  <tr key={sym}>
                    <td><strong>{sym}</strong></td>
                    <td>{stats.totalTrades}</td>
                    <td style={{ color: stats.winRate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>{(stats.winRate * 100).toFixed(1)}% ({stats.wins}W / {stats.losses}L)</td>
                    <td style={{ color: stats.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${stats.pnl.toFixed(2)}</td>
                    <td>${stats.avgWin.toFixed(2)}</td>
                    <td>${stats.avgLoss.toFixed(2)}</td>
                    <td>{stats.profitFactor >= 999 ? '∞' : stats.profitFactor.toFixed(2)}</td>
                    <td style={{ color: 'var(--red)' }}>{(stats.maxDdPct * 100).toFixed(2)}%</td>
                    <td>{stats.sharpe.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        
        {/* PERIOD BREAKDOWN TABLE */}
        <div>
          <h4 style={{ margin: '0 0 12px 0', fontSize: '0.9rem', textTransform: 'capitalize' }}>{period} Breakdown</h4>
          <div className="table-wrapper" style={{ maxHeight: 600, overflow: 'auto' }}>
            <table>
              <thead style={{ position: 'sticky', top: 0, zIndex: 10, background: 'var(--bg-secondary)' }}>
                <tr>
                  <th style={{ width: 24 }}></th>
                  <th>Period</th>
                  <th>Trades</th>
                  <th>Win Rate</th>
                  <th>Period P&L</th>
                  <th>Cum. P&L</th>
                  <th>End Balance</th>
                  <th>Max DD %</th>
                  <th>Sharpe</th>
                </tr>
              </thead>
              <tbody>
                {periodMatrix.map(row => {
                  const isExpanded = expandedRows.has(row.period);
                  return (
                    <React.Fragment key={row.period}>
                      <tr onClick={() => toggleExpand(row.period)} style={{ cursor: 'pointer', background: isExpanded ? 'rgba(255,255,255,0.03)' : 'transparent' }}>
                        <td style={{ color: 'var(--text-muted)' }}>{isExpanded ? '▼' : '▶'}</td>
                        <td><strong>{row.period}</strong></td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span>{row.totalTrades}</span>
                            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                              {Object.entries(row.symbols).map(([sym, st]) => (
                                <span key={sym} className="badge badge-secondary" style={{ fontSize: '0.65rem' }}>{sym.split(' ')[0]}: {st.trades}</span>
                              ))}
                            </div>
                          </div>
                        </td>
                        <td style={{ color: row.winRate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>{(row.winRate * 100).toFixed(1)}%</td>
                        <td style={{ color: row.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>${row.pnl.toFixed(2)}</td>
                        <td style={{ color: row.cumulativePnl >= 0 ? 'var(--green)' : 'var(--red)' }}>${row.cumulativePnl.toFixed(2)}</td>
                        <td>${row.endBalance.toFixed(2)}</td>
                        <td style={{ color: 'var(--red)' }}>{(row.maxDdPct * 100).toFixed(2)}%</td>
                        <td>{row.sharpe.toFixed(2)}</td>
                      </tr>
                      {isExpanded && Object.entries(row.symbols).map(([sym, st]) => (
                        <tr key={`${row.period}-${sym}`} style={{ background: 'var(--bg-tertiary)', fontSize: '0.8rem' }}>
                          <td></td>
                          <td style={{ paddingLeft: 20 }}>└ {sym}</td>
                          <td>{st.trades}</td>
                          <td style={{ color: st.winRate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>{(st.winRate * 100).toFixed(1)}% ({st.wins}W / {st.losses}L)</td>
                          <td style={{ color: st.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>${st.pnl.toFixed(2)}</td>
                          <td colSpan={2}>Avg Win: ${st.avgWin.toFixed(2)} / Avg Loss: ${st.avgLoss.toFixed(2)}</td>
                          <td colSpan={2}></td>
                        </tr>
                      ))}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}

function MetricCard({ title, value, color }) {
  return (
    <div style={{ padding: '12px 16px', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: '1.2rem', fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}
