import { useQuery } from '@tanstack/react-query';
import { Camera } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState } from './shared';
import { fmtDate, fmtMoney, fmtPrice, fmtSinceLaunch, useInvAction } from './format';

export default function Nav() {
  const q = useQuery({ queryKey: ['inv', 'nav'], queryFn: inv.navHistory });
  const snap = useInvAction(inv.snapshot);

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <div className="card-header"><span className="card-title">Value the fund</span></div>
        <p className="inv-hint" style={{ marginTop: 0 }}>
          Enter what the trading accounts hold today. Fund value = pool equity − what the fund owes.
          Every investor&rsquo;s balance moves by the same percentage, and deposits and withdrawals on
          or after this day use it. One valuation per WAT day; replacing a day needs a reason and is
          recorded, and money that already moved keeps the value it moved at.
        </p>
        <ActionForm label="Record valuation" icon={Camera} tone="primary" submitLabel="Save valuation"
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
          {(rows) => rows.length === 0 ? <Empty>No valuations yet — the fund is at its launch value ($100 a unit).</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Day</th><th className="num">Fund value</th><th className="num">Pool equity</th>
                  <th className="num">Owed</th><th className="num">Since launch</th>
                  <th className="num" title="What one unit — $100 at launch — is worth">Unit price</th>
                  <th>Source</th><th>Recorded</th></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.as_of_date}>
                      <td>{r.as_of_date}</td>
                      <td className="num"><strong>{fmtMoney(r.fund_value)}</strong></td>
                      <td className="num">{fmtMoney(r.pool_equity)}</td>
                      <td className="num">{fmtMoney(r.liabilities)}</td>
                      <td className="num">{fmtSinceLaunch(r.nav_per_unit)}</td>
                      <td className="num">{fmtPrice(r.nav_per_unit)}</td>
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
