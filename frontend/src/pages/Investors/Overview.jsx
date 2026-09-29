import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { inv } from '../../services/api';
import { QueryState } from './shared';
import { fmtMoney, fmtUnits } from './format';

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
            <div className="kpi"><div className="kpi-label">NAV / unit</div>
              <div className="kpi-value">{fmtUnits(o.nav_per_unit, 4)}</div></div>
            <div className="kpi"><div className="kpi-label">Units in issue</div>
              <div className="kpi-value">{fmtUnits(o.units_in_issue, 2)}</div></div>
            <div className="kpi"><div className="kpi-label">Active investors</div>
              <div className="kpi-value">{o.investors.active || 0}</div></div>
            <div className="kpi"><div className="kpi-label">Last NAV snapshot</div>
              <div className="kpi-value" style={{ fontSize: '0.95rem' }}>
                {o.last_snapshot || 'none yet'}</div></div>
          </div>

          {!o.last_snapshot && (
            <div className="card inv-hint" style={{ borderColor: 'var(--yellow)' }}>
              No NAV snapshot has been taken, so every deposit is priced at the opening NAV.
              Take one on the <Link to="/investors/nav">NAV</Link> tab once the pool has traded.
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
