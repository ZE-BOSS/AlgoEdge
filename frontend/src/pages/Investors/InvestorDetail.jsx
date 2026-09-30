import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Plus, Wrench, Pencil, Landmark, DoorOpen, Undo2, KeyRound, Copy } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, ErrorLine, QueryState, StateBadge } from './shared';
import { fmtDate, fmtMoney, fmtUnits, isNeg, useInvAction, fmtPrice } from './format';

function Statement({ s }) {
  return (
    <div className="kpi-strip">
      <div className="kpi"><div className="kpi-label">Current value</div>
        <div className="kpi-value">{fmtMoney(s.current_value)}</div></div>
      <div className="kpi"><div className="kpi-label">Units</div>
        <div className="kpi-value">{fmtUnits(s.units)}</div></div>
      <div className="kpi"><div className="kpi-label">Capital in</div>
        <div className="kpi-value">{fmtMoney(s.capital_in)}</div></div>
      <div className="kpi"><div className="kpi-label">Withdrawn</div>
        <div className="kpi-value">{fmtMoney(s.withdrawn)}</div></div>
      <div className="kpi"><div className="kpi-label">Profit</div>
        <div className="kpi-value" style={{ color: isNeg(s.profit) ? 'var(--red)' : 'var(--green)' }}>
          {fmtMoney(s.profit, { sign: true })}</div></div>
      <div className="kpi"><div className="kpi-label">Share of pool</div>
        <div className="kpi-value">{s.share_of_pool_pct}%</div></div>
      <div className="kpi"><div className="kpi-label">Withdrawable now</div>
        <div className="kpi-value">{fmtMoney(s.withdrawable_now)}</div>
        <div className="inv-hint">{s.withdrawable_explanation}</div></div>
    </div>
  );
}

function Closure({ investor }) {
  const id = investor.id;
  const quote = useQuery({
    queryKey: ['inv', 'closure', id], queryFn: () => inv.closureQuote(id),
    enabled: investor.status === 'closing',
  });
  const request = useInvAction((b) => inv.requestClosure(id, b));
  const cancel = useInvAction(() => inv.cancelClosure(id));
  const approve = useInvAction((b) => inv.approveClosure(id, b));

  if (investor.status === 'closed') {
    return <div className="inv-hint">Closed {fmtDate(investor.closed_at)}. Personal details were
      erased on approval; the ledger and audit trail are kept.</div>;
  }
  if (investor.status !== 'closing') {
    return (
      <ActionForm label="Request closure" icon={DoorOpen} tone="danger" submitLabel="Mark as closing"
                  fields={[{ name: 'reason', label: 'Why (optional)', type: 'textarea' }]}
                  onSubmit={(b) => request.mutateAsync(b)} />
    );
  }
  return (
    <QueryState q={quote}>
      {(qt) => (
        <div style={{ display: 'grid', gap: 10 }}>
          <div className="detail-pairs inv-pairs">
            <span>Units</span><span className="num">{fmtUnits(qt.units)}</span>
            <span>Unit price</span><span className="num">{fmtPrice(qt.nav_per_unit)}</span>
            <span>Gross value</span><span className="num">{fmtMoney(qt.gross_value)}</span>
            <span>Fees owed</span><span className="num">{fmtMoney(qt.fees_owed)}</span>
            <span><strong>Net payable</strong></span><span className="num"><strong>{fmtMoney(qt.net_payable)}</strong></span>
          </div>
          {qt.blockers.length > 0 && (
            <ul className="inv-blockers">{qt.blockers.map(b => <li key={b}>{b}</li>)}</ul>
          )}
          <div className="inv-hint">
            Pay {fmtMoney(qt.net_payable)} to the investor first, then approve with the transfer
            reference. Approval redeems every unit and <strong>permanently erases</strong> their
            name, email, phone and bank details. This cannot be undone.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <ActionForm label="Approve closure" tone="danger" submitLabel="Close account"
                        disabled={qt.blockers.length > 0}
                        fields={[{ name: 'payment_reference', label: 'Payout transfer reference', required: true }]}
                        confirm={{ label: `Type the investor's name to confirm`, match: investor.name }}
                        onSubmit={(b) => approve.mutateAsync(b)} />
            <ActionForm label="Cancel closure" icon={Undo2} onSubmit={() => cancel.mutateAsync()} />
          </div>
        </div>
      )}
    </QueryState>
  );
}

/**
 * A single-use link for the investor to set their password. Shown once: only
 * its hash is stored, so if it is lost the answer is a new link (which also
 * retires this one). Until Phase 4 emails it, the admin sends it by hand.
 */
function LoginLink({ investor }) {
  const [link, setLink] = useState(null);
  const [copied, setCopied] = useState(false);
  const make = useInvAction(({ purpose, send }) => inv.loginLink(investor.id, purpose, send),
    { onSuccess: (res) => { setLink(res.data); setCopied(false); } });
  if (investor.status === 'closed') return null;
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div className="inv-hint">
        Investors sign in at the investor app with their own password. An invitation is emailed to
        {' '}{investor.email} (or copy the link and send it yourself). It works once and expires in
        72 hours (a reset in 30 minutes); a new link cancels the previous one.
      </div>
      <div className="inv-actions">
        <button className="btn btn-primary btn-sm" disabled={make.isPending}
                onClick={() => make.mutate({ purpose: 'invite', send: true })}><KeyRound size={12} /> Email invitation</button>
        <button className="btn btn-secondary btn-sm" disabled={make.isPending}
                onClick={() => make.mutate({ purpose: 'invite', send: false })}><KeyRound size={12} /> Copy invitation link</button>
        <button className="btn btn-secondary btn-sm" disabled={make.isPending}
                onClick={() => make.mutate({ purpose: 'reset', send: true })}><KeyRound size={12} /> Email password reset</button>
      </div>
      <ErrorLine error={make.error} />
      {link && (
        <div className="inv-link-box">
          <code>{link.url}</code>
          <button className="btn btn-primary btn-sm" onClick={() => {
            navigator.clipboard?.writeText(link.url).then(() => setCopied(true));
          }}><Copy size={12} /> {copied ? 'Copied' : 'Copy'}</button>
          <span className="inv-hint">{link.emailed ? `Emailed to ${investor.email}. ` : ''}Expires {fmtDate(link.expires_at)} UTC. This is the only time it is shown.</span>
        </div>
      )}
    </div>
  );
}

function Statements({ investor }) {
  const [month, setMonth] = useState(() => {
    const d = new Date(); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
  });
  const [error, setError] = useState(null);
  const open = async () => {
    setError(null);
    const [y, m] = month.split('-');
    try {
      const res = await inv.statementPdf(investor.id, +y, +m);
      window.open(URL.createObjectURL(res.data), '_blank', 'noopener');
    } catch (e) { setError(e); }
  };
  return (
    <div className="inv-actions" style={{ alignItems: 'center' }}>
      <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} style={{ maxWidth: 170 }} />
      <button className="btn btn-secondary btn-sm" onClick={open}>Open statement (PDF)</button>
      <ErrorLine error={error} />
    </div>
  );
}

function Profile({ investor }) {
  const id = investor.id;
  const update = useInvAction((b) => inv.update(id, b));
  const closed = investor.status === 'closed';
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div className="detail-pairs inv-pairs">
        <span>Email</span><span>{investor.email}</span>
        <span>Phone</span><span>{investor.phone || '—'}</span>
        <span>Country</span><span>{investor.country || '—'}</span>
        <span>KYC</span><span>{investor.kyc_status}</span>
        <span>Terms version</span><span>{investor.terms_version ?? '—'}</span>
        <span>Joined</span><span>{fmtDate(investor.activated_at || investor.created_at)}</span>
      </div>
      {!closed && (
        <ActionForm label="Edit details" icon={Pencil} submitLabel="Save"
                    fields={[
                      { name: 'name', label: 'Name', defaultValue: investor.name },
                      { name: 'phone', label: 'Phone', defaultValue: investor.phone || '' },
                      { name: 'country', label: 'Country', defaultValue: investor.country || '' },
                      { name: 'kyc_status', label: 'KYC status', defaultValue: investor.kyc_status },
                    ]}
                    onSubmit={(b) => update.mutateAsync(b)} />
      )}

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
        <div className="kpi-label">Payout account</div>
        <div className="detail-pairs inv-pairs">
          <span>Bank</span><span>{investor.payout_bank_name || '—'}</span>
          <span>Account no.</span><span className="num">{investor.payout_account_number || '—'}</span>
          <span>Account name</span><span>{investor.payout_account_name || '—'}</span>
        </div>
        {!closed && (
          <ActionForm label="Change payout account" icon={Landmark} submitLabel="Save"
                      fields={[
                        { name: 'payout_bank_name', label: 'Bank', defaultValue: investor.payout_bank_name || '' },
                        { name: 'payout_account_number', label: 'Account number', defaultValue: investor.payout_account_number || '' },
                        { name: 'payout_account_name', label: 'Account name', defaultValue: investor.payout_account_name || '' },
                        { name: 'reason', label: 'Reason', type: 'textarea', required: true,
                          hint: 'Where withdrawals go. Recorded with the old account — say how the change was verified.' },
                      ]}
                      onSubmit={(b) => update.mutateAsync(b)} />
        )}
      </div>
    </div>
  );
}

function Money({ investor }) {
  const id = investor.id;
  const deposit = useInvAction((b) => inv.recordDeposit(id, b));
  const correct = useInvAction((b) => inv.correct(id, b));
  if (investor.status === 'closed') return null;
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
      <ActionForm label="Record deposit" icon={Plus} tone="primary" submitLabel="Record"
                  fields={[
                    { name: 'amount', label: 'Amount received (USD)', type: 'number', required: true },
                    { name: 'on', label: 'Date it arrived', type: 'date',
                      hint: 'Leave blank for today. Units are issued at the NAV of this day.' },
                    { name: 'note', label: 'Note' },
                    { name: 'min_override_reason', label: 'Below the minimum? Reason to accept it',
                      hint: 'Only for a first deposit under this investor\'s minimum. Recorded in the audit log.' },
                  ]}
                  onSubmit={(b) => deposit.mutateAsync(b)} />
      <ActionForm label="Correct units" icon={Wrench} submitLabel="Write correction"
                  fields={[
                    { name: 'unit_delta', label: 'Units (+ to add, − to remove)', type: 'number', required: true },
                    { name: 'reason', label: 'Reason', type: 'textarea', required: true,
                      hint: 'A compensating ledger row. History is never edited.' },
                  ]}
                  onSubmit={(b) => correct.mutateAsync(b)} />
    </div>
  );
}

/** Gross (no fees ever charged) against net (what the investor sees). Both are
 *  real ledger figures; the difference is exactly what fees have cost them. */
/** The capital-vs-profit view this investor sees on their dashboard. */
function CapitalView({ c }) {
  const bad = c.state === 'capital_loss';
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">Capital and profit (as the investor sees it)</span></div>
      <div className="detail-pairs inv-pairs">
        <span>Paid in</span><span className="num">{fmtMoney(c.paid_in)}</span>
        <span>Paid out</span><span className="num">{fmtMoney(c.paid_out)}</span>
        <span>Net invested</span><span className="num">{fmtMoney(c.net_invested)}</span>
        <span>Balance now</span><span className="num">{fmtMoney(c.value)}</span>
        <span><strong>{bad ? 'Capital lost' : 'Profit on top'}</strong></span>
        <span className="num" style={{ color: bad ? 'var(--red)' : 'var(--green)' }}>
          <strong>{fmtMoney(bad ? `-${c.capital_eroded}` : c.profit_on_top, { sign: true })}</strong>
          {c.profit_pct_of_capital && ` (${c.profit_pct_of_capital}% of capital)`}</span>
        <span>Fees paid</span><span className="num">{fmtMoney(c.fees_paid)}</span>
      </div>
      <p className="inv-hint">{c.state === 'not_invested' ? 'No money in yet.'
        : bad ? 'Their dashboard tells them losses are eating into their capital.'
          : 'Their dashboard tells them their capital is intact with profit on top.'}</p>
    </div>
  );
}

/** Their share of each published trade, exactly as their Trades tab shows it. */
function TradeShares({ t }) {
  const rows = t.trades.filter((r) => r.your_amount !== null).slice(0, 12);
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Their share of published trades</span>
        <span className="inv-hint">{t.summary.wins} gains · {t.summary.losses} losses · total {fmtMoney(t.summary.your_total, { sign: true })}</span>
      </div>
      {rows.length === 0 ? <div className="inv-hint">Not invested when any published trade closed.</div> : (
        <div className="table-wrapper">
          <table>
            <thead><tr><th>Closed</th><th>Trade</th><th className="num">Their share of fund</th><th className="num">Their result</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.closed_on}</td><td>{r.symbol} {r.direction}</td>
                  <td className="num">{r.your_share_pct}%</td>
                  <td className="num" style={{ color: isNeg(r.your_amount) ? 'var(--red)' : 'var(--green)' }}>
                    {fmtMoney(r.your_amount, { sign: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function GrossNet({ g }) {
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">Before and after fees</span></div>
      <div className="kpi-strip" style={{ margin: 0 }}>
        <div className="kpi"><div className="kpi-label">Gross value (no fees)</div>
          <div className="kpi-value">{fmtMoney(g.gross_value)}</div></div>
        <div className="kpi"><div className="kpi-label">Net value (investor sees)</div>
          <div className="kpi-value">{fmtMoney(g.net_value)}</div></div>
        <div className="kpi"><div className="kpi-label">Difference</div>
          <div className="kpi-value">{fmtMoney(g.difference)}</div>
          <div className="inv-hint" style={{ margin: 0 }}>{g.difference_pct}% of gross</div></div>
        <div className="kpi"><div className="kpi-label">Fees charged (at the time)</div>
          <div className="kpi-value">{fmtMoney(g.fees_charged)}</div></div>
      </div>
      <p className="inv-hint">The difference is the fees at today&apos;s value; &quot;fees charged&quot; is the dollar
        amount on the day each was taken. They differ only by how the fund has moved since.</p>
    </div>
  );
}

/** This investor's own fee rates and minimum. Blank = the fund's terms. The
 *  investor's app shows exactly these, and the fees engine charges exactly these. */
function Terms({ investor, terms }) {
  const save = useInvAction((b) => inv.setTerms(investor.id, {
    performance_fee_pct: b.performance_fee_pct === '' || b.performance_fee_pct == null ? null : b.performance_fee_pct,
    management_fee_pct: b.management_fee_pct === '' || b.management_fee_pct == null ? null : b.management_fee_pct,
    min_investment: b.min_investment === '' || b.min_investment == null ? null : b.min_investment,
    reason: b.reason,
  }));
  const own = (k) => terms.overridden.includes(k);
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">Fee terms for this investor</span></div>
      <div className="detail-pairs inv-pairs">
        <span>Performance fee</span><span className="num">{terms.performance_fee_pct}%{own('performance_fee_pct') ? ' (own)' : ' (fund)'}</span>
        <span>Management fee</span><span className="num">{terms.management_fee_pct}% of each two-month period&apos;s profit{own('management_fee_pct') ? ' (own)' : ' (fund)'}</span>
        <span>Minimum first deposit</span><span className="num">{fmtMoney(terms.min_investment)}{own('min_investment') ? ' (own)' : ' (fund)'}</span>
      </div>
      {investor.status !== 'closed' && (
        <div style={{ marginTop: 10 }}>
          <ActionForm label="Change this investor's terms" submitLabel="Save terms"
                      fields={[
                        { name: 'performance_fee_pct', label: 'Performance fee %', type: 'number',
                          defaultValue: investor.performance_fee_pct ?? '', hint: 'Blank = the fund\'s rate.' },
                        { name: 'management_fee_pct', label: 'Management fee % of period profit', type: 'number',
                          defaultValue: investor.management_fee_pct ?? '', hint: 'Blank = the fund\'s rate.' },
                        { name: 'min_investment', label: 'Minimum first deposit (USD)', type: 'number',
                          defaultValue: investor.min_investment ?? '', hint: 'Blank = the fund\'s minimum.' },
                        { name: 'reason', label: 'Reason', type: 'textarea', required: true },
                      ]}
                      onSubmit={(b) => save.mutateAsync(b)} />
          <p className="inv-hint">Applies from the next fee period charged. Periods already charged stay as they were.</p>
        </div>
      )}
    </div>
  );
}

function Table({ title, rows, cols, empty }) {
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">{title}</span></div>
      {rows.length === 0 ? <div className="inv-hint">{empty}</div> : (
        <div className="table-wrapper">
          <table>
            <thead><tr>{cols.map(c => <th key={c[0]} className={c[2] ? 'num' : ''}>{c[0]}</th>)}</tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>{cols.map(c => <td key={c[0]} className={c[2] ? 'num' : ''}>{c[1](r)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function InvestorDetail() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ['inv', 'investor', id], queryFn: () => inv.get(id) });

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Link to="/investors/list" className="inv-hint" style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
        <ArrowLeft size={14} /> All investors
      </Link>
      <QueryState q={q}>
        {(d) => !d ? <Empty>Investor not found</Empty> : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <h3 style={{ margin: 0 }}>{d.investor.name}</h3>
              <StateBadge state={d.investor.status} />
            </div>

            <Statement s={d.statement} />
            <Money investor={d.investor} />

            <div className="grid-2">
              <GrossNet g={d.gross_net} />
              <Terms investor={d.investor} terms={d.terms} />
            </div>

            <div className="grid-2">
              <CapitalView c={d.capital} />
              <TradeShares t={d.trades} />
            </div>

            <div className="grid-2">
              <div className="card">
                <div className="card-header"><span className="card-title">Details</span></div>
                <Profile investor={d.investor} />
              </div>
              <div className="card">
                <div className="card-header"><span className="card-title">Access &amp; closure</span></div>
                <Closure investor={d.investor} />
                <div style={{ borderTop: '1px solid var(--border)', marginTop: 16, paddingTop: 12 }}>
                  <div className="kpi-label" style={{ marginBottom: 6 }}>Statements</div>
                  <Statements investor={d.investor} />
                </div>
                <div style={{ borderTop: '1px solid var(--border)', marginTop: 16, paddingTop: 12 }}>
                  <div className="kpi-label" style={{ marginBottom: 6 }}>Investor app login</div>
                  <LoginLink investor={d.investor} />
                </div>
              </div>
            </div>

            <Table title="Unit ledger" rows={d.ledger} empty="No movements yet."
                   cols={[
                     ['Date', r => r.effective_date],
                     ['Kind', r => r.kind],
                     ['Units', r => fmtUnits(r.units), true],
                     ['Unit price', r => fmtPrice(r.nav_per_unit), true],
                     ['Amount', r => fmtMoney(r.amount), true],
                     ['Source', r => r.source_kind ? `${r.source_kind}${r.source_id ? ' ' + r.source_id : ''}` : '—'],
                     ['Note', r => r.note || ''],
                   ]} />
            <Table title="Deposits" rows={d.deposits} empty="No deposits."
                   cols={[
                     ['Created', r => fmtDate(r.created_at)],
                     ['State', r => <StateBadge state={r.state} />],
                     ['Claimed', r => fmtMoney(r.amount_claimed), true],
                     ['Confirmed', r => fmtMoney(r.amount_confirmed), true],
                     ['Priced on', r => r.effective_date || '—'],
                     ['Method', r => r.method],
                     ['Reason', r => r.rejected_reason || ''],
                   ]} />
            <Table title="Withdrawals" rows={d.withdrawals} empty="No withdrawals."
                   cols={[
                     ['Created', r => fmtDate(r.created_at)],
                     ['State', r => <StateBadge state={r.state} />],
                     ['Requested', r => fmtMoney(r.amount_requested), true],
                     ['Paid', r => fmtMoney(r.amount_paid), true],
                     ['Cap at request', r => fmtMoney(r.cap_at_request), true],
                     ['Reference', r => r.payment_reference || ''],
                     ['Note', r => r.justification || r.declined_reason || ''],
                   ]} />
            <Table title="Adjustments" rows={d.adjustments} empty="No adjustments — nothing about this investor has been overridden."
                   cols={[
                     ['When', r => fmtDate(r.created_at)],
                     ['Field', r => r.field],
                     ['Old', r => r.old_value ?? '—'],
                     ['New', r => r.new_value ?? '—'],
                     ['Reason', r => r.reason],
                     ['By', r => r.actor_id],
                   ]} />
          </>
        )}
      </QueryState>
    </div>
  );
}
