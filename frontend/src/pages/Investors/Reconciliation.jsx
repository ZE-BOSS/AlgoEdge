import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { inv } from '../../services/api';
import { QueryState } from './shared';
import { fmtMoney, fmtUnits, isZero } from './format';

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
                   placeholder="blank = latest NAV snapshot" />
          </div>
          <button className="btn btn-primary btn-sm" type="submit">Reconcile</button>
        </form>
        <div className="inv-hint" style={{ marginTop: 6 }}>
          The investor side never reads MT5 directly, so the live figure is entered here. Without one,
          the latest snapshot's equity is used — which only proves the books agree with themselves.
        </div>
      </div>

      <QueryState q={q}>
        {(r) => (
          <>
            <div className="card inv-health" style={{ borderColor: r.healthy ? 'var(--green-dim)' : 'var(--red)' }}>
              {r.healthy ? <CheckCircle2 size={18} color="var(--green)" /> : <AlertTriangle size={18} color="var(--red)" />}
              <div>
                <div style={{ fontWeight: 600 }}>{r.healthy ? 'Everything agrees' : 'Something does not add up'}</div>
                <div className="inv-hint">
                  As of {r.as_of} · pool equity {fmtMoney(r.pool_equity)} ({r.pool_equity_source}) ·
                  NAV {fmtUnits(r.nav_per_unit)} · {fmtUnits(r.units_in_issue, 2)} units
                </div>
              </div>
            </div>

            <div className="card">
              <div className="table-wrapper">
                <table>
                  <thead><tr><th>Check</th><th className="num">Pool (raw)</th>
                    <th className="num">Investor-facing</th><th className="num">Difference</th></tr></thead>
                  <tbody>
                    {r.lines.map(l => (
                      <tr key={l.label}>
                        <td><div style={{ fontWeight: 600 }}>{l.label}</div><div className="inv-hint">{l.note}</div></td>
                        <td className="num">{fmtMoney(l.pool)}</td>
                        <td className="num">{fmtMoney(l.investor_facing)}</td>
                        <td className="num" style={{ color: isZero(l.difference) ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                          {fmtMoney(l.difference, { sign: true })}</td>
                      </tr>
                    ))}
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
