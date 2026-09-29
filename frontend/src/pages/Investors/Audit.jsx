import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { inv } from '../../services/api';
import { Empty, QueryState } from './shared';
import { fmtDate } from './format';

function Log() {
  const [action, setAction] = useState('');
  const q = useQuery({
    queryKey: ['inv', 'audit', action],
    queryFn: () => inv.auditLog(action ? { action } : {}),
  });
  return (
    <>
      <div className="inv-inline" style={{ marginBottom: 12 }}>
        <div>
          <label>Action starts with</label>
          <select value={action} onChange={e => setAction(e.target.value)}>
            <option value="">everything</option>
            {['investor', 'deposit', 'withdrawal', 'closure', 'ledger', 'nav', 'settings', 'disclosure']
              .map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
      </div>
      <QueryState q={q}>
        {(rows) => rows.length === 0 ? <Empty>Nothing recorded.</Empty> : (
          <div className="table-wrapper">
            <table>
              <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                    <td>{r.actor_kind}<div className="inv-hint">{r.actor_id}</div></td>
                    <td><code>{r.action}</code></td>
                    <td>{r.entity_type}<div className="inv-hint">{r.entity_id}</div></td>
                    <td><code className="inv-json">{r.detail ? JSON.stringify(r.detail) : ''}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </>
  );
}

function Adjustments() {
  const q = useQuery({ queryKey: ['inv', 'adjustments'], queryFn: () => inv.adjustments() });
  return (
    <QueryState q={q}>
      {(rows) => rows.length === 0 ? <Empty>No adjustments have been made.</Empty> : (
        <div className="table-wrapper">
          <table>
            <thead><tr><th>When</th><th>What</th><th>Field</th><th>Old</th><th>New</th><th>Reason</th><th>By</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                  <td>{r.entity_type}<div className="inv-hint">{r.entity_id}</div></td>
                  <td>{r.field}</td>
                  <td className="num">{r.old_value ?? '—'}</td>
                  <td className="num">{r.new_value ?? '—'}</td>
                  <td style={{ maxWidth: 280 }}>{r.reason}</td>
                  <td>{r.actor_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryState>
  );
}

function Emails() {
  const q = useQuery({ queryKey: ['inv', 'emails'], queryFn: () => inv.emails() });
  const tone = { sent: 'green', logged: 'blue', failed: 'red', queued: 'yellow' };
  return (
    <QueryState q={q}>
      {(rows) => rows.length === 0 ? <Empty>No emails yet.</Empty> : (
        <div className="table-wrapper">
          <table>
            <thead><tr><th>When</th><th>To</th><th>Subject</th><th>Kind</th><th>State</th><th>Provider id / error</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                  <td>{r.to}</td>
                  <td style={{ maxWidth: 280 }}>{r.subject}{r.attachments && <div className="inv-hint">{r.attachments.join(', ')}</div>}</td>
                  <td><code>{r.kind}</code></td>
                  <td><span className={`badge badge-${tone[r.state] || 'blue'}`}>{r.state}</span></td>
                  <td className="inv-hint" style={{ maxWidth: 260 }}>{r.error || r.provider_id || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryState>
  );
}

export default function Audit() {
  const [tab, setTab] = useState('log');
  return (
    <div className="card">
      <div className="filter-tabs" style={{ marginBottom: 12 }}>
        <button className={`btn btn-sm ${tab === 'log' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('log')}>
          Audit log</button>
        <button className={`btn btn-sm ${tab === 'adj' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('adj')}>
          Adjustments</button>
        <button className={`btn btn-sm ${tab === 'mail' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('mail')}>
          Emails</button>
      </div>
      <p className="inv-hint" style={{ marginTop: 0 }}>
        {tab === 'log' ? 'Every state change on the investor side, by whom. Immutable.'
          : tab === 'adj' ? 'Every override of a figure an investor can see: the old value, the new one, and why.'
            : 'Every email sent: "logged" means EMAIL_MODE=log (nothing was sent); a failure shows the provider\u2019s error.'}
      </p>
      {tab === 'log' ? <Log /> : tab === 'adj' ? <Adjustments /> : <Emails />}
    </div>
  );
}
