import { Link, NavLink, Route, Routes } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Check, X, Banknote } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState, StateBadge } from './shared';
import { fmtDate, fmtMoney, fmtUnits, useInvAction } from './format';

function Who({ id, name }) {
  return <Link to={`/investors/p/${id}`} style={{ fontWeight: 600 }}>{name || id}</Link>;
}

function Deposits() {
  const q = useQuery({ queryKey: ['inv', 'q', 'deposits'], queryFn: () => inv.depositQueue() });
  const confirm = useInvAction(({ id, ...b }) => inv.confirmDeposit(id, b));
  const reject = useInvAction(({ id, ...b }) => inv.rejectDeposit(id, b));
  return (
    <QueryState q={q}>
      {(rows) => rows.length === 0 ? <Empty>No deposits waiting.</Empty> : (
        <div className="table-wrapper">
          <table>
            <thead><tr><th>Investor</th><th>Claimed</th><th>Reference</th><th>Created</th>
              <th>State</th><th>Decide</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td><Who id={r.investor_id} name={r.investor_name} /></td>
                  <td className="num">{fmtMoney(r.amount_claimed)} {r.currency !== 'USD' && r.currency}</td>
                  <td className="num">{r.reference_code || '—'}{r.has_proof && ' · proof'}</td>
                  <td>{fmtDate(r.created_at)}</td>
                  <td><StateBadge state={r.state} /></td>
                  <td>
                    <div className="inv-actions">
                      <ActionForm label="Confirm" icon={Check} tone="primary" compact
                                  fields={[
                                    { name: 'amount_confirmed', label: 'Amount that actually arrived', type: 'number',
                                      required: true, defaultValue: r.amount_claimed || '',
                                      hint: 'Units are issued for THIS figure, at the NAV of the day below.' },
                                    { name: 'on', label: 'Date it arrived', type: 'date' },
                                    { name: 'min_override_reason', label: 'Below the minimum? Reason to accept it' },
                                  ]}
                                  onSubmit={(b) => confirm.mutateAsync({ id: r.id, ...b })} />
                      <ActionForm label="Reject" icon={X} tone="danger" compact
                                  fields={[{ name: 'reason', label: 'Reason', type: 'textarea', required: true }]}
                                  onSubmit={(b) => reject.mutateAsync({ id: r.id, ...b })} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryState>
  );
}

function Withdrawals({ state, empty }) {
  const q = useQuery({ queryKey: ['inv', 'q', 'withdrawals', state], queryFn: () => inv.withdrawalQueue(state) });
  const approve = useInvAction(({ id, ...b }) => inv.approveWithdrawal(id, b));
  const pay = useInvAction(({ id, ...b }) => inv.payWithdrawal(id, b));
  const decline = useInvAction(({ id, ...b }) => inv.declineWithdrawal(id, b));
  const toPay = state === 'approved';

  return (
    <QueryState q={q}>
      {(rows) => rows.length === 0 ? <Empty>{empty}</Empty> : (
        <div className="table-wrapper">
          <table>
            <thead><tr>
              <th>Investor</th><th className="num">Amount</th>
              {toPay ? <><th>Approved</th><th>Pay to</th></>
                : <><th className="num">Holding now</th><th>Limit</th>{state === 'exception_pending' && <th>Justification</th>}</>}
              <th>Decide</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td><Who id={r.investor_id} name={r.investor_name} />
                    <div className="inv-hint">{fmtDate(r.created_at)}</div></td>
                  <td className="num">{fmtMoney(r.amount_requested)}</td>
                  {toPay ? (
                    <>
                      <td>{fmtDate(r.approved_at)}</td>
                      <td>{r.destination_bank_name || '—'}<div className="inv-hint num">
                        {r.destination_account_number} {r.destination_account_name}</div></td>
                    </>
                  ) : (
                    <>
                      <td className="num" style={{ color: r.affordable_now ? undefined : 'var(--red)' }}>
                        {fmtMoney(r.holding_value_now)}
                        {!r.affordable_now && <div className="inv-hint" style={{ color: 'var(--red)' }}>
                          no longer covers the request</div>}
                      </td>
                      <td><span className="num">{fmtMoney(r.cap_now)}</span>
                        <div className="inv-hint">{r.cap_explanation}</div>
                        <div className="inv-hint">at request: {fmtMoney(r.cap_at_request)}</div></td>
                      {state === 'exception_pending' && <td style={{ maxWidth: 260 }}>{r.justification}</td>}
                    </>
                  )}
                  <td>
                    <div className="inv-actions">
                      {toPay ? (
                        <ActionForm label="Mark paid" icon={Banknote} tone="primary" compact
                                    fields={[{ name: 'reference', label: 'Transfer reference', required: true },
                                              { name: 'on', label: 'Day it went out', type: 'date', hint: 'Blank = today.' }]}
                                    onSubmit={(b) => pay.mutateAsync({ id: r.id, ...b })} />
                      ) : (
                        <>
                          <ActionForm label="Approve" icon={Check} tone="primary" compact
                                      fields={[{ name: 'on', label: 'Effective date', type: 'date',
                                                 hint: 'Blank = today. Units are cancelled at this day’s NAV; paying is a separate step.' }]}
                                      onSubmit={(b) => approve.mutateAsync({ id: r.id, ...b })} />
                          <ActionForm label="Decline" icon={X} tone="danger" compact
                                      fields={[{ name: 'reason', label: 'Reason', type: 'textarea', required: true }]}
                                      onSubmit={(b) => decline.mutateAsync({ id: r.id, ...b })} />
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryState>
  );
}

function Closures() {
  const q = useQuery({ queryKey: ['inv', 'q', 'closures'], queryFn: inv.closureQueue });
  return (
    <QueryState q={q}>
      {(rows) => rows.length === 0 ? <Empty>No closures requested.</Empty> : (
        <div className="table-wrapper">
          <table>
            <thead><tr><th>Investor</th><th className="num">Units</th><th className="num">Net payable</th>
              <th>Blocked by</th><th /></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td><Who id={r.id} name={r.name} /></td>
                  <td className="num">{fmtUnits(r.quote.units)}</td>
                  <td className="num">{fmtMoney(r.quote.net_payable)}</td>
                  <td>{r.quote.blockers.length ? r.quote.blockers.join('; ') : <StateBadge state="ok" />}</td>
                  <td><Link className="btn btn-secondary btn-sm" to={`/investors/p/${r.id}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryState>
  );
}

export default function Queues() {
  const { data } = useQuery({ queryKey: ['inv', 'overview'], queryFn: inv.overview });
  const c = data?.data?.queues || {};
  const tabs = [
    { to: '/investors/queues', label: 'Deposits', n: c.deposits, end: true },
    { to: '/investors/queues/withdrawals', label: 'Withdrawals', n: c.withdrawals },
    { to: '/investors/queues/exceptions', label: 'Exceptions', n: c.exceptions },
    { to: '/investors/queues/to-pay', label: 'To pay', n: c.to_pay },
    { to: '/investors/queues/closures', label: 'Closures', n: c.closures },
  ];
  return (
    <div className="card">
      <div className="filter-tabs" style={{ marginBottom: 12 }}>
        {tabs.map(t => (
          <NavLink key={t.to} to={t.to} end={t.end}
                   className={({ isActive }) => `btn btn-sm ${isActive ? 'btn-primary' : 'btn-secondary'}`}>
            {t.label}{t.n > 0 && <span className="inv-count">{t.n}</span>}
          </NavLink>
        ))}
      </div>
      <Routes>
        <Route index element={<Deposits />} />
        <Route path="withdrawals" element={<Withdrawals state="requested" empty="No withdrawal requests within the limit." />} />
        <Route path="exceptions" element={<Withdrawals state="exception_pending" empty="No over-limit requests." />} />
        <Route path="to-pay" element={<Withdrawals state="approved" empty="Nothing approved and waiting to be paid." />} />
        <Route path="closures" element={<Closures />} />
      </Routes>
    </div>
  );
}
