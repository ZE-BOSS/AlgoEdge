import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { UserPlus } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState, StateBadge } from './shared';
import { fmtMoney, fmtUnits, isNeg, useInvAction } from './format';

const FILTERS = ['all', 'active', 'pending', 'closing', 'closed'];

export default function InvestorList() {
  const [status, setStatus] = useState('all');
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ['inv', 'list', status],
    queryFn: () => inv.list(status === 'all' ? {} : { status }),
  });
  const create = useInvAction(inv.create, {
    onSuccess: (res) => navigate(`/investors/p/${res.data.id}`),
  });

  return (
    <div className="card">
      <div className="card-header" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {FILTERS.map(f => (
            <button key={f} className={`btn btn-sm ${status === f ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setStatus(f)}>{f}</button>
          ))}
        </div>
        <ActionForm label="Add investor" icon={UserPlus} tone="primary" submitLabel="Create"
                    fields={[
                      { name: 'name', label: 'Full name', required: true },
                      { name: 'email', label: 'Email', type: 'email', required: true },
                      { name: 'phone', label: 'Phone' },
                      { name: 'country', label: 'Country' },
                    ]}
                    onSubmit={(body) => create.mutateAsync(body)} />
      </div>

      <QueryState q={q}>
        {(d) => d.investors.length === 0 ? (
          <Empty>
            <h3>No investors{status !== 'all' ? ` with status ${status}` : ''}</h3>
            <p>Add the people already in the fund, then record their deposits with the date they
              actually paid — units are priced at that day's NAV.</p>
          </Empty>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Investor</th><th>Status</th><th className="num">Units</th>
                  <th className="num">Value</th><th className="num">Capital in</th>
                  <th className="num">Withdrawn</th><th className="num">Profit</th>
                  <th className="num">Share</th>
                </tr>
              </thead>
              <tbody>
                {d.investors.map(i => (
                  <tr key={i.id}>
                    <td>
                      <Link to={`/investors/p/${i.id}`} style={{ fontWeight: 600 }}>{i.name}</Link>
                      <div className="inv-hint">{i.email}</div>
                    </td>
                    <td><StateBadge state={i.status} /></td>
                    <td className="num">{fmtUnits(i.units)}</td>
                    <td className="num">{fmtMoney(i.current_value)}</td>
                    <td className="num">{fmtMoney(i.capital_in)}</td>
                    <td className="num">{fmtMoney(i.withdrawn)}</td>
                    <td className="num" style={{ color: isNeg(i.profit) ? 'var(--red)' : 'var(--green)' }}>
                      {fmtMoney(i.profit, { sign: true })}</td>
                    <td className="num">{i.share_of_pool_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </div>
  );
}
