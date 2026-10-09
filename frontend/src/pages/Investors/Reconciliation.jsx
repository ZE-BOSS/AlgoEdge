import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { inv } from '../../services/api';
import { QueryState } from './shared';
import { fmtMoney, fmtUnits, isZero, fmtPrice } from './format';
import { HEALTH_SHORT, HEALTH_STYLE, HEALTH_TITLE, VERIFIED, healthAdvice, healthState } from './health';

export default function Reconciliation() {
  const [draft, setDraft] = useState('');
  const [equity, setEquity] = useState(null);
  const q = useQuery({ queryKey: ['inv', 'reconciliation', equity], queryFn: () => inv.reconciliation(equity) });

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <form className="inv-inline" onSubmit={(e) => { e.preventDefault(); setEquity(draft.trim() || null); }}>
          <div>
            <label>Broker equity right now (USD)</label>
            <input inputMode="decimal" value={draft} onChange={e => setDraft(e.target.value)}
                   placeholder="blank = latest valuation" />
          </div>
          <button className="btn btn-primary btn-sm" type="submit">Reconcile</button>
        </form>
        <div className="inv-hint" style={{ marginTop: 6 }}>
          The investor side never reads MT5 directly, so the live figure is entered here. Without one,
          the latest valuation's equity is used — which only proves the books agree with themselves.
        </div>
      </div>

      <QueryState q={q}>
        {(r) => (
          <>
            <div className="card inv-health"
                 style={{ borderColor: HEALTH_STYLE[healthState(r)].border }}>
              {healthState(r) === VERIFIED
                ? <CheckCircle2 size={18} color={HEALTH_STYLE[VERIFIED].colour} />
                : <AlertTriangle size={18} color={HEALTH_STYLE[healthState(r)].colour} />}
              <div>
                <div style={{ fontWeight: 600 }}>{HEALTH_TITLE[healthState(r)]}</div>
                <div className="inv-hint">
                  As of {r.as_of} · pool equity {fmtMoney(r.pool_equity)} ({r.pool_equity_source}) ·
                  unit price {fmtPrice(r.nav_per_unit)} · {fmtUnits(r.units_in_issue, 2)} units
                </div>
                {(r.broker_missing || []).length > 0 && (
                  <ul className="inv-hint" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {r.broker_missing.map((m) => <li key={m}>{m}</li>)}
                  </ul>
                )}
              </div>
            </div>

            {r.shortfall && (
              <div className="card">
                <h3 style={{ marginTop: 0 }}>What would have to be covered</h3>
                <div className="inv-hint" style={{ marginBottom: 10 }}>
                  What investors are owed, against what the fund actually holds —
                  cash at the broker plus cash confirmed in and not yet sent there.
                  Money in transit is an asset: leaving it out would mark every
                  unit down for cash sitting in a bank account.
                </div>
                <table className="inv-table">
                  <tbody>
                    <tr><td>Investor claims</td>
                        <td className="num">{fmtMoney(r.shortfall.investor_claims)}</td></tr>
                    <tr><td>Pool value</td>
                        <td className="num">{fmtMoney(r.shortfall.pool_equity)}</td></tr>
                    <tr><td>Owed out (approved withdrawals, charged fees)</td>
                        <td className="num">{fmtMoney(r.shortfall.owed_out)}</td></tr>
                    <tr><td>Available to investors</td>
                        <td className="num">{fmtMoney(r.shortfall.available_to_investors)}</td></tr>
                    <tr style={{ fontWeight: 600 }}>
                      <td>{r.shortfall.is_covered ? 'Covered, with room of' : 'Shortfall to cover'}</td>
                      <td className="num" style={{
                        color: r.shortfall.is_covered ? 'var(--green)' : 'var(--red)' }}>
                        {fmtMoney(r.shortfall.is_covered
                          ? String(-Number(r.shortfall.shortfall))
                          : r.shortfall.shortfall)}
                      </td>
                    </tr>
                    {r.shortfall.confirmed_but_not_funded
                      && r.shortfall.confirmed_but_not_funded !== '0.00' && (
                      <tr><td className="inv-hint">
                            of which confirmed in but not yet sent to the broker</td>
                          <td className="num inv-hint">
                            {fmtMoney(r.shortfall.confirmed_but_not_funded)}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            <div className="card">
              <div className="table-wrapper">
                <table>
                  <thead><tr><th>Check</th><th className="num">Pool (raw)</th>
                    <th className="num">Investor-facing</th><th className="num">Difference</th></tr></thead>
                  <tbody>
                    {r.lines.map(l => {
                      // Red only where a difference is a DEFECT. Three of
                      // these five are legitimately non-zero at any moment —
                      // money in transit, profit not yet published — and
                      // colouring those red makes the page permanently alarming,
                      // which is how a real difference goes unnoticed.
                      const zero = isZero(l.difference);
                      const colour = zero ? 'var(--green)'
                        : (l.must_balance === false ? 'var(--warn, #d29922)' : 'var(--red)');
                      return (
                      <tr key={l.label}>
                        <td>
                          <div style={{ fontWeight: 600 }}>
                            {l.label}
                            {l.must_balance === false && (
                              <span className="inv-hint" style={{ fontWeight: 400 }}>
                                {' '}· may differ
                              </span>
                            )}
                          </div>
                          <div className="inv-hint">{l.note}</div>
                          {!zero && l.means && (
                            <div className="inv-hint" style={{ marginTop: 3, fontStyle: 'italic' }}>
                              This difference is {l.means}.
                            </div>
                          )}
                        </td>
                        <td className="num">{fmtMoney(l.pool)}</td>
                        <td className="num">{fmtMoney(l.investor_facing)}</td>
                        <td className="num" style={{ color: colour, fontWeight: 600 }}>
                          {fmtMoney(l.difference, { sign: true })}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="card">
              <div className="card-header"><span className="card-title">Ledger vs cache</span></div>
              {r.ledger_drift.length === 0 ? (
                <div className="inv-hint">Every investor's cached balance matches a replay of the ledger.</div>
              ) : (
                <div className="table-wrapper">
                  <table>
                    <thead><tr><th>Investor</th><th className="num">Ledger</th><th className="num">Cache</th></tr></thead>
                    <tbody>
                      {r.ledger_drift.map(d => (
                        <tr key={d.investor_id}>
                          <td><Link to={`/investors/p/${d.investor_id}`}>{d.investor_id}</Link></td>
                          <td className="num">{fmtUnits(d.ledger)}</td>
                          <td className="num" style={{ color: 'var(--red)' }}>{fmtUnits(d.cache)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </QueryState>
    </div>
  );
}
