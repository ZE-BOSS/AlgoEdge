import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { inv } from '../../services/api';
import { QueryState } from './shared';
import { fmtMoney, fmtSinceLaunch } from './format';

const QUEUES = [
  { key: 'deposits', label: 'Deposits to confirm', to: '/investors/queues' },
  { key: 'withdrawals', label: 'Withdrawals to decide', to: '/investors/queues/withdrawals' },
  { key: 'exceptions', label: 'Over-cap exceptions', to: '/investors/queues/exceptions' },
  { key: 'to_pay', label: 'Approved, not yet paid', to: '/investors/queues/to-pay' },
  { key: 'closures', label: 'Closures', to: '/investors/queues/closures' },
  { key: 'disclosures', label: 'Trades to disclose', to: '/investors/disclosure' },
];

function Health() {
  const q = useQuery({ queryKey: ['inv', 'reconciliation', null], queryFn: () => inv.reconciliation() });
  return (
    <QueryState q={q}>
      {(r) => (
        <Link to="/investors/reconciliation" className="card inv-health"
              style={{ borderColor: r.healthy ? 'var(--green-dim)' : 'var(--red)' }}>
          {r.healthy
            ? <CheckCircle2 size={18} color="var(--green)" />
            : <AlertTriangle size={18} color="var(--red)" />}
          <div>
            <div style={{ fontWeight: 600 }}>
              {r.healthy ? 'Books reconcile' : 'Reconciliation needs attention'}
            </div>
            <div className="inv-hint">
              Against pool equity {fmtMoney(r.pool_equity)} ({r.pool_equity_source})
              {r.ledger_drift.length > 0 && ` · ${r.ledger_drift.length} ledger/cache mismatch(es)`}
            </div>
          </div>
        </Link>
      )}
    </QueryState>
  );
}

export default function Overview() {
  const q = useQuery({ queryKey: ['inv', 'overview'], queryFn: inv.overview });
  return (
    <QueryState q={q}>
      {(o) => (
        <div style={{ display: 'grid', gap: 16 }}>
          <div className="kpi-strip">
            <div className="kpi"><div className="kpi-label">Assets under management</div>
              <div className="kpi-value">{fmtMoney(o.aum)}</div></div>
            <div className="kpi"><div className="kpi-label">Fund since launch</div>
              <div className="kpi-value">{fmtSinceLaunch(o.nav_per_unit)}</div>
              <div className="inv-hint" style={{ margin: 0 }}>$100 at launch is now {fmtMoney(o.nav_per_unit)}</div></div>
            <div className="kpi"><div className="kpi-label">Active investors</div>
              <div className="kpi-value">{o.investors.active || 0}</div></div>
            <div className="kpi"><div className="kpi-label">Last valuation</div>
              <div className="kpi-value" style={{ fontSize: '0.95rem' }}>
                {o.last_snapshot || 'none yet'}</div></div>
          </div>

          {!o.last_snapshot && (
            <div className="card inv-hint" style={{ borderColor: 'var(--yellow)' }}>
              The fund has not been valued yet, so every deposit buys in at the launch price ($100 a unit).
              Record a valuation on the <Link to="/investors/nav">Valuation</Link> tab once the pool has traded.
            </div>
          )}

          <Health />

          <div className="inv-queue-grid">
            {QUEUES.map(({ key, label, to }) => (
              <Link key={key} to={to} className="card inv-queue-card">
                <span className="inv-hint">{label}</span>
                <span className="kpi-value" style={{ color: o.queues[key] ? 'var(--gold)' : undefined }}>
                  {o.queues[key]}
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}
    </QueryState>
  );
}
