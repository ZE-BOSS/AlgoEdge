import { useEffect } from 'react';
import { api } from '../api';
import { useLoad } from '../hooks';
import { ago } from '../format';
import { Card } from './ui';

/** Trades open right now: market, side and when; the result comes when it is published. */
export default function LiveTrades({ compact = false }) {
  const q = useLoad(api.live);
  const { reload } = q;
  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) reload(); }, 60_000);
    return () => clearInterval(id);
  }, [reload]);
  const d = q.data;
  if (!d || !d.invested || (compact && d.trades.length === 0)) return null;
  return (
    <Card title={<span className="live-title"><span className="live-dot" aria-hidden="true" />Live now</span>}>
      {d.trades.length === 0 ? <p className="muted">No trades open right now. You get a notification the moment one opens.</p> : (
        <>
          <ul className="trade-list">
            {d.trades.map((t) => (
              <li key={t.id}>
                <span className={`dir ${t.direction === 'BUY' ? 'long' : 'short'}`}>{t.side}</span>
                <div><strong>{t.symbol}</strong><span className="muted small">opened {ago(t.opened_at)}</span></div>
                <div className="right"><span className="live-pill">open</span></div>
              </li>
            ))}
          </ul>
          <p className="muted small">Your share of each result reaches your balance when the trade is published
            after it closes. You will be notified.</p>
        </>
      )}
    </Card>
  );
}
