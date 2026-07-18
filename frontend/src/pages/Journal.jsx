import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, ChevronDown, ChevronRight, Bot, ExternalLink } from 'lucide-react';
import { getTrades, analyzeTrade } from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';

function MiniChart({ data }) {
  const containerRef = React.useRef(null);
  React.useEffect(() => {
    const candles = data?.candles || data;
    if (!containerRef.current || !candles || !Array.isArray(candles) || candles.length === 0) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 220,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8b949e',
        fontFamily: 'Inter',
      },
      grid: {
        vertLines: { color: '#21283640' },
        horzLines: { color: '#21283640' },
      },
      timeScale: { timeVisible: true },
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb68b', downColor: '#f85149',
      borderDownColor: '#f85149', borderUpColor: '#3fb68b',
      wickDownColor: '#f85149', wickUpColor: '#3fb68b',
    });
    
    // Ensure chronological order and valid timestamps
    const mapped = candles.map(c => {
      let t = c.time;
      if (typeof t === 'string') t = Math.floor(new Date(t).getTime() / 1000);
      return { time: t, open: c.open, high: c.high, low: c.low, close: c.close };
    }).sort((a, b) => a.time - b.time).filter((c, i, arr) => i === 0 || c.time > arr[i - 1].time);
    
    candleSeries.setData(mapped);
    chart.timeScale().fitContent();
    
    const handleResize = () => chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener('resize', handleResize);
    
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [data]);
  return <div ref={containerRef} style={{ width: '100%', height: 220, marginTop: 8 }} />;
}

function TradeRow({ trade }) {
  const [expanded, setExpanded] = useState(false);
  const [analysis, setAnalysis] = useState(trade.llm_analysis || null);
  const [analyzing, setAnalyzing] = useState(false);
  const isWin = trade.pnl > 0;

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      const res = await analyzeTrade({ trade_id: trade.id });
      setAnalysis(res.data.analysis);
    } catch (e) {
      setAnalysis('Analysis failed: ' + e.message);
    }
    setAnalyzing(false);
  };

  let chartDataObj = null;
  if (trade.chart_data) {
    try {
      chartDataObj = typeof trade.chart_data === 'string' ? JSON.parse(trade.chart_data) : trade.chart_data;
    } catch (e) {}
  }

  let durationStr = '—';
  if (trade.entry_time && trade.exit_time) {
    const diffMs = new Date(trade.exit_time) - new Date(trade.entry_time);
    const diffMins = Math.floor(diffMs / 60000);
    const hours = Math.floor(diffMins / 60);
    const mins = diffMins % 60;
    durationStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  }

  const formatTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <>
      <tr onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        <td>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td><strong>{trade.symbol}</strong></td>
        <td>
          <span className={`badge ${trade.direction === 'BUY' ? 'badge-green' : 'badge-red'}`}>
            {trade.direction === 'BUY' ? '▲ LONG' : '▼ SHORT'}
          </span>
        </td>
        <td>{trade.entry_price}</td>
        <td>{trade.exit_price || '—'}</td>
        <td>
          <span style={{ color: isWin ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
            {isWin ? '+' : ''}{(trade.pnl || 0).toFixed(2)}
          </span>
        </td>
        <td>{trade.risk_reward ? `1:${trade.risk_reward.toFixed(1)}` : '—'}</td>
        <td>
          <span className={`badge ${trade.exit_reason?.includes('TP') ? 'badge-green' : trade.exit_reason === 'SL' ? 'badge-red' : 'badge-yellow'}`}>
            {trade.exit_reason || 'OPEN'}
          </span>
        </td>
        <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          {trade.entry_time ? new Date(trade.entry_time).toLocaleDateString() : '—'}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} style={{ background: 'var(--bg-tertiary)', padding: 20 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
              <div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 12, fontWeight: 600, textTransform: 'uppercase' }}>Trade Details</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: '0.85rem' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Entry Time:</span><span>{formatTime(trade.entry_time)}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Exit Time:</span><span>{formatTime(trade.exit_time)}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Duration:</span><span>{durationStr}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Stop Loss:</span><span>{trade.stop_loss}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Take Profit:</span><span>{trade.take_profit}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Volume:</span><span>{trade.volume}</span>
                  <span style={{ color: 'var(--text-muted)' }}>MT5 Ticket:</span><span>{trade.mt5_ticket || '—'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>P&L Pips:</span><span>{trade.pnl_pips != null ? trade.pnl_pips.toFixed(1) : '—'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Balance Before:</span><span>{trade.balance_before != null ? '$' + trade.balance_before.toFixed(2) : '—'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Balance After:</span><span>{trade.balance_after != null ? '$' + trade.balance_after.toFixed(2) : '—'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Confluence Score:</span><span>{trade.confluence_score != null ? trade.confluence_score + ' / 100' : '—'}</span>
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 12, fontWeight: 600, textTransform: 'uppercase' }}>
                  <Bot size={12} style={{ marginRight: 4, display: 'inline' }} /> AI Analysis
                </div>
                {analysis ? (
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-primary)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
                    {analysis}
                  </div>
                ) : (
                  <button className="btn btn-secondary btn-sm" onClick={handleAnalyze} disabled={analyzing}>
                    <Bot size={14} />
                    {analyzing ? 'Analyzing...' : 'Analyze with AI'}
                  </button>
                )}
                {chartDataObj && (
                  <div style={{ marginTop: 20 }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 4, fontWeight: 600, textTransform: 'uppercase' }}>Trade Chart</div>
                    <MiniChart data={chartDataObj} />
                  </div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function Journal() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [filter, setFilter] = useState('ALL');

  const { data: trades, isLoading } = useQuery({
    queryKey: ['trades', filter],
    queryFn: () => getTrades({ status: filter !== 'ALL' ? filter : undefined, limit: 500 }).then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const queryClient = useQueryClient();
  React.useEffect(() => {
    const handler = (e) => {
      if (e.detail?.type === 'trade_update') queryClient.invalidateQueries({ queryKey: ['trades'] });
    };
    window.addEventListener('ws-message', handler);
    return () => window.removeEventListener('ws-message', handler);
  }, [queryClient]);

  const [viewMode, setViewMode] = useState('TRADES'); // 'TRADES' or 'SUMMARY'
  const [summaryGrouping, setSummaryGrouping] = useState('Month'); // 'Day', 'Week', 'Month', 'Year'

  const groupedTrades = React.useMemo(() => {
    if (!trades) return [];
    const groups = {};
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    trades.forEach(t => {
      const d = new Date(t.entry_time || t.created_at || Date.now());
      let key;
      if (d >= today) {
        key = 'Today';
      } else if (d >= yesterday) {
        key = 'Yesterday';
      } else if (d.getMonth() === today.getMonth() && d.getFullYear() === today.getFullYear()) {
        key = 'This Month';
      } else {
        key = d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
      }

      if (!groups[key]) groups[key] = [];
      groups[key].push(t);
    });

    return Object.entries(groups);
  }, [trades]);

  const summaryData = React.useMemo(() => {
    if (!trades || trades.length === 0) return [];
    const groups = {};
    const sortedTrades = [...trades].sort((a, b) => new Date(a.entry_time || a.created_at) - new Date(b.entry_time || b.created_at));

    const getWeekNumber = (d) => {
        const date = new Date(d.getTime());
        date.setHours(0, 0, 0, 0);
        date.setDate(date.getDate() + 3 - (date.getDay() + 6) % 7);
        const week1 = new Date(date.getFullYear(), 0, 4);
        return 1 + Math.round(((date.getTime() - week1.getTime()) / 86400000 - 3 + (week1.getDay() + 6) % 7) / 7);
    };

    sortedTrades.forEach(t => {
      const d = new Date(t.entry_time || t.created_at || Date.now());
      let key = '';
      if (summaryGrouping === 'Day') key = d.toLocaleDateString();
      if (summaryGrouping === 'Week') key = `Week ${getWeekNumber(d)}, ${d.getFullYear()}`;
      if (summaryGrouping === 'Month') key = d.toLocaleString('default', { month: 'long', year: 'numeric' });
      if (summaryGrouping === 'Year') key = d.getFullYear().toString();

      if (!groups[key]) groups[key] = { trades: [], startBal: null, endBal: null, pnl: 0, wins: 0, losses: 0 };
      groups[key].trades.push(t);
      if (groups[key].startBal === null) groups[key].startBal = t.balance_before;
      groups[key].endBal = t.balance_after;
      const pnl = t.pnl || 0;
      groups[key].pnl += pnl;
      if (pnl > 0) groups[key].wins++; else groups[key].losses++;
    });

    return Object.entries(groups).map(([k, v]) => ({
      period: k,
      tradeCount: v.trades.length,
      startBal: v.startBal,
      endBal: v.endBal,
      pnl: v.pnl,
      winRate: v.wins / (v.wins + v.losses) || 0,
      firstDate: v.trades[0]?.entry_time || v.trades[0]?.created_at || Date.now()
    })).sort((a, b) => new Date(b.firstDate) - new Date(a.firstDate));
  }, [trades, summaryGrouping]);

  return (
    <>
      <div className="page-header">
        <h2><BookOpen size={22} style={{ display: 'inline', marginRight: 8 }} />Trade Journal</h2>
        <p>Complete trade history with AI-powered analysis</p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          {['ALL', 'OPEN', 'CLOSED'].map(f => (
            <button key={f} className={`btn ${filter === f ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className={`btn ${viewMode === 'TRADES' ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setViewMode('TRADES')}>
            List View
          </button>
          <button className={`btn ${viewMode === 'SUMMARY' ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setViewMode('SUMMARY')}>
            Summary View
          </button>
          {viewMode === 'SUMMARY' && (
            <select value={summaryGrouping} onChange={e => setSummaryGrouping(e.target.value)} style={{ padding: '4px 8px', fontSize: '0.8rem', background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)' }}>
              <option value="Day">Group by Day</option>
              <option value="Week">Group by Week</option>
              <option value="Month">Group by Month</option>
              <option value="Year">Group by Year</option>
            </select>
          )}
        </div>
      </div>

      <div className="card">
        {viewMode === 'SUMMARY' ? (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Trades</th>
                  <th>Win Rate</th>
                  <th>Starting Balance</th>
                  <th>Ending Balance</th>
                  <th>Period P&L</th>
                </tr>
              </thead>
              <tbody>
                {summaryData.length > 0 ? summaryData.map((row, i) => (
                  <tr key={i}>
                    <td><strong>{row.period}</strong></td>
                    <td>{row.tradeCount}</td>
                    <td style={{ color: row.winRate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>{(row.winRate * 100).toFixed(1)}%</td>
                    <td>${row.startBal != null ? row.startBal.toFixed(2) : '—'}</td>
                    <td>${row.endBal != null ? row.endBal.toFixed(2) : '—'}</td>
                    <td style={{ color: row.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                      ${row.pnl.toFixed(2)}
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', padding: '40px 0' }}>No trades found for this period.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th style={{ width: 30 }}></th>
                <th>Symbol</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>P&L</th>
                <th>RR</th>
                <th>Exit Reason</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {groupedTrades.length ? (
                groupedTrades.map(([groupName, groupTrades]) => (
                  <React.Fragment key={groupName}>
                    <tr style={{ background: 'var(--bg-secondary)', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      <td colSpan={9} style={{ padding: '8px 16px', borderBottom: 'none' }}>{groupName}</td>
                    </tr>
                    {groupTrades.map((t, i) => <TradeRow key={t.id || i} trade={t} />)}
                  </React.Fragment>
                ))
              ) : (
                <tr>
                  <td colSpan={9}>
                    <div className="empty-state">
                      <BookOpen />
                      <h3>{isLoading ? 'Loading trades...' : 'No trades recorded'}</h3>
                      <p>Trades will appear here once your bot starts executing</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        )}
      </div>
    </>
  );
}
