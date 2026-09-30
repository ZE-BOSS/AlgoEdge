import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Eye, EyeOff, Pencil, Users } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState, StateBadge } from './shared';
import { fmtMoney, isNeg, useInvAction } from './format';

const STATES = ['undisclosed', 'published', 'hidden'];

/** Whether a published result has reached investors' balances. */
function BookingNote({ b }) {
  if (!b || b.booked === null) return <> <span className="badge badge-yellow">not in balances yet</span></>;
  if (b.in_valuation) return <> <span className="badge badge-blue" title="Closed before your latest valuation, which already includes it">in valuation</span></>;
  return <> <span className="badge badge-green" title={`Booked into the unit price on ${b.booked_on}`}>in balances</span></>;
}

/** How one published trade's result divides between the investors who held
 *  the fund when it closed. The lines add up to the published result exactly;
 *  each investor sees only their own line. */
function Split({ tradeId }) {
  const q = useQuery({ queryKey: ['inv', 'disclosure-split', tradeId], queryFn: () => inv.disclosureSplit(tradeId) });
  return (
    <QueryState q={q}>
      {(s) => s.lines.length === 0 ? (
        <div className="inv-hint">Nobody was invested at the start of {s.closed_on}, so nothing is allocated.</div>
      ) : (
        <div style={{ display: 'grid', gap: 8 }}>
          <div className="inv-hint">
            {s.symbol} {s.direction} closed {s.closed_on}: {fmtMoney(s.result_amount, { sign: true })}
            {s.result_pct ? ` (${s.result_pct}% of the pool)` : ''}, split between {s.investors} investor(s) by
            the fund they held at the end of the previous day. Allocated {fmtMoney(s.allocated, { sign: true })}.
          </div>
          <div className="inv-split">
            {s.lines.map((l) => (
              <div key={l.investor_id} className="inv-split-row">
                <Link to={`/investors/p/${l.investor_id}`}>{l.name || l.investor_id.slice(0, 8)}</Link>
                <span className="inv-split-track">
                  <span style={{ width: `${Math.min(100, Number(l.share_pct))}%`,
                                 background: isNeg(l.amount) ? 'var(--red)' : 'var(--green)' }} />
                </span>
                <span className="num">{Number(l.share_pct).toFixed(2)}%</span>
                <span className="num" style={{ color: isNeg(l.amount) ? 'var(--red)' : 'var(--green)' }}>
                  {fmtMoney(l.amount, { sign: true })}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </QueryState>
  );
}

function Preview() {
  const q = useQuery({ queryKey: ['inv', 'disclosure-preview'], queryFn: inv.disclosurePreview });
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Published trades, fund totals</span>
        <span className="inv-hint">Admin only. Each investor sees their own share of these, never the totals.</span>
      </div>
      <QueryState q={q}>
        {(rows) => rows.length === 0 ? <div className="inv-hint">Nothing published yet.</div> : (
          <div className="table-wrapper">
            <table>
              <thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th className="num">Result</th>
                <th className="num">% of pool</th><th>Note</th></tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{r.closed_on}</td><td>{r.symbol}</td><td>{r.direction}</td>
                    <td className="num" style={{ color: isNeg(r.result_amount) ? 'var(--red)' : 'var(--green)' }}>
                      {fmtMoney(r.result_amount, { sign: true })}</td>
                    <td className="num">{r.result_pct ? `${r.result_pct}%` : '—'}</td>
                    <td>{r.note || ''}</td>
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

export default function Disclosure() {
  const [state, setState] = useState('undisclosed');
  const [open, setOpen] = useState(null);
  const q = useQuery({ queryKey: ['inv', 'disclosures', state], queryFn: () => inv.disclosures(state) });
  const publish = useInvAction(({ id, ...b }) => inv.publishTrade(id, b));
  const hide = useInvAction(({ id, ...b }) => inv.hideTrade(id, b));

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card inv-hint">
        Published trades carry symbol, side, date and result only. Strategy, parameters, entry logic,
        ticket and size are never stored on the published record, so they cannot leak through the
        investor API. Changing a figure before publishing needs a reason and is recorded as an
        adjustment beside the raw value. Each investor sees only their own share of a published result,
        split by the fund they held when it closed; open "Investor split" on a published trade to see
        every line. Publishing sends invested investors a phone alert (market and % only).
        Publishing also books the result into today&rsquo;s unit price, so every holder&rsquo;s balance moves
        at once; editing the result books only the difference. A trade that closed before your latest
        entered valuation is not booked again, since that valuation already contains it. Hiding a
        published trade does not undo its booking: the money was still made or lost.
      </div>

      <div className="card">
        <div className="filter-tabs" style={{ marginBottom: 12 }}>
          {STATES.map(s => (
            <button key={s} className={`btn btn-sm ${state === s ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setState(s)}>{s}</button>
          ))}
        </div>
        <QueryState q={q}>
          {(rows) => rows.length === 0 ? <Empty>No {state} trades.</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Closed</th><th>Symbol</th><th>Side</th><th>Strategy (admin only)</th>
                  <th className="num">P&amp;L</th><th>Published as</th><th>Decide</th></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <Fragment key={r.trade_id}>
                    <tr>
                      <td style={{ whiteSpace: 'nowrap' }}>{r.raw.closed_on || '—'}</td>
                      <td>{r.raw.symbol}</td>
                      <td>{r.raw.direction}</td>
                      <td className="inv-hint">{r.raw.strategy_id} · {r.raw.exit_reason || ''}</td>
                      <td className="num" style={{ color: isNeg(r.raw.pnl) ? 'var(--red)' : 'var(--green)' }}>
                        {fmtMoney(r.raw.pnl, { sign: true })}</td>
                      <td>
                        {r.published ? (
                          <span>{r.published.symbol} {r.published.direction} {fmtMoney(r.published.result_amount, { sign: true })}
                            {r.edited && <> <span className="badge badge-yellow">edited</span></>}
                            <BookingNote b={r.booking} /></span>
                        ) : <StateBadge state={r.state} />}
                      </td>
                      <td>
                        <div className="inv-actions">
                          {r.state === 'published' && (
                            <button className="btn btn-sm btn-secondary"
                                    onClick={() => setOpen(open === r.trade_id ? null : r.trade_id)}>
                              <Users size={12} /> {open === r.trade_id ? 'Hide split' : 'Investor split'}
                            </button>
                          )}
                          {r.state !== 'published' && (
                            <ActionForm label="Publish" icon={Eye} tone="primary" compact
                                        onSubmit={() => publish.mutateAsync({ id: r.trade_id })} />
                          )}
                          <ActionForm label={r.state === 'published' ? 'Edit' : 'Edit & publish'} icon={Pencil} compact
                                      submitLabel="Publish"
                                      fields={[
                                        { name: 'symbol', label: 'Symbol', defaultValue: r.published?.symbol ?? r.raw.symbol },
                                        { name: 'direction', label: 'Side', defaultValue: r.published?.direction ?? r.raw.direction },
                                        { name: 'result_amount', label: 'Result (USD)', type: 'number',
                                          defaultValue: r.published?.result_amount ?? r.raw.pnl ?? '' },
                                        { name: 'note', label: 'Note to investors', defaultValue: r.published?.note ?? '' },
                                        { name: 'reason', label: 'Reason for any changed figure', type: 'textarea',
                                          hint: 'Required if symbol, side or result differ from the trade.' },
                                      ]}
                                      onSubmit={(b) => publish.mutateAsync({ id: r.trade_id, ...b })} />
                          {r.state !== 'hidden' && (
                            r.state === 'published' ? (
                              <ActionForm label="Withdraw" icon={EyeOff} tone="danger" compact
                                          fields={[{ name: 'reason', label: 'Why investors will no longer see it',
                                                     type: 'textarea', required: true }]}
                                          onSubmit={(b) => hide.mutateAsync({ id: r.trade_id, ...b })} />
                            ) : (
                              <ActionForm label="Hide" icon={EyeOff} compact
                                          onSubmit={() => hide.mutateAsync({ id: r.trade_id })} />
                            )
                          )}
                        </div>
                      </td>
                    </tr>
                    {open === r.trade_id && (
                      <tr><td colSpan={7} style={{ background: 'var(--bg-tertiary)' }}><Split tradeId={r.trade_id} /></td></tr>
                    )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </div>

      <Preview />
    </div>
  );
}
