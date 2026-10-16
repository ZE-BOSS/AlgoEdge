import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState, StateBadge } from './shared';
import { fmtMoney, useInvAction } from './format';

/**
 * What we owe introducers.
 *
 * An earning is ACCRUED when the referred investor's fee period closes; it is
 * a debt, not a payment. Settling records that the money was actually sent,
 * with the reference, and can happen exactly once per row.
 */
export default function Referrals() {
  const [state, setState] = useState('accrued');
  const q = useQuery({
    queryKey: ['inv', 'referral-earnings', state],
    queryFn: () => inv.referralEarnings(state ? { state } : {}),
  });
  const settle = useInvAction(({ id, payment_reference }) => inv.settleEarning(id, payment_reference));

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Referral earnings</span>
        <div style={{ display: 'flex', gap: 4 }}>
          {[['accrued', 'Owed'], ['settled', 'Paid'], ['', 'All']].map(([v, l]) => (
            <button key={l} className={`btn btn-sm ${state === v ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setState(v)}>{l}</button>
          ))}
        </div>
      </div>
      <p className="inv-hint" style={{ marginTop: 0 }}>
        Earned when the referred investor's fee period closes, at the rates in the terms they joined
        under. Rates are set on Fund terms.
      </p>
      <QueryState q={q}>
        {(d) => (
          <>
            <div className="kpi-strip" style={{ marginBottom: 12 }}>
              {Object.entries(d.totals).map(([k, v]) => (
                <div key={k} className="kpi"><div className="kpi-label">{k === 'accrued' ? 'Owed' : k === 'settled' ? 'Paid' : k}</div>
                  <div className="kpi-value">{fmtMoney(v)}</div></div>
              ))}
            </div>
            {d.rows.length ? (
              <div className="table-wrapper"><table>
                <thead><tr><th>Period</th><th>Earner</th><th>From</th><th>Level</th><th>Model</th>
                  <th className="num">Basis</th><th className="num">Rate</th><th className="num">Amount</th>
                  <th>State</th><th /></tr></thead>
                <tbody>{d.rows.map((r) => (
                  <tr key={r.id} title={r.note || ''}>
                    <td>{r.period_start} → {r.period_end}</td>
                    <td><Link to={`/investors/p/${r.earner_id}`}>{r.earner}</Link></td>
                    <td><Link to={`/investors/p/${r.source_investor_id}`}>{r.source}</Link></td>
                    <td>{r.level === 1 ? 'Direct' : 'Level 2'}</td>
                    <td>{r.model === 'HOUSE_FEE' ? 'Our fee' : 'Investor profit'}</td>
                    <td className="num">{fmtMoney(r.basis)}</td>
                    <td className="num">{Number(r.rate_pct)}%</td>
                    <td className="num">{fmtMoney(r.amount)}</td>
                    <td><StateBadge state={r.state} /></td>
                    <td>
                      {r.state === 'accrued' && (
                        <ActionForm label="Settle" icon={CheckCircle} submitLabel="Record payment"
                                    fields={[{ name: 'payment_reference', label: 'Payment reference', required: true }]}
                                    onSubmit={(b) => settle.mutateAsync({ id: r.id, ...b })} />
                      )}
                    </td>
                  </tr>))}
                </tbody></table></div>
            ) : <Empty>{state === 'accrued' ? 'Nothing owed right now.' : 'No earnings recorded.'}</Empty>}
          </>
        )}
      </QueryState>
    </div>
  );
}
