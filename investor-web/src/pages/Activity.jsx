import { api, download } from '../api';
import { useLoad } from '../hooks';
import { useState } from 'react';
import { Badge, Card, Loaded, Problem } from '../components/ui';
import { STATE_LABEL, day, isNeg, money, units } from '../format';

const TONE = { paid: 'good', confirmed: 'good', declined: 'bad', rejected: 'bad', approved: 'info' };

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];

function Statements() {
  const q = useLoad(api.statements);
  const [error, setError] = useState(null);
  return (
    <Card title="Monthly statements">
      <Loaded q={q}>
        {(rows) => rows.length === 0 ? <p className="muted">Your first statement appears after your first full month.</p> : (
          <ul className="rows">
            {rows.slice(0, 24).map((r) => (
              <li key={r.label}>
                <strong>{MONTHS[r.month - 1]} {r.year}</strong>
                <button className="btn small" onClick={() => download(`/statements/${r.year}-${r.month}.pdf`,
                  `alphavantiq-statement-${r.label}.pdf`).catch(setError)}>Download PDF</button>
              </li>
            ))}
          </ul>
        )}
      </Loaded>
      <Problem error={error} />
    </Card>
  );
}

export default function Activity() {
  const q = useLoad(api.activity);
  return (
    <Loaded q={q}>
      {({ ledger, deposits, withdrawals }) => (
        <div className="stack">
          <Statements />
          <Card title="Withdrawals">
            {withdrawals.length === 0 ? <p className="muted">None yet.</p> : (
              <ul className="rows">
                {withdrawals.map((w) => (
                  <li key={w.id}>
                    <div>
                      <strong className="num">{money(w.amount_paid || w.amount_requested)}</strong>
                      <span className="muted small">Requested {day(w.created_at)}
                        {w.expected_by && ` · expected by ${day(w.expected_by)}`}
                        {w.paid_at && ` · paid ${day(w.paid_at)}`}
                        {w.payment_reference && ` · ref ${w.payment_reference}`}</span>
                      {w.reason && <span className="small">{w.reason}</span>}
                    </div>
                    <Badge tone={TONE[w.state] || 'neutral'}>{STATE_LABEL[w.state] || w.state}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Money in">
            {deposits.length === 0 ? <p className="muted">None yet.</p> : (
              <ul className="rows">
                {deposits.map((d) => (
                  <li key={d.id}>
                    <div>
                      <strong className="num">{money(d.amount_confirmed || d.amount_claimed)}</strong>
                      <span className="muted small">
                        {d.priced_on ? `Units bought ${day(d.priced_on)}` : `Told us ${day(d.created_at)}`}
                        {d.amount_confirmed && d.amount_claimed && d.amount_confirmed !== d.amount_claimed &&
                          ` · you told us ${money(d.amount_claimed)}, ${money(d.amount_confirmed)} arrived`}
                      </span>
                      {d.reason && <span className="small">{d.reason}</span>}
                    </div>
                    <Badge tone={TONE[d.state] || 'neutral'}>{STATE_LABEL[d.state] || d.state}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Every movement of your units">
            {ledger.length === 0 ? <p className="muted">None yet.</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Date</th><th>What</th><th className="num">Units</th>
                    <th className="num hide-sm">Unit price</th><th className="num">Amount</th></tr></thead>
                  <tbody>
                    {ledger.map((t) => (
                      <tr key={t.id}>
                        <td>{day(t.date)}</td><td>{t.label}</td>
                        <td className={`num ${isNeg(t.units) ? 'bad' : ''}`}>{units(t.units)}</td>
                        <td className="num hide-sm">{money(t.nav_per_unit)}</td>
                        <td className="num">{money(t.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </Loaded>
  );
}
