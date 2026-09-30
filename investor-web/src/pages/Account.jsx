import { useEffect, useState } from 'react';
import { api, setSession } from '../api';
import { useLoad, useSubmit } from '../hooks';
import { Link } from 'react-router-dom';
import { Badge, Field, Loaded, Problem, PasswordInput } from '../components/ui';
import { RangeTabs } from '../components/Charts';
import Icon from '../components/Icons';
import { usePrefs } from '../usePrefs';
import { day, money } from '../format';
import * as webpush from '../webpush';

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
        <PasswordInput autoComplete="current-password" value={cur} required onChange={(e) => setCur(e.target.value)} />
      </Field>
      <Field label="New password" hint="At least 10 characters. Every other device is signed out.">
        <PasswordInput autoComplete="new-password" value={next} minLength={10} maxLength={72} required
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
      <p>We will pay out your balance as valued on the day we process it, to your account
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

function Toggle({ label, hint, checked, onChange, disabled }) {
  return (
    <label className="toggle-row">
      <span><strong>{label}</strong>{hint && <span className="muted small">{hint}</span>}</span>
      <input type="checkbox" role="switch" className="switch" checked={!!checked} disabled={disabled}
             onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function Section({ icon, title, children, aside }) {
  return (
    <section className="card settings-card">
      <header className="card-head">
        <h2 className="with-icon"><Icon name={icon} size={16} />{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

function Display() {
  const { prefs, update } = usePrefs();
  const [error, setError] = useState(null);
  const save = (c) => update(c).catch(setError);
  return (
    <Section icon="eye" title="Display">
      <Toggle label="Hide my balances" checked={prefs.hide_balances} onChange={(v) => save({ hide_balances: v })}
              hint="Amounts show as dots until you tap the eye at the top. Handy in public." />
      <Toggle label="Short numbers" checked={prefs.compact_numbers} onChange={(v) => save({ compact_numbers: v })}
              hint="$12.5k instead of $12,480.00 in tiles and lists." />
      <div className="toggle-row">
        <span><strong>Default chart range</strong><span className="muted small">What the balance chart opens on.</span></span>
        <RangeTabs value={prefs.chart_range} onChange={(v) => save({ chart_range: v })} />
      </div>
      <Problem error={error} />
    </Section>
  );
}

function ThisBrowser() {
  const [state, setState] = useState('checking');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => { webpush.status().then(setState).catch(() => setState('unsupported')); }, []);
  const flip = async (on) => {
    setBusy(true); setError(null);
    try { await (on ? webpush.enable() : webpush.disable()); setState(await webpush.status()); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  const note = {
    unsupported: 'This browser cannot show notifications. On iPhone, add this site to your home screen first.',
    blocked: 'Notifications are blocked for this site. Allow them in your browser settings, then come back.',
  }[state];
  return (
    <div className="toggle-row">
      <span><strong>Notifications in this browser</strong>
        <span className="muted small">{note || 'Pop-ups on this computer or phone browser, even with the tab closed.'}</span></span>
      {!note && state !== 'checking' && (
        <input type="checkbox" role="switch" className="switch" checked={state === 'on'} disabled={busy}
               aria-label="Notifications in this browser" onChange={(e) => flip(e.target.checked)} />
      )}
      <Problem error={error} />
    </div>
  );
}

// [key, label, hint] — the same list drives the push and email columns
const EVENTS = [
  ['live_trades', 'A trade opens', 'Market and side, the moment the fund opens a position.'],
  ['trades', 'A trade result', 'Profit or loss when a trade is published; your share is in the app and email.'],
  ['money', 'Money in', 'Your transfer noted and received.'],
  ['withdrawals', 'Withdrawals', 'Requested, approved, paid or declined.'],
  ['fees', 'Fees charged', 'Every two months, what came off your balance.'],
  ['statements', 'Statements ready', 'Your monthly PDF.'],
];
// emails for these always go: they are the record of what happened to your money
const EMAIL_ALWAYS = new Set(['money', 'withdrawals']);

function Notifications() {
  const { prefs, update } = usePrefs();
  const [error, setError] = useState(null);
  const save = (c) => update(c).catch(setError);
  return (
    <Section icon="bell" title="Notifications">
      <p className="muted small">Every notification also lands under the bell at the top, whatever you choose
        here. Password and payout-account changes are always sent, for your safety.</p>
      <ThisBrowser />
      <div className="notif-grid" role="table" aria-label="What you are told about, and how">
        <div className="notif-row head" role="row"><span role="columnheader">Tell me when</span>
          <span role="columnheader">Push</span><span role="columnheader">Email</span></div>
        {EVENTS.map(([k, label, hint]) => (
          <div className="notif-row" role="row" key={k}>
            <span role="cell"><strong>{label}</strong><span className="muted small">{hint}</span></span>
            <span role="cell"><input type="checkbox" role="switch" className="switch" aria-label={`${label}: push`}
              checked={!!prefs.push[k]} onChange={(e) => save({ push: { [k]: e.target.checked } })} /></span>
            {EMAIL_ALWAYS.has(k) ? <span role="cell" className="muted tiny">always</span> : (
              <span role="cell"><input type="checkbox" role="switch" className="switch" aria-label={`${label}: email`}
                checked={!!prefs.email[k]} onChange={(e) => save({ email: { [k]: e.target.checked } })} /></span>
            )}
          </div>
        ))}
      </div>
      <p className="muted small">Push goes to this browser (if turned on above) and to the Android app.</p>
      <Problem error={error} />
    </Section>
  );
}

export default function Account() {
  const me = useLoad(api.me);
  return (
    <Loaded q={me}>
      {({ investor: i, terms: t }) => (
        <div className="settings">
          <section className="card profile-card">
            <div className="avatar big-avatar" aria-hidden="true">
              {String(i.name || '?').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('')}
            </div>
            <div>
              <h2>{i.name}</h2>
              <span className="muted">{i.email}</span>
              <div className="row small muted">
                <span>Member since {day(i.joined)}</span>
                <Badge tone={i.status === 'active' ? 'good' : 'neutral'}>{i.status}</Badge>
              </div>
            </div>
          </section>

          <div className="settings-grid">
            <Section icon="user" title="Your details">
              <dl className="pairs">
                <dt>Name</dt><dd>{i.name}</dd>
                <dt>Email</dt><dd>{i.email}</dd>
                <dt>Phone</dt><dd>{i.phone || '-'}</dd>
                <dt>Country</dt><dd>{i.country || '-'}</dd>
              </dl>
              <p className="muted small">To change these, email us from the address above.</p>
            </Section>

            <Display />
            <Notifications />

            <Section icon="bank" title="Where we pay you">
              {i.payout.account_number ? (
                <dl className="pairs">
                  <dt>Bank</dt><dd>{i.payout.bank_name}</dd>
                  <dt>Account</dt><dd className="num">****{i.payout.account_number.slice(-4)}</dd>
                  <dt>Name</dt><dd>{i.payout.account_name}</dd>
                </dl>
              ) : <p className="muted">No account on file yet.</p>}
              <p className="muted small">To protect you, the account we pay can only be changed by contacting us
                directly, never from the app. If someone got into your account, they still could not redirect
                your money.</p>
            </Section>

            <Section icon="doc" title="Your terms" aside={<Link to="/how" className="small">How it works</Link>}>
              <dl className="pairs">
                <dt>Performance fee</dt><dd>{t.performance_fee_pct}% of new profit</dd>
                <dt>Management fee</dt><dd>{t.management_fee_pct}% of each two-month period&rsquo;s profit</dd>
                <dt>Minimum first deposit</dt><dd>{money(t.min_investment)}</dd>
                <dt>Lock-up</dt><dd>{t.lockup_days} days</dd>
                <dt>Withdrawal notice</dt><dd>{t.notice_days} days</dd>
              </dl>
            </Section>

            <Section icon="shield" title="Password"><ChangePassword /></Section>
            <Section icon="close" title="Close your account"><CloseAccount status={i.status} onDone={me.reload} /></Section>
          </div>
        </div>
      )}
    </Loaded>
  );
}
