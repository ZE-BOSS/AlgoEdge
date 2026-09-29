import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Check, Phone, X } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState, StateBadge } from './shared';
import { fmtDate, useInvAction } from './format';

const FILTERS = ['new', 'contacted', 'accepted', 'declined', 'all'];

export default function Applications() {
  const [status, setStatus] = useState('new');
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ['inv', 'applications', status],
    queryFn: () => inv.applications(status === 'all' ? null : status) });
  const mark = useInvAction(({ id, s }) => inv.applicationStatus(id, s));
  const accept = useInvAction((id) => inv.acceptApplication(id),
    { onSuccess: (res) => navigate(`/investors/p/${res.data.investor_id}`) });
  return (
    <div className="card">
      <div className="filter-tabs" style={{ marginBottom: 12 }}>
        {FILTERS.map((f) => (
          <button key={f} className={`btn btn-sm ${status === f ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setStatus(f)}>{f}</button>
        ))}
      </div>
      <p className="inv-hint" style={{ marginTop: 0 }}>From the website's Apply form. Accepting creates a pending
        investor — no money moves and no login is sent until you do that from their page.</p>
      <QueryState q={q}>
        {(rows) => rows.length === 0 ? <Empty>No {status === 'all' ? '' : status} applications.</Empty> : (
          <div className="table-wrapper">
            <table>
              <thead><tr><th>Received</th><th>Applicant</th><th>Amount</th><th>Message</th><th>Status</th><th>Decide</th></tr></thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(a.created_at)}</td>
                    <td><strong>{a.name}</strong><div className="inv-hint">{a.email}{a.phone && ` · ${a.phone}`}{a.country && ` · ${a.country}`}</div></td>
                    <td>{a.amount_band || '—'}</td>
                    <td style={{ maxWidth: 300 }}>{a.message || ''}</td>
                    <td><StateBadge state={a.status} /></td>
                    <td>
                      {a.status !== 'accepted' && (
                        <div className="inv-actions">
                          <ActionForm label="Accept" icon={Check} tone="primary" compact onSubmit={() => accept.mutateAsync(a.id)} />
                          {a.status !== 'contacted' && <ActionForm label="Contacted" icon={Phone} compact onSubmit={() => mark.mutateAsync({ id: a.id, s: 'contacted' })} />}
                          {a.status !== 'declined' && <ActionForm label="Decline" icon={X} tone="danger" compact onSubmit={() => mark.mutateAsync({ id: a.id, s: 'declined' })} />}
                        </div>
                      )}
                    </td>
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
