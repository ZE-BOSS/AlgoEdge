import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Banknote } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, ErrorLine, QueryState } from './shared';
import { fmtMoney, useInvAction, fmtPrice } from './format';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Fees are charged every two months: Jan-Feb, Mar-Apr, ... Nov-Dec.
// A period is named by its first month; the API takes any month inside it.
function periodLabel({ year, month }) {
  return `${MONTHS[month - 1]} to ${MONTHS[month]} ${year}`;
}

function recentPeriods(n = 12) {
  const d = new Date();
  let year = d.getUTCFullYear();
  let month = d.getUTCMonth() + 1;
  month = month % 2 === 1 ? month : month - 1;       // start of the current period
  const out = [];
  for (let i = 0; i < n; i += 1) {
    month -= 2;
    if (month < 1) { month += 12; year -= 1; }
    out.push({ year, month });
  }
  return out;                                          // most recent ended period first
}

function Preview({ year, month }) {
  const [waive, setWaive] = useState(() => new Set());
  const q = useQuery({ queryKey: ['inv', 'fees', 'preview', year, month], queryFn: () => inv.feePreview(year, month) });
  const close = useInvAction(() => inv.closeFees({ year, month, waive: [...waive] }));
  const send = useInvAction(() => inv.sendStatements({ year, month: month + 1 }));
  const toggle = (id) => setWaive((w) => { const n = new Set(w); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <QueryState q={q}>
      {(p) => (
        <>
          <p className="inv-hint" style={{ marginTop: 0 }}>
            {p.already_closed ? 'Charged — these are the recorded charges. ' : `Valued as of ${p.priced_on} (unit price ${fmtPrice(p.nav_per_unit)}). `}
            Management = rate x the period&apos;s profit (nothing in a losing period), taken first.
            Performance = rate x lifetime profit after management, above the investor&apos;s high-water mark (set net of the fee).
            Rates are each investor&apos;s own terms.
          </p>
          {p.lines.length === 0 ? <Empty>No investor had money in the fund at the end of this period.</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Investor</th><th className="num">Value</th>
                  <th className="num">Period profit</th>
                  <th className="num">Management</th><th className="num">Lifetime profit</th><th className="num">Mark</th>
                  <th className="num">Performance</th><th className="num">Total</th>
                  {!p.already_closed && <th>Waive</th>}</tr></thead>
                <tbody>
                  {p.lines.map((l) => (
                    <tr key={l.investor_id} style={waive.has(l.investor_id) ? { opacity: 0.5 } : undefined}>
                      <td>{l.name}</td>
                      <td className="num">{fmtMoney(l.value)}</td>
                      <td className="num">{fmtMoney(l.period_profit)}</td>
                      <td className="num">{fmtMoney(l.management)}<div className="inv-hint">{l.management_pct}% of period profit</div></td>
                      <td className="num">{l.profit ? fmtMoney(l.profit) : <span className="inv-hint">{l.above_mark ? `${fmtMoney(l.above_mark)} above mark` : '—'}</span>}</td>
                      <td className="num">{fmtMoney(l.high_water_mark ?? l.new_high_water_mark)}</td>
                      <td className="num">{fmtMoney(l.performance)}<div className="inv-hint">{l.performance_pct}%</div></td>
                      <td className="num"><strong>{fmtMoney(l.total)}</strong>{l.state === 'waived' && <div className="inv-hint">waived</div>}</td>
                      {!p.already_closed && (
                        <td><input type="checkbox" checked={waive.has(l.investor_id)}
                                   onChange={() => toggle(l.investor_id)} aria-label={`Waive ${l.name}`} /></td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
            <strong>Total {fmtMoney(p.total)}</strong>
            {p.already_closed && (
              <button className="btn btn-secondary btn-sm" disabled={send.isPending || send.isSuccess}
                      onClick={() => send.mutate()}>
                {send.isSuccess ? `Statements queued for ${send.data.data.queued} investor(s)` : `Email ${MONTHS[month]} statements`}
              </button>
            )}
            {p.already_closed ? <span className="badge badge-green">charged</span> : (
              <button className="btn btn-primary btn-sm" disabled={close.isPending || !p.lines.length}
                      onClick={() => close.mutate()}>
                <Check size={12} /> Charge these fees{waive.size ? ` (waiving ${waive.size})` : ''}
              </button>
            )}
          </div>
          <ErrorLine error={close.error || send.error} />
          {!p.already_closed && (
            <p className="inv-hint">Charging cannot be repeated for the period.
              A waived fee is recorded but not charged; the high-water mark still moves.
              A refund afterwards is a unit correction with a reason.</p>
          )}
        </>
      )}
    </QueryState>
  );
}

export default function Fees() {
  const choices = recentPeriods();
  const [period, setPeriod] = useState(choices[0]);
  const periods = useQuery({ queryKey: ['inv', 'fees', 'periods'], queryFn: inv.feePeriods });
  const paid = useInvAction((b) => inv.feesPaid(b));
  const value = `${period.year}-${String(period.month).padStart(2, '0')}`;
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <div className="card-header" style={{ flexWrap: 'wrap', gap: 8 }}>
          <span className="card-title">Charge a two-month period&apos;s fees</span>
          <select value={value} style={{ maxWidth: 220 }}
                  onChange={(e) => { const [y, m] = e.target.value.split('-'); setPeriod({ year: +y, month: +m }); }}>
            {choices.map((c) => (
              <option key={`${c.year}-${c.month}`} value={`${c.year}-${String(c.month).padStart(2, '0')}`}>
                {periodLabel(c)}
              </option>
            ))}
          </select>
        </div>
        <Preview key={value} year={period.year} month={period.month} />
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Charged periods</span></div>
        <p className="inv-hint" style={{ marginTop: 0 }}>Charged fees stay in the broker account, owed to the manager, until
          marked paid. Until then they are subtracted before every valuation so no investor is valued on them.</p>
        <QueryState q={periods}>
          {(rows) => rows.length === 0 ? <Empty>No fees charged yet.</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Period</th><th className="num">Management</th><th className="num">Performance</th>
                  <th className="num">Owed to manager</th><th className="num">Paid out</th><th className="num">Waived</th><th /></tr></thead>
                <tbody>
                  {rows.map((r) => {
                    return (
                      <tr key={r.period_start + r.period_end}>
                        <td>{r.period_start} → {r.period_end}</td>
                        <td className="num">{fmtMoney(r.management)}</td>
                        <td className="num">{fmtMoney(r.performance)}</td>
                        <td className="num">{fmtMoney(r.charged)}</td>
                        <td className="num">{fmtMoney(r.paid)}</td>
                        <td className="num">{fmtMoney(r.waived)}</td>
                        <td>{r.charged !== '0.00' && (
                          <ActionForm label="Mark withdrawn" icon={Banknote} compact submitLabel="Mark paid"
                                      fields={[{ name: 'reference', label: 'Reference' },
                                               { name: 'on', label: 'Day withdrawn', type: 'date', hint: 'Blank = today.' }]}
                                      onSubmit={(b) => paid.mutateAsync({ period_start: r.period_start, period_end: r.period_end, ...b })} />
                        )}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </div>
    </div>
  );
}
