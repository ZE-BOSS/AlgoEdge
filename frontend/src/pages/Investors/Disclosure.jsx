import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Eye, EyeOff, Pencil } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, QueryState, StateBadge } from './shared';
import { fmtMoney, isNeg, useInvAction } from './format';

const STATES = ['undisclosed', 'published', 'hidden'];

function Preview() {
  const q = useQuery({ queryKey: ['inv', 'disclosure-preview'], queryFn: inv.disclosurePreview });
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">What investors see</span>
        <span className="inv-hint">The exact response their trade list will receive</span>
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
  const q = useQuery({ queryKey: ['inv', 'disclosures', state], queryFn: () => inv.disclosures(state) });
  const publish = useInvAction(({ id, ...b }) => inv.publishTrade(id, b));
  const hide = useInvAction(({ id, ...b }) => inv.hideTrade(id, b));

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card inv-hint">
        Published trades carry symbol, side, date and result only. Strategy, parameters, entry logic,
        ticket and size are never stored on the published record, so they cannot leak through the
        investor API. Changing a figure before publishing needs a reason and is recorded as an
        adjustment beside the raw value.
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
                    <tr key={r.trade_id}>
                      <td style={{ whiteSpace: 'nowrap' }}>{r.raw.closed_on || '—'}</td>
                      <td>{r.raw.symbol}</td>
                      <td>{r.raw.direction}</td>
                      <td className="inv-hint">{r.raw.strategy_id} · {r.raw.exit_reason || ''}</td>
                      <td className="num" style={{ color: isNeg(r.raw.pnl) ? 'var(--red)' : 'var(--green)' }}>
                        {fmtMoney(r.raw.pnl, { sign: true })}</td>
                      <td>
                        {r.published ? (
                          <span>{r.published.symbol} {r.published.direction} {fmtMoney(r.published.result_amount, { sign: true })}
                            {r.edited && <> <span className="badge badge-yellow">edited</span></>}</span>
                        ) : <StateBadge state={r.state} />}
                      </td>
                      <td>
                        <div className="inv-actions">
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
