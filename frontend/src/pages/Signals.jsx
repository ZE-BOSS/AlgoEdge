import { useState, useEffect, Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Zap, CheckCircle, XCircle, ChevronDown, ChevronRight, Image, Activity } from 'lucide-react';
import { useConnectionStore, useAuthStore } from '../store';
import AnalyzeButton from '../components/AnalyzeButton';
import { getSignals, getSignalDetail, getBackendUrl } from '../services/api';

function ConfluenceBar({ score }) {
  const color = score >= 75 ? 'var(--green)' : score >= 60 ? 'var(--yellow)' : 'var(--red)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ width: 80, height: 6, background: 'var(--bg-hover)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${Math.min(score, 100)}%`, height: '100%', background: color, borderRadius: 3, transition: 'width 0.3s' }} />
      </div>
      <span style={{ fontSize: '0.75rem', fontWeight: 600, color }}>{score}/100</span>
    </div>
  );
}

function ConfluenceBreakdown({ breakdownJson }) {
  if (!breakdownJson) return null;
  let breakdown = {};
  try { breakdown = typeof breakdownJson === 'string' ? JSON.parse(breakdownJson) : breakdownJson; } catch { return null; }

  const factors = [
    { key: 'htf_bias', label: 'HTF Bias', max: 15 },
    { key: 'm15_bos', label: 'M15 BOS', max: 15 },
    { key: 'm15_choch', label: 'M15 ChoCH', max: 10 },
    { key: 'sweep', label: 'Liquidity Sweep', max: 15 },
    { key: 'fresh_ob', label: 'Fresh OB', max: 15 },
    { key: 'fvg_inside_ob', label: 'FVG ∩ OB', max: 10 },
    { key: 'ote_zone', label: 'OTE Zone', max: 5 },
    { key: 'candle', label: 'Candlestick', max: 15 },
    { key: 'ltf_choch', label: 'LTF ChoCH', max: 10 },
    { key: 'kill_zone', label: 'Kill Zone', max: 5 },
  ];

  return (
    <div className="confluence-breakdown">
      {factors.map(({ key, label, max }) => {
        const val = breakdown[key] || 0;
        return (
          <div key={key} className="confluence-factor">
            <span className="factor-label">{label}</span>
            <div className="factor-bar-bg">
              <div className="factor-bar-fill" style={{ width: `${(val / max) * 100}%` }} />
            </div>
            <span className="factor-score">{val}/{max}</span>
          </div>
        );
      })}
    </div>
  );
}

function AuthenticatedImage({ url, alt }) {
  const [imgSrc, setImgSrc] = useState(null);
  const [error, setError] = useState(false);
  const token = useAuthStore(s => s.token);

  useEffect(() => {
    let objectUrl = null;
    fetch(url, {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then(r => {
        if (!r.ok) throw new Error('Failed to fetch snapshot');
        return r.blob();
      })
      .then(blob => {
        objectUrl = URL.createObjectURL(blob);
        setImgSrc(objectUrl);
      })
      .catch(err => {
        console.error(err);
        setError(true);
      });

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, token]);

  if (error) return <div style={{ color: 'var(--text-muted)' }}>Failed to load snapshot</div>;
  if (!imgSrc) return <div style={{ color: 'var(--text-muted)' }}>Loading...</div>;
  return <img src={imgSrc} alt={alt} />;
}

function SignalDetail({ signal }) {
  const [detail, setDetail] = useState(null);
  const [showSnapshot, setShowSnapshot] = useState(false);

  const { data } = useQuery({
    queryKey: ['signalDetail', signal.id],
    queryFn: () => getSignalDetail(signal.id).then(r => r.data),
    enabled: true,
    staleTime: 60000,
  });

  const d = data || signal;

  return (
    <div className="signal-detail">
      <div className="signal-detail-grid">
        <div className="detail-section">
          <h4>Price Levels</h4>
          <div className="detail-pairs">
            <div><span>Entry:</span> <strong>{d.entry_price}</strong></div>
            <div><span>SL:</span> <strong className="red">{d.stop_loss}</strong></div>
            <div><span>TP1:</span> <strong className="green">{d.tp1_price}</strong></div>
            <div><span>TP2:</span> <strong className="green">{d.tp2_price}</strong></div>
            <div><span>TP3:</span> <strong className="green">{d.tp3_price}</strong></div>
            {d.tp4_price && <div><span>TP4:</span> <strong className="green">{d.tp4_price}</strong></div>}
            {d.tp5_price && <div><span>TP5:</span> <strong className="green">{d.tp5_price}</strong></div>}
          </div>
        </div>

        {/* Session context. `session` was always blank because the live path
            never wrote it; `session_close_time` is new, so a setup taken near
            the session close can be recognised as such. */}
        <div className="detail-section">
          <h4>Session</h4>
          <div className="detail-pairs">
            <div><span>Session:</span> <strong>{d.session || '—'}</strong></div>
            <div>
              <span>Session closes:</span>{' '}
              <strong>
                {d.session_close_time
                  ? new Date(d.session_close_time).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })
                  : (d.session === '24/7' ? '24/7 — no close' : '—')}
              </strong>
            </div>
            <div>
              <span>Signal time:</span>{' '}
              {d.signal_time ? new Date(d.signal_time).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '—'}
            </div>
          </div>
        </div>

        <div className="detail-section">
          <h4>SMC Zones</h4>
          <div className="detail-pairs">
            <div><span>OB:</span> {d.ob_top} — {d.ob_bottom}</div>
            <div><span>FVG:</span> {d.fvg_top} — {d.fvg_bottom}</div>
            <div><span>HTF Bias:</span> <strong>{d.htf_bias}</strong></div>
          </div>
        </div>

        <div className="detail-section">
          <h4>Confluence</h4>
          <ConfluenceBreakdown breakdownJson={d.confluence_breakdown} />
        </div>

        <div className="detail-section">
          <h4>AI Analysis</h4>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-primary)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
            {d.llm_analysis || 'No detailed analysis available.'}
          </div>
        </div>

        {d.skip_reason && (
          <div className="detail-section">
            <h4>Rejection Reason</h4>
            <div className="rejection-reason">{d.skip_reason}</div>
          </div>
        )}

        {data?.linked_trade && (
          <div className="detail-section">
            <h4>Trade Result</h4>
            <div className="detail-pairs">
              <div><span>P&L:</span> <strong className={data.linked_trade.pnl >= 0 ? 'green' : 'red'}>${data.linked_trade.pnl?.toFixed(2)}</strong></div>
              <div><span>R:R achieved:</span> <strong>{data.linked_trade.risk_reward != null ? `${data.linked_trade.risk_reward.toFixed(2)}R` : '—'}</strong></div>
              <div><span>P&L pips:</span> <strong>{data.linked_trade.pnl_pips != null ? data.linked_trade.pnl_pips.toFixed(1) : '—'}</strong></div>
              <div><span>Exit:</span> {data.linked_trade.exit_reason || '—'} @ {data.linked_trade.exit_price ?? '—'}</div>
              <div>
                <span>Exit time:</span>{' '}
                {data.linked_trade.exit_time
                  ? new Date(data.linked_trade.exit_time).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })
                  : '—'}
              </div>
              <div>
                <span>Duration:</span>{' '}
                {data.linked_trade.duration_seconds != null
                  ? `${Math.floor(data.linked_trade.duration_seconds / 60)}m`
                  : '—'}
              </div>
              <div>
                <span>Balance:</span>{' '}
                {data.linked_trade.balance_before != null ? `$${data.linked_trade.balance_before.toFixed(2)}` : '—'}
                {' → '}
                <strong>{data.linked_trade.balance_after != null ? `$${data.linked_trade.balance_after.toFixed(2)}` : '—'}</strong>
              </div>
            </div>
          </div>
        )}

        {d.entry_snapshot && (
          <div className="detail-section">
            <button className="btn btn-sm" onClick={() => setShowSnapshot(!showSnapshot)}>
              <Image size={14} /> {showSnapshot ? 'Hide' : 'View'} Snapshot
            </button>
            {showSnapshot && (
              <div className="snapshot-viewer">
                <AuthenticatedImage url={`${getBackendUrl()}/api/signals/${signal.id}/snapshot`} alt="Entry snapshot" />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Signals() {
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState(new Set());
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const { data: signals = [], isLoading } = useQuery({
    queryKey: ['signals', filter],
    queryFn: () => {
      const params = {};
      if (filter === 'executed') params.status = 'executed';
      if (filter === 'skipped') params.status = 'skipped';
      return getSignals(params).then(r => r.data);
    },
    enabled: status === 'ONLINE' && isAuthenticated,
    refetchInterval: 10000,
  });

  const toggle = (id) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <>
      <div className="page-header">
        <h2><Zap size={22} style={{ display: 'inline', marginRight: 8 }} />Signals</h2>
        <p>All generated signals — executed and skipped — with confluence scores</p>
        <div style={{ marginTop: 8 }}>
          <AnalyzeButton
            targetType="signals"
            compact
            question="Which gates are blocking the most signals, and are any of them blocking setups that should have traded?"
          />
        </div>
      </div>

      <div className="filter-tabs" style={{ marginBottom: 16 }}>
        {['all', 'executed', 'skipped'].map(f => (
          <button key={f} className={`tab-btn ${filter === f ? 'active' : ''}`} onClick={() => setFilter(f)}>
            {f === 'all' ? 'All' : f === 'executed' ? '✅ Executed' : '⏭️ Skipped'}
          </button>
        ))}
      </div>

      <div className="card">
        {signals.length === 0 ? (
          <div className="empty-state">
            <Activity />
            <h3>{isLoading ? 'Loading signals...' : 'No Signals Yet'}</h3>
            <p>{isLoading ? '' : 'Signals will appear here when the strategy engine is running'}</p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 30 }}></th>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th>Entry</th>
                  <th>SL</th>
                  <th>Confluence</th>
                  <th>Session</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {signals.map(sig => (
                  <Fragment key={sig.id}>
                    <tr key={sig.id} onClick={() => toggle(sig.id)} className="signal-row clickable">
                      <td>{expanded.has(sig.id) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                      <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        {sig.signal_time ? new Date(sig.signal_time).toLocaleString() : '—'}
                      </td>
                      <td><strong>{sig.symbol}</strong></td>
                      <td>
                        <span className={`badge ${sig.direction === 'BUY' ? 'badge-green' : 'badge-red'}`}>
                          {sig.direction === 'BUY' ? '▲ LONG' : '▼ SHORT'}
                        </span>
                      </td>
                      <td>{sig.entry_price}</td>
                      <td>{sig.stop_loss}</td>
                      <td><ConfluenceBar score={sig.confluence_score || 0} /></td>
                      <td><span className="badge badge-blue">{sig.session || '—'}</span></td>
                      <td>
                        {sig.acted_on ? (
                          <span className="badge badge-green"><CheckCircle size={10} style={{ marginRight: 2 }} /> Executed</span>
                        ) : (
                          <span className="badge badge-yellow" title={sig.skip_reason}><XCircle size={10} style={{ marginRight: 2 }} /> Skipped</span>
                        )}
                      </td>
                    </tr>
                    {expanded.has(sig.id) && (
                      <tr key={`${sig.id}-detail`}>
                        <td colSpan={9} style={{ padding: 0 }}>
                          <SignalDetail signal={sig} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
