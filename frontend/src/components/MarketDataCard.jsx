import { useQuery } from '@tanstack/react-query';
import { Radio } from 'lucide-react';
import { getMarketDataStatus } from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';

const ago = (s) => (s == null ? '-' : s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`);

/**
 * Where the bots' and the backtester's prices come from on THIS server.
 * Markets with a CME future behind them read Databento (translated to the
 * broker's prices); everything else - Deriv synthetics included - reads MT5.
 * Execution, sizing and spreads always come from MT5.
 */
export default function MarketDataCard() {
  const { status } = useConnectionStore();
  const isAuth = useAuthStore((s) => s.isAuthenticated);
  const { data, isError } = useQuery({
    queryKey: ['marketdata-status'],
    queryFn: () => getMarketDataStatus().then(r => r.data),
    enabled: status === 'ONLINE' && isAuth,
    refetchInterval: 30000,
  });
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title"><Radio size={14} /> Market data</span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {data ? (data.enabled ? 'Databento on' : 'Databento off (MT5 only)') : isError ? 'unavailable' : '...'}
        </span>
      </div>
      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 0 }}>
        Gold, silver, oil, the US indices, Nikkei and the FX majors are priced from the CME futures on Databento
        (1-minute bars with order flow, shifted to this broker's prices); synthetics and anything unmapped come from MT5.
        Orders, lot sizes and spreads always come from MT5. If the stream falls behind, a symbol uses MT5 bars until it catches up.
      </p>
      {data && (
        <>
          <table style={{ width: '100%', fontSize: '0.75rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                <th>Market</th><th>Future</th><th>Live stream</th><th>Newest minute</th><th>History stored</th>
              </tr>
            </thead>
            <tbody>
              {data.feeds.map(f => (
                <tr key={f.future} style={{ borderTop: '1px solid var(--border)' }}>
                  <td>{f.market}</td>
                  <td>{f.future}</td>
                  <td style={{ color: f.live_ok ? 'var(--green, #22c55e)' : 'var(--amber, #f59e0b)' }}>{f.live_ok ? 'current' : 'behind / closed'}</td>
                  <td>{ago(f.newest_minute_s_ago)} ago</td>
                  <td>{f.stored_days ? `${f.stored_days} days (${f.first_day} - ${f.last_day})` : 'none'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.served?.length > 0 && (
            <table style={{ width: '100%', fontSize: '0.75rem', borderCollapse: 'collapse', marginTop: 12 }}>
              <thead>
                <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                  <th>Symbol</th><th>Use</th><th>Last served from</th><th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.served.map(s => (
                  <tr key={`${s.symbol}|${s.mode}`} style={{ borderTop: '1px solid var(--border)' }}>
                    <td>{s.symbol}</td>
                    <td>{s.mode === 'live' ? 'bots' : 'backtests'}</td>
                    <td style={{ color: s.source === 'databento' ? 'var(--green, #22c55e)' : 'var(--amber, #f59e0b)' }}>
                      {s.source === 'databento' ? `Databento ${s.future}` : 'MT5 (fallback)'}
                    </td>
                    <td>{ago(s.seconds_ago)} ago</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!data.key_present && (
            <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: 0 }}>
              This server has no Databento key: it reads the shared store and the live stream, and cannot fetch
              history the store does not hold (those backtests use MT5 bars).
            </p>
          )}
        </>
      )}
    </div>
  );
}
