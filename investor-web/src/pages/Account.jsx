import { useState } from 'react';
import { api, setSession } from '../api';
import { useLoad, useSubmit } from '../hooks';
import { Card, Field, Loaded, Problem } from '../components/ui';
import { day, money } from '../format';

function ChangePassword() {
  const [cur, setCur] = useState('');
  const [next, setNext] = useState('');
  const [ok, setOk] = useState(false);
  const { busy, error, submit } = useSubmit(async () => {
    setSession(await api.changePassword(cur, next));
    setCur(''); setNext(''); setOk(true);
  });
  return (
    <form className="form" onSubmit={submit}>
      <Field label="Current password">
        <input type="password" autoComplete="current-password" value={cur} required onChange={(e) => setCur(e.target.value)} />
      </Field>
      <Field label="New password" hint="At least 10 characters. Every other device is signed out.">
        <input type="password" autoComplete="new-password" value={next} minLength={10} maxLength={72} required
               onChange={(e) => { setNext(e.target.value); setOk(false); }} />
      </Field>
      <Problem error={error} />
      {ok && <p className="good small">Password changed. Other devices have been signed out.</p>}
      <button className="btn" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
    </form>
  );
}

function CloseAccount({ status, onDone }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [quote, setQuote] = useState(null);
  const { busy, error, submit } = useSubmit(async () => {
    const r = await api.requestClosure(reason);
    setQuote(r.quote);
    onDone();
  });
  if (status === 'closing' || quote) {
    return (
      <p className="muted">You have asked to close your account{quote && `; your holding is worth ${money(quote.net_payable)} today`}.
        We pay it to your account on file, then close the account and delete your personal details.
        To change your mind, contact us before we pay.</p>
    );
  }
  if (!open) return <button className="btn danger-outline" onClick={() => setOpen(true)}>Close my account</button>;
  return (
    <form className="form" onSubmit={submit}>
      <p>We will sell all your units at the price on the day we process it, pay the money to your account
        on file, and then delete your personal details. Your transaction history is kept by the fund
        in anonymous form, as the law requires.</p>
      <Field label="Anything you would like to tell us (optional)">
        <textarea rows={2} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <Problem error={error} />
      <div className="row">
        <button className="btn danger" disabled={busy}>{busy ? 'Sending…' : 'Yes, close my account'}</button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>Keep my account</button>
      </div>
    </form>
  );
}

export default function Account() {
  const me = useLoad(api.me);
  return (
    <Loaded q={me}>
      {({ investor: i }) => (
        <div className="stack">
          <Card title="Your details">
            <dl className="pairs">
              <dt>Name</dt><dd>{i.name}</dd>
              <dt>Email</dt><dd>{i.email}</dd>
              <dt>Phone</dt><dd>{i.phone || '—'}</dd>
              <dt>Member since</dt><dd>{day(i.joined)}</dd>
            </dl>
          </Card>
          <Card title="Where we pay you">
            {i.payout.account_number ? (
              <dl className="pairs">
                <dt>Bank</dt><dd>{i.payout.bank_name}</dd>
                <dt>Account</dt><dd className="num">····{i.payout.account_number.slice(-4)}</dd>
                <dt>Name</dt><dd>{i.payout.account_name}</dd>
              </dl>
            ) : <p className="muted">No account on file yet.</p>}
            <p className="muted small">To protect you, the account we pay can only be changed by
              contacting us directly — never from the app. If someone got into your account, they still
              could not redirect your money.</p>
          </Card>
          <Card title="Password"><ChangePassword /></Card>
          <Card title="Close your account"><CloseAccount status={i.status} onDone={me.reload} /></Card>
        </div>
      )}
    </Loaded>
  );
}
