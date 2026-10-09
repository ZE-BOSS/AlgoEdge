import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, ArrowDownLeft, ArrowUpRight, CheckCircle2, Coins, RefreshCw, Wallet,
} from 'lucide-react';
import { cash } from '../services/api';

/**
 * What the ACCOUNT did, next to what the bot did.
 *
 * The journal has always shown trades. It has never shown deposits,
 * withdrawals, the prop firm's allocation, commission, swap, or whether any of
 * it adds up — so a profitable-looking journal could sit beside a balance that
 * said otherwise and nothing connected the two.
 *
 * Three things this screen refuses to do, each for a measured reason:
 *
 *   It does not fold costs into profit. On FundedNext that is $459.98 of
 *   commission; the old code counted it as a DEPOSIT.
 *
 *   It does not hide a zero residual. A check that only speaks up when it is
 *   unhappy is one people stop believing, and a silent zero is
 *   indistinguishable from a check that did not run.
 *
 *   It does not call the books healthy while anything is unclassified. A
 *   ledger that balances because an unexplained deal happens to net to zero is
 *   lucky, not reconciled.
 */

const money = (v, { sign = false } = {}) => {
  const n = Number(v ?? 0);
  const body = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  const prefix = n < 0 ? '−' : (sign && n > 0 ? '+' : '');
  return `${prefix}$${body}`;
};

const tone = (v) => (Number(v ?? 0) < 0 ? 'var(--red)'
  : Number(v ?? 0) > 0 ? 'var(--text-accent)' : 'var(--text-secondary)');

function Stat({ icon: Icon, label, value, hint, colour }) {
  return (
    <div className="metric-card">
      <div className="metric-label">
        <Icon size={12} style={{ marginRight: 4, display: 'inline' }} />{label}
      </div>
      <div className="metric-value" style={colour ? { color: colour } : undefined}>{value}</div>
      {hint && <div className="inv-hint" style={{ fontSize: '0.7rem' }}>{hint}</div>}
    </div>
  );
}

function Reconciliation({ login }) {
  const q = useQuery({
    queryKey: ['cash', 'reconciliation', login],
    queryFn: () => cash.reconciliation(login).then((r) => r.data),
    enabled: !!login,
  });
  const r = q.data;
  if (!r) return null;

  const ok = r.healthy;
  return (
    <div className="card" style={{ borderColor: ok ? 'var(--green-dim, #1b7a60)' : 'var(--warn, #d29922)' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        {ok ? <CheckCircle2 size={18} color="var(--text-accent)" />
            : <AlertTriangle size={18} color="var(--warn, #d29922)" />}
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600 }}>
            {ok ? 'The ledger matches the broker' : 'The ledger does not close yet'}
          </div>
          <div className="inv-hint">
            {r.anchored_at
              ? <>From {String(r.anchored_at).slice(0, 10)} · {r.anchor_note}</>
              : 'No opening anchor — sync this account to set one from the first bot trade'}
          </div>
          {(r.blocked_by || []).length > 0 && (
            <ul className="inv-hint" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {r.blocked_by.map((b) => <li key={b}>{b}</li>)}
            </ul>
          )}
        </div>
      </div>

      <table style={{ width: '100%', marginTop: 12 }}>
        <tbody>
          {[
            ['Opening balance', r.opening_balance, r.anchor_source],
            ['Capital in', r.capital_in],
            ['Capital out', r.capital_out],
            ['Trading result', r.trade_result],
            ['Costs (commission, swap, interest, tax)', r.costs],
            ['Adjustments', r.adjustments],
            ...(r.unclassified_count
              ? [[`Unclassified (${r.unclassified_count} awaiting review)`, r.unclassified_amount]]
              : []),
          ].map(([label, value, note]) => (
            <tr key={label}>
              <td style={{ padding: '4px 0', color: 'var(--text-secondary)' }}>
                {label}{note ? <span className="inv-hint"> · {note}</span> : null}
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{money(value)}</td>
            </tr>
          ))}
          <tr style={{ borderTop: '1px solid var(--border)' }}>
            <td style={{ padding: '8px 0 4px', fontWeight: 600 }}>Expected balance</td>
            <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {money(r.expected_balance)}</td>
          </tr>
          <tr>
            <td style={{ padding: '2px 0', fontWeight: 600 }}>
              Actual balance <span className="inv-hint">· {r.balance_source}</span></td>
            <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {r.actual_balance === null ? '—' : money(r.actual_balance)}</td>
          </tr>
          {/* Shown even when it is zero. That is the point: a residual that
              only appears when it is wrong is one nobody trusts when it is right. */}
          <tr>
            <td style={{ padding: '2px 0', fontWeight: 600 }}>Residual</td>
            <td style={{
              textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 600,
              color: r.residual === undefined || Math.abs(Number(r.residual)) >= 0.01
                ? 'var(--warn, #d29922)' : 'var(--text-accent)',
            }}>
              {r.residual === undefined ? '—' : money(r.residual, { sign: true })}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function ReviewQueue({ login }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ['cash', 'review', login],
    queryFn: () => cash.review(login).then((r) => r.data),
    enabled: !!login,
  });
  const fix = useMutation({
    mutationFn: ({ id, flow_class }) => cash.reclassify(id, { flow_class }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cash'] }),
  });
  const rows = q.data?.flows || [];
  if (!rows.length) return null;

  return (
    <div className="card" style={{ borderColor: 'var(--warn, #d29922)' }}>
      <h4 style={{ marginTop: 0 }}>
        <AlertTriangle size={14} style={{ display: 'inline', marginRight: 6, verticalAlign: -2 }} />
        {rows.length} deal{rows.length === 1 ? '' : 's'} the classifier would not guess at
      </h4>
      <p className="inv-hint">
        A broker correction can be capital or a cost, and only you know which. These
        block the reconciliation from reporting healthy until they are placed —
        a ledger that balances while something in it is unexplained is lucky, not
        reconciled.
      </p>
      <div className="table-wrapper">
        <table>
          <thead><tr>
            <th>When</th><th>Type</th><th>Comment</th>
            <th className="num">Amount</th><th>Place it as</th>
          </tr></thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.id}>
                <td className="inv-hint">{String(f.time || '').slice(0, 16).replace('T', ' ')}</td>
                <td>{f.type_name}</td>
                <td className="inv-hint">{f.comment || '—'}</td>
                <td className="num" style={{ color: tone(f.amount) }}>{money(f.amount, { sign: true })}</td>
                <td>
                  <select defaultValue="" disabled={fix.isPending}
                          onChange={(e) => e.target.value
                            && fix.mutate({ id: f.id, flow_class: e.target.value })}>
                    <option value="">choose…</option>
                    <option value="CAPITAL_IN">Money in</option>
                    <option value="CAPITAL_OUT">Money out</option>
                    <option value="COST">A cost</option>
                    <option value="TRADE_RESULT">A trading result</option>
                    <option value="ADJUSTMENT">A broker adjustment</option>
                    <option value="CREDIT_NOT_CAPITAL">Credit (not withdrawable)</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Periods({ login }) {
  const [grain, setGrain] = useState('day');
  const q = useQuery({
    queryKey: ['cash', 'periods', login, grain],
    queryFn: () => cash.periods(login, grain).then((r) => r.data),
    enabled: !!login,
  });
  const rows = [...(q.data?.periods || [])].reverse().slice(0, 40);
  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h4 style={{ margin: 0 }}>Per period</h4>
        <div style={{ display: 'flex', gap: 4 }}>
          {['day', 'week', 'month'].map((g) => (
            <button key={g} className={`btn btn-sm ${grain === g ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setGrain(g)}>{g}</button>
          ))}
        </div>
      </div>
      <p className="inv-hint">
        Boundaries follow the accounting offset the risk engine uses
        {q.data ? ` (UTC${q.data.accounting_offset_hours >= 0 ? '+' : ''}${q.data.accounting_offset_hours})` : ''},
        so a day here is the same day the circuit breaker halts on.
      </p>
      <div className="table-wrapper">
        <table>
          <thead><tr>
            <th>Period</th><th className="num">Capital in</th><th className="num">Capital out</th>
            <th className="num">Trading</th><th className="num">Costs</th><th className="num">Deals</th>
          </tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.period}>
                <td>{p.period}</td>
                <td className="num">{money(p.capital_in)}</td>
                <td className="num">{money(p.capital_out)}</td>
                <td className="num" style={{ color: tone(p.trade_result) }}>
                  {money(p.trade_result, { sign: true })}</td>
                <td className="num" style={{ color: tone(p.costs) }}>{money(p.costs)}</td>
                <td className="num inv-hint">{p.deals}</td>
              </tr>
            ))}
            {!rows.length && (
              <tr><td colSpan={6} className="inv-hint">
                Nothing yet — sync this account to read its deal history.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function CashLedger() {
  const qc = useQueryClient();
  const accounts = useQuery({ queryKey: ['cash', 'accounts'],
    queryFn: () => cash.accounts().then((r) => r.data) });
  const list = accounts.data?.accounts || [];
  const [login, setLogin] = useState(null);
  const active = login ?? list[0]?.login ?? null;

  const summary = useQuery({
    queryKey: ['cash', 'summary', active],
    queryFn: () => cash.summary(active).then((r) => r.data),
    enabled: !!active,
  });

  const sync = useMutation({
    mutationFn: () => cash.sync(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cash'] }),
  });

  const s = summary.data;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong>Account</strong>
          {list.length ? (
            <select value={active ?? ''} onChange={(e) => setLogin(Number(e.target.value))}>
              {list.map((a) => (
                <option key={a.login} value={a.login}>
                  {a.label || a.login} · {a.login}{a.holds_investor_capital ? ' · investor capital' : ''}
                </option>
              ))}
            </select>
          ) : <span className="inv-hint">None known yet — sync to add the attached terminal.</span>}
          <button className="btn btn-secondary btn-sm" disabled={sync.isPending}
                  onClick={() => sync.mutate()}>
            <RefreshCw size={13} /> {sync.isPending ? 'Reading…' : 'Sync from MT5'}
          </button>
          {sync.isError && (
            <span style={{ color: 'var(--red)', fontSize: '0.78rem' }}>
              {sync.error?.response?.data?.detail || 'Sync failed'}
            </span>
          )}
          {sync.data?.data && (
            <span className="inv-hint">
              {sync.data.data.added} new deal(s)
              {sync.data.data.unknown ? ` · ${sync.data.data.unknown} need review` : ''}
            </span>
          )}
        </div>
        <p className="inv-hint" style={{ marginTop: 8 }}>
          Syncing is idempotent — the broker's own deal ticket is the key, so pressing
          this twice adds nothing. It refuses if MT5 is attached to a different account
          than the one selected, rather than writing one account's deals into another's
          ledger.
        </p>
      </div>

      {s && (
        <>
          <div className="metrics-grid">
            <Stat icon={ArrowDownLeft} label="Capital in" value={money(s.capital.in)} />
            <Stat icon={ArrowUpRight} label="Capital out" value={money(s.capital.out)} />
            <Stat icon={Wallet} label="Net capital" value={money(s.capital.net)}
                  hint="deposits less withdrawals" />
            <Stat icon={Coins} label="Trading, net" value={money(s.profit.net, { sign: true })}
                  colour={tone(s.profit.net)} hint="after the costs below" />
          </div>
          <div className="metrics-grid">
            <Stat icon={Coins} label="Gross profit" value={money(s.profit.gross_profit)}
                  colour="var(--text-accent)" />
            <Stat icon={Coins} label="Gross loss" value={money(s.profit.gross_loss)}
                  colour="var(--red)" />
            {/* Separated on purpose: the old code counted commission and swap
                as DEPOSITS, which moved the prop-firm drawdown baseline. */}
            <Stat icon={Coins} label="Commission" value={money(s.costs.commission)}
                  colour={tone(s.costs.commission)} hint="not a deposit" />
            <Stat icon={Coins} label="Swap" value={money(s.costs.swap)}
                  colour={tone(s.costs.swap)} hint="not a deposit" />
          </div>
        </>
      )}

      {active && <Reconciliation login={active} />}
      {active && <ReviewQueue login={active} />}
      {active && <Periods login={active} />}
    </div>
  );
}
