import { useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { api } from '../api';
import { useLoad, useSubmit } from '../hooks';
import { Card, Copy, Field, Loaded, Problem } from '../components/ui';
import { cleanAmount, day, money } from '../format';

function AddMoney() {
  const info = useLoad(api.instructions);
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [done, setDone] = useState(null);
  const clean = cleanAmount(amount);
  const { busy, error, submit } = useSubmit(async () => {
    setDone(await api.claimDeposit(clean, note));
    setAmount(''); setNote('');
  });
  return (
    <Loaded q={info}>
      {(i) => (
        <div className="stack">
          <Card title="1 · Send a bank transfer">
            {!i.configured ? (
              <p className="muted">Our bank details are not set up here yet. Contact us and we will send
                them to you directly.</p>
            ) : (
              <dl className="pairs">
                <dt>Bank</dt><dd>{i.bank_name}</dd>
                <dt>Account number</dt><dd className="num">{i.account_number} <Copy text={i.account_number} /></dd>
                <dt>Account name</dt><dd>{i.account_name}</dd>
                <dt>Reference</dt><dd className="num strong">{i.reference_code} <Copy text={i.reference_code} /></dd>
              </dl>
            )}
            <p className="muted small">
              Always put <strong>{i.reference_code}</strong> as the reference — it is how we match the
              transfer to you. {i.instructions}
            </p>
          </Card>

          <Card title="2 · Tell us you have sent it">
            {done ? (
              <div className="ok-box">
                <strong>Thank you — we are looking out for {money(done.amount_claimed)}.</strong>
                <p>When it arrives we confirm the amount that actually landed and buy your units at
                  that day&rsquo;s price. You will see it under Activity.</p>
                <button className="link" onClick={() => setDone(null)}>Tell us about another transfer</button>
              </div>
            ) : (
              <form onSubmit={submit} className="form">
                <Field label={`Amount sent (${i.currency})`} hint={`Minimum ${money(i.min_investment)}.`}>
                  <input inputMode="decimal" value={amount} placeholder="1,000"
                         onChange={(e) => setAmount(e.target.value)} />
                </Field>
                <Field label="Anything we should know (optional)" hint="e.g. which bank it came from">
                  <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
                </Field>
                <Problem error={error} />
                <button className="btn primary" disabled={busy || !clean}>{busy ? 'Sending…' : 'I have sent it'}</button>
                <p className="muted small">Nothing is added to your account until we see the money arrive.</p>
              </form>
            )}
          </Card>
        </div>
      )}
    </Loaded>
  );
}

function Withdraw() {
  const me = useLoad(api.me);
  const [amount, setAmount] = useState('');
  const [debounced, setDebounced] = useState('');
  const [reason, setReason] = useState('');
  const [done, setDone] = useState(null);
  const clean = cleanAmount(amount);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(clean || ''), 300);
    return () => clearTimeout(t);
  }, [clean]);
  const quote = useLoad(() => api.quote(debounced || undefined), debounced);
  const q = quote.data;
  // The quote in hand is for the amount on screen (not one still being typed).
  // Numbers only for this equality test; nothing displayed comes from them.
  const live = !!(q && clean && clean === debounced && Number(q.amount) === Number(clean));
  const needsReason = !!(clean && live && q.needs_reason);
  const tooMuch = !!(clean && live && q.exceeds_holding);

  const { busy, error, submit } = useSubmit(async () => {
    if (needsReason && !reason.trim()) throw new Error('Tell us what the money is for.');
    setDone(await api.withdraw(clean, needsReason ? reason : null));
    setAmount(''); setReason('');
    quote.reload();
  });

  return (
    <Loaded q={me}>
      {({ investor, terms }) => (
        <div className="stack">
          <Card title="What you can take out">
            <Loaded q={quote}>
              {(qq) => (
                <>
                  <dl className="pairs">
                    <dt>Your holding today</dt><dd className="num">{money(qq.holding_value)}</dd>
                    <dt>Standard limit this month</dt><dd className="num">{money(qq.standard_limit)}</dd>
                  </dl>
                  <p className="muted small">
                    The standard limit is {qq.cap_pct}% of what your holding has earned this month
                    ({money(qq.month_profit)}). {qq.explanation.startsWith('no profit') &&
                      'Nothing has been earned yet this month, so any withdrawal is reviewed by us first.'}
                    {qq.in_lockup && ` Your money is in its lock-up period until ${day(qq.lockup_until)}, so a withdrawal before then is reviewed by us first.`}
                    {qq.cooling_off && ' The account we pay you was changed in the last 48 hours, so for your protection every withdrawal is reviewed by us first.'}
                  </p>
                </>
              )}
            </Loaded>
          </Card>

          <Card title="Request a withdrawal">
            {investor.status === 'closing' ? (
              <p className="muted">Your account is being closed. The closing payment covers everything.</p>
            ) : !investor.payout.account_number ? (
              <p className="muted">We do not have a bank account on file to pay you. Contact us to add one —
                for your protection it cannot be changed from the app.</p>
            ) : done ? (
              <div className="ok-box">
                <strong>Request received for {money(done.amount_requested)}.</strong>
                <p>{done.is_exception
                  ? 'Because it is above the standard limit, we review it first and will be in touch.'
                  : `We will pay it within ${terms.notice_days} days.`}</p>
                <button className="link" onClick={() => setDone(null)}>Make another request</button>
              </div>
            ) : (
              <form onSubmit={submit} className="form">
                <Field label="Amount (USD)">
                  <input inputMode="decimal" value={amount} placeholder="0.00"
                         onChange={(e) => setAmount(e.target.value)} />
                </Field>
                {tooMuch && <p className="bad small">That is more than your holding is worth today.</p>}
                {needsReason && !tooMuch && (
                  <Field label="What is it for?"
                         hint="This amount needs our review first. A short reason helps us decide quickly.">
                    <textarea rows={3} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} />
                  </Field>
                )}
                <p className="muted small">
                  Paid to {investor.payout.bank_name} ····{investor.payout.account_number.slice(-4)}
                  {' '}({investor.payout.account_name}). Standard requests are paid within
                  {' '}{terms.notice_days} days.
                </p>
                <Problem error={error} />
                <button className="btn primary" disabled={busy || !clean || tooMuch}>
                  {busy ? 'Sending…' : needsReason ? 'Send for review' : 'Request withdrawal'}
                </button>
              </form>
            )}
          </Card>
        </div>
      )}
    </Loaded>
  );
}

export default function Money() {
  return (
    <div className="stack">
      <div className="segmented" role="tablist">
        <NavLink to="/money" end>Add money</NavLink>
        <NavLink to="/money/withdraw">Withdraw</NavLink>
      </div>
      <Routes>
        <Route index element={<AddMoney />} />
        <Route path="withdraw" element={<Withdraw />} />
      </Routes>
    </div>
  );
}
