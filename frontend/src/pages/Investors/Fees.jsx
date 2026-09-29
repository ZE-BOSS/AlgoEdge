import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Banknote } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, ErrorLine, QueryState } from './shared';
import { fmtMoney, useInvAction, fmtPrice } from './format';

function lastMonth() {
  const d = new Date();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() - 1);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1 };
}

function Preview({ year, month }) {
  const [waive, setWaive] = useState(() => new Set());
  const q = useQuery({ queryKey: ['inv', 'fees', 'preview', year, month], queryFn: () => inv.feePreview(year, month) });
  const close = useInvAction(() => inv.closeFees({ year, month, waive: [...waive] }));
  const send = useInvAction(() => inv.sendStatements({ year, month }));
  const toggle = (id) => setWaive((w) => { const n = new Set(w); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <QueryState q={q}>
      {(p) => (
        <>
          <p className="inv-hint" style={{ marginTop: 0 }}>
            {p.already_closed ? 'Charged — these are the recorded charges. ' : `Valued as of ${p.priced_on} (unit price ${fmtPrice(p.nav_per_unit)}). `}
            Management = value × rate × days ÷ 365.
            Performance = rate × lifetime profit above the investor's high-water mark, which is set net of the fee.
            Rates are each investor's committed terms.
          </p>
          {p.lines.length === 0 ? <Empty>No investor held units at the end of this month.</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Investor</th><th className="num">Value</th><th className="num">Days</th>
                  <th className="num">Management</th><th className="num">Profit</th><th className="num">Mark</th>
                  <th className="num">Performance</th><th className="num">Total</th>
                  {!p.already_closed && <th>Waive</th>}</tr></thead>
                <tbody>
                  {p.lines.map((l) => (
                    <tr key={l.investor_id} style={waive.has(l.investor_id) ? { opacity: 0.5 } : undefined}>
                      <td>{l.name}</td>
                      <td className="num">{fmtMoney(l.value)}</td>
                      <td className="num">{l.days}</td>
                      <td className="num">{fmtMoney(l.management)}<div className="inv-hint">{l.management_pct}%/yr</div></td>
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
                {send.isSuccess ? `Statements queued for ${send.data.data.queued} investor(s)` : 'Email this month\u2019s statements'}
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
            <p className="inv-hint">Charging cancels units at this price and cannot be repeated for the month.
              A waived fee is recorded but not charged; the high-water mark still moves.
              A refund afterwards is a unit correction with a reason.</p>
          )}
        </>
      )}
    </QueryState>
  );
}

export default function Fees() {
  const [period, setPeriod] = useState(lastMonth);
  const periods = useQuery({ queryKey: ['inv', 'fees', 'periods'], queryFn: inv.feePeriods });
  const paid = useInvAction((b) => inv.feesPaid(b));
  const value = `${period.year}-${String(period.month).padStart(2, '0')}`;
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <div className="card-header" style={{ flexWrap: 'wrap', gap: 8 }}>
          <span className="card-title">Charge a month's fees</span>
          <input type="month" value={value} style={{ maxWidth: 180 }}
                 onChange={(e) => { const [y, m] = e.target.value.split('-'); if (y && m) setPeriod({ year: +y, month: +m }); }} />
        </div>
        <Preview key={value} year={period.year} month={period.month} />
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Charged months</span></div>
        <p className="inv-hint" style={{ marginTop: 0 }}>Charged fees stay in the broker account, owed to the manager, until
          marked paid. Until then they are subtracted before every NAV so no investor is priced on them.</p>
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
