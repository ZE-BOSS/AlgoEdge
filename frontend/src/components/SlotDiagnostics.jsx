/**
 * frontend/src/components/SlotDiagnostics.jsx
 *
 * "Why isn't this slot trading?"
 *
 * Three completely different situations used to look identical from outside:
 * a slot that is not running, a slot that is running and finding nothing, and
 * a slot that is finding setups which something then refuses. Until you can
 * tell them apart, "demo and live take different trades with the same
 * settings" is not a question anyone can answer by looking.
 *
 * Switch servers with the picker in the header and compare the two directly -
 * the query is namespaced per server, so what you see is the box named at the
 * top and not a cached copy of the other one.
 */

import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, HelpCircle, MinusCircle } from 'lucide-react';
import api from '../services/api';
import { qk } from '../services/servers';

const VERDICT = {
  'trading': { colour: '#3fb950', Icon: CheckCircle2,
    hint: 'Finding setups and taking them.' },
  'no setup found': { colour: '#8b949e', Icon: MinusCircle,
    hint: 'Running. The strategy is finding nothing — that is a market answer, '
        + 'and usually the honest one.' },
  'every signal refused': { colour: '#d29922', Icon: AlertTriangle,
    hint: 'It found setups and something refused every one. The reasons are on '
        + 'the right, with counts.' },
  'not scanned': { colour: '#f85149', Icon: AlertTriangle,
    hint: 'NOT RUNNING. A configuration answer, not a market one: check that it '
        + 'is enabled and that the bot was restarted after you added it.' },
  'running but not in the saved config': { colour: '#f85149', Icon: HelpCircle,
    hint: 'A stale engine from before an edit. Restart the bot.' },
};

export default function SlotDiagnostics() {
  const { data } = useQuery({
    queryKey: qk('slot-diagnostics'),
    queryFn: () => api.get('/slot-diagnostics').then((r) => r.data),
    refetchInterval: 30000,
  });

  if (!data?.slots?.length) return null;

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-header">
        <span className="card-title">Why isn&rsquo;t this slot trading?</span>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          {data.scanning} of {data.configured} scanning · {data.trading} trading
          {data.account ? ` · #${data.account}` : ''}
        </span>
      </div>

      {!data.bot_running && (
        <div className="offline-banner" style={{ marginBottom: 10 }}>
          The bot is not running, so nothing below has been scanned today.
        </div>
      )}

      <div style={{ display: 'grid', gap: 4 }}>
        {data.slots.map((s) => {
          const v = VERDICT[s.verdict] || VERDICT['not scanned'];
          const dropped = Object.entries(s.dropped || {})
            .sort((a, b) => b[1] - a[1]);
          return (
            <div key={s.slot_id} title={v.hint}
                 style={{ display: 'flex', gap: 10, alignItems: 'center',
                          fontSize: '0.75rem', padding: '5px 8px',
                          background: 'var(--bg-tertiary)',
                          borderRadius: 'var(--radius-xs)',
                          borderLeft: `3px solid ${v.colour}` }}>
              <v.Icon size={13} style={{ color: v.colour, flex: '0 0 auto' }} />
              <span style={{ fontWeight: 600, minWidth: 130 }}>{s.symbol}</span>
              <span style={{ color: 'var(--text-muted)', minWidth: 130 }}>
                {s.strategy_id}
              </span>
              <span style={{ color: v.colour, minWidth: 150 }}>{s.verdict}</span>
              <span style={{ color: 'var(--text-muted)' }}>
                {s.scans} scans · {s.signals} signals · {s.trades} trades
              </span>
              {!s.enabled && <span className="badge badge-red">disabled</span>}
              {dropped.length > 0 && (
                <span style={{ color: 'var(--text-muted)', marginLeft: 'auto' }}>
                  {dropped.map(([reason, n]) => `${reason} x${n}`).join(' · ')}
                </span>
              )}
            </div>
          );
        })}
      </div>

      <p style={{ fontSize: '0.68rem', color: 'var(--text-muted)', margin: '8px 0 0' }}>
        {data.note} The refusal reasons use the same names as the
        backtester&rsquo;s rejection funnel, so a live slot and a backtest of the
        same slot can be compared directly.
      </p>
    </div>
  );
}
