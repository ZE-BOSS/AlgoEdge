import { useQuery } from '@tanstack/react-query';
import { FilePlus } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, QueryState } from './shared';
import { fmtDate, fmtMoney, useInvAction } from './format';

const FIELDS = [
  ['performance_fee_pct', 'Performance fee %', 'number'],
  ['management_fee_pct', 'Management fee % (of each two-month period\'s profit)', 'number'],
  ['withdrawal_cap_pct', 'Withdrawal cap, % of month’s profit', 'number'],
  ['min_investment', 'Minimum investment (USD)', 'number'],
  ['lockup_days', 'Lock-up (days)', 'number'],
  ['notice_days', 'Notice period (days)', 'number'],
  ['bank_name', 'Receiving bank'],
  ['bank_account_number', 'Receiving account number'],
  ['bank_account_name', 'Receiving account name'],
  ['bank_instructions', 'Deposit instructions', 'textarea'],
];

export default function Terms() {
  const q = useQuery({ queryKey: ['inv', 'settings'], queryFn: inv.settings });
  const change = useInvAction((b) => {
    // Only send what actually changed: every field sent is recorded as a change.
    const cur = q.data?.data?.current || {};
    const diff = Object.fromEntries(Object.entries(b).filter(([k, v]) => String(cur[k] ?? '') !== String(v)));
    for (const k of ['lockup_days', 'notice_days']) if (k in diff) diff[k] = parseInt(diff[k], 10);
    return inv.changeSettings(diff);
  });

  return (
    <QueryState q={q}>
      {(d) => (
        <div style={{ display: 'grid', gap: 16 }}>
          <div className="card">
            <div className="card-header">
              <span className="card-title">Current terms · version {d.current.version}</span>
              <ActionForm label="New version" icon={FilePlus} tone="primary" submitLabel="Publish new version"
                          fields={FIELDS.map(([name, label, type]) => ({
                            name, label, type, defaultValue: d.current[name] ?? '',
                          }))}
                          onSubmit={(b) => change.mutateAsync(b)} />
            </div>
            <p className="inv-hint" style={{ marginTop: 0 }}>
              Terms are versioned, never edited. A lock-up and a notice period are promises: money
              already committed keeps the version it was committed under, and a change applies to new
              commitments only.
            </p>
            <div className="detail-pairs inv-pairs">
              {FIELDS.map(([name, label]) => (
                <FieldPair key={name} label={label} name={name} value={d.current[name]} />
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-header"><span className="card-title">History</span></div>
            <div className="table-wrapper">
              <table>
                <thead><tr><th>v</th><th>From</th><th className="num">Perf</th><th className="num">Mgmt</th>
                  <th className="num">Cap</th><th className="num">Min</th><th className="num">Lock-up</th>
                  <th className="num">Notice</th><th>By</th></tr></thead>
                <tbody>
                  {d.history.map(v => (
                    <tr key={v.version}>
                      <td>{v.version}</td><td>{fmtDate(v.effective_from)}</td>
                      <td className="num">{v.performance_fee_pct}%</td>
                      <td className="num">{v.management_fee_pct}%</td>
                      <td className="num">{v.withdrawal_cap_pct}%</td>
                      <td className="num">{fmtMoney(v.min_investment)}</td>
                      <td className="num">{v.lockup_days}d</td>
                      <td className="num">{v.notice_days}d</td>
                      <td>{v.created_by || 'defaults'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </QueryState>
  );
}

function FieldPair({ label, name, value }) {
  let shown = value ?? '—';
  if (name.endsWith('_pct') && value != null) shown = `${value}%`;
  if (name === 'min_investment') shown = fmtMoney(value);
  if (name.endsWith('_days') && value != null) shown = `${value} days`;
  return <><span>{label}</span><span className={name.includes('pct') || name.includes('days') ? 'num' : ''}>{shown}</span></>;
}
