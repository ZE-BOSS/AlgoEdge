import { useQuery } from '@tanstack/react-query';
import { Camera } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState } from './shared';
import { fmtDate, fmtMoney, fmtUnits, useInvAction } from './format';

export default function Nav() {
  const q = useQuery({ queryKey: ['inv', 'nav'], queryFn: inv.navHistory });
  const snap = useInvAction(inv.snapshot);

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <div className="card-header"><span className="card-title">Price the fund</span></div>
        <p className="inv-hint" style={{ marginTop: 0 }}>
          NAV per unit = (pool equity − liabilities) ÷ units in issue. One price per WAT day;
          deposits and withdrawals execute at the latest price on or before their date. Re-pricing
          a day that already has a NAV needs a reason and is recorded — ledger rows that already
          executed keep the price they executed at.
        </p>
        <ActionForm label="Take snapshot" icon={Camera} tone="primary" submitLabel="Save NAV"
                    fields={[
                      { name: 'pool_equity', label: 'Pool equity (USD, all broker accounts)', type: 'number', required: true },
                      { name: 'liabilities', label: 'Other liabilities (USD)', type: 'number',
                        hint: 'Approved-but-unpaid withdrawals and unpaid fees are added automatically. Only enter anything else owed. Blank = 0.' },
                      { name: 'on', label: 'Day', type: 'date', hint: 'Blank = today (WAT).' },
                      { name: 'reason', label: 'Reason (required when replacing a day)', type: 'textarea' },
                    ]}
                    onSubmit={(b) => snap.mutateAsync(b)} />
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">History</span></div>
        <QueryState q={q}>
          {(rows) => rows.length === 0 ? <Empty>No snapshots yet — the fund is at its opening NAV.</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Day</th><th className="num">NAV / unit</th><th className="num">Pool equity</th>
                  <th className="num">Liabilities</th><th className="num">Units in issue</th><th>Source</th>
                  <th>Recorded</th></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.as_of_date}>
                      <td>{r.as_of_date}</td>
                      <td className="num">{fmtUnits(r.nav_per_unit)}</td>
                      <td className="num">{fmtMoney(r.pool_equity)}</td>
                      <td className="num">{fmtMoney(r.liabilities)}</td>
                      <td className="num">{fmtUnits(r.units_in_issue, 2)}</td>
                      <td>{r.source}</td>
                      <td>{fmtDate(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </div>
    </div>
  );
}
