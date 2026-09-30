import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, download } from '../api';
import { useLoad } from '../hooks';
import { Amount, Badge, Card, Loaded, Problem, Stat } from '../components/ui';
import Icon from '../components/Icons';
import { STATE_LABEL, day, isNeg, money } from '../format';

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
          <ul className="doc-list">
            {rows.slice(0, 24).map((r) => (
              <li key={r.label}>
                <Icon name="doc" />
                <strong>{MONTHS[r.month - 1]} {r.year}</strong>
                <button className="btn small" onClick={() => download(`/statements/${r.year}-${r.month}.pdf`,
                  `alphavantiq-statement-${r.label}.pdf`).catch(setError)}>PDF</button>
              </li>
            ))}
          </ul>
        )}
      </Loaded>
      <Problem error={error} />
    </Card>
  );
}

/** Everything that has happened to the money, newest first, in one list. */
function events({ ledger, deposits, withdrawals }) {
  const out = [];
  for (const d of deposits) {
    out.push({
      key: `d${d.id}`, type: 'in', icon: 'arrowIn', when: d.priced_on || d.created_at,
      title: d.state === 'confirmed' ? 'Money received' : d.state === 'rejected' ? 'Transfer not received' : 'Transfer on its way',
      amount: d.amount_confirmed || d.amount_claimed, sign: 1, state: d.state,
      detail: d.amount_confirmed && d.amount_claimed && d.amount_confirmed !== d.amount_claimed
        ? `You told us ${money(d.amount_claimed)}; ${money(d.amount_confirmed)} arrived and was invested.`
        : d.state === 'confirmed' ? 'Invested the day it arrived.' : d.reason,
    });
  }
  for (const w of withdrawals) {
    out.push({
      key: `w${w.id}`, type: 'out', icon: 'arrowOut', when: w.paid_at || w.created_at,
      title: w.state === 'paid' ? 'Withdrawal paid' : w.state === 'declined' ? 'Withdrawal declined' : 'Withdrawal requested',
      amount: w.amount_paid || w.amount_requested, sign: -1, state: w.state,
      detail: [w.expected_by && `Expected by ${day(w.expected_by)}`, w.payment_reference && `Ref ${w.payment_reference}`,
        w.reason].filter(Boolean).join(' · '),
    });
  }
  for (const t of ledger) {
    if (t.kind === 'FEE') {
      out.push({ key: `l${t.id}`, type: 'fee', icon: 'fee', when: t.date, title: 'Fee charged',
        amount: t.amount, sign: -1, detail: 'Management and performance fees for the period, taken from your balance.' });
    } else if (t.kind === 'CORRECTION') {
      out.push({ key: `l${t.id}`, type: 'fix', icon: 'fix', when: t.date, title: 'Correction',
        amount: t.amount, sign: isNeg(t.units) ? -1 : 1, detail: 'An adjustment to your balance by the fund.' });
    }
  }
  return out.sort((a, b) => String(b.when).localeCompare(String(a.when)));
}

const KINDS = [['all', 'All'], ['in', 'Money in'], ['out', 'Money out'], ['fee', 'Fees']];

export default function Activity() {
  const q = useLoad(api.activity);
  const [kind, setKind] = useState('all');
  return (
    <Loaded q={q}>
      {(data) => <Timeline data={data} kind={kind} setKind={setKind} />}
    </Loaded>
  );
}

function Timeline({ data, kind, setKind }) {
  const all = useMemo(() => events(data), [data]);
  const shown = all.filter((e) => kind === 'all' || e.type === kind);
  const sum = (type, states) => all.filter((e) => e.type === type && (!states || states.includes(e.state)))
    .reduce((s, e) => s + Number(e.amount || 0), 0).toFixed(2);
  return (
    <div className="stack">
      <div className="stat-grid">
        <Stat label="Money received" icon="arrowIn"><Amount v={sum('in', ['confirmed'])} /></Stat>
        <Stat label="Paid out to you" icon="arrowOut"><Amount v={sum('out', ['paid'])} /></Stat>
        <Stat label="Fees charged" icon="fee"><Amount v={sum('fee')} /></Stat>
        <Stat label="Waiting" icon="activity" note="transfers and withdrawals in progress">
          {all.filter((e) => ['claimed_sent', 'requested', 'approved', 'exception_pending'].includes(e.state)).length}</Stat>
      </div>
      <div className="two-col wide-left">
        <Card title="Everything that has happened">
          <div className="chips">
            {KINDS.map(([k, label]) => (
              <button key={k} className={`chip ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>{label}</button>
            ))}
          </div>
          {all.length === 0 ? (
            <div className="empty">
              <Icon name="activity" size={28} />
              <strong>Nothing yet</strong>
              <p className="muted">Your transfers, withdrawals and fees appear here as they happen, each with
                its status. Start by <Link to="/money">adding money</Link>.</p>
            </div>
          ) : shown.length === 0 ? <p className="muted">Nothing of this kind yet.</p> : (
            <ol className="timeline">
              {shown.map((e) => (
                <li key={e.key} className={`ev ev-${e.type}`}>
                  <span className="ev-dot"><Icon name={e.icon} size={16} /></span>
                  <div className="ev-body">
                    <div className="ev-top">
                      <strong>{e.title}</strong>
                      <Amount v={`${e.sign < 0 ? '-' : ''}${e.amount}`} sign
                              className={e.sign < 0 ? 'bad' : 'good'} />
                    </div>
                    <div className="ev-meta">
                      <span className="muted small">{day(e.when)}</span>
                      {e.state && <Badge tone={TONE[e.state] || 'neutral'}>{STATE_LABEL[e.state] || e.state}</Badge>}
                    </div>
                    {e.detail && <span className="small muted">{e.detail}</span>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
        <Statements />
      </div>
    </div>
  );
}
