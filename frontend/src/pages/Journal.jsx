import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, ChevronDown, ChevronRight, Bot, ExternalLink } from 'lucide-react';
import { getTrades, analyzeTrade } from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';

function TradeRow({ trade }) {
  const [expanded, setExpanded] = useState(false);
  const [analysis, setAnalysis] = useState(null);
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
                  <span style={{ color: 'var(--text-muted)' }}>Stop Loss:</span><span>{trade.stop_loss}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Take Profit:</span><span>{trade.take_profit}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Volume:</span><span>{trade.volume}</span>
                  <span style={{ color: 'var(--text-muted)' }}>MT5 Ticket:</span><span>{trade.mt5_ticket || '—'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>P&L Pips:</span><span>{trade.pnl_pips || '—'}</span>
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
    queryFn: () => getTrades({ status: filter !== 'ALL' ? filter : undefined, limit: 100 }).then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  return (
    <>
      <div className="page-header">
        <h2><BookOpen size={22} style={{ display: 'inline', marginRight: 8 }} />Trade Journal</h2>
        <p>Complete trade history with AI-powered analysis</p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {['ALL', 'OPEN', 'CLOSED'].map(f => (
          <button key={f} className={`btn ${filter === f ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setFilter(f)}>
            {f}
          </button>
        ))}
      </div>

      <div className="card">
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
              {trades?.length ? (
                trades.map((t, i) => <TradeRow key={t.id || i} trade={t} />)
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
      </div>
    </>
  );
}
