import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api } from '../api';
import { Mark } from '../components/Brand';
import { useSubmit } from '../hooks';
import { Field, Problem } from '../components/ui';

/** Open an account. The email that follows confirms the address and sets the password. */
export default function Signup() {
  const [f, setF] = useState({ name: '', email: '', phone: '', country: '', consent: false, website: '' });
  const [done, setDone] = useState(null);
  // The affiliate code from the link they were sent (?ref=...). It is checked
  // up front so the cost of joining through it is shown BEFORE they agree to
  // anything: under one of the fund's arrangements a referred investor pays
  // their introducer out of their own profit, and that must not be a surprise
  // on their first statement.
  const ref = (new URLSearchParams(useLocation().search).get('ref') || '').trim();
  const [refInfo, setRefInfo] = useState(null);
  useEffect(() => {
    if (!ref) return;
    api.referralCheck(ref).then(setRefInfo).catch(() => setRefInfo({ valid: false }));
  }, [ref]);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const { busy, error, submit } = useSubmit(async () => {
    // A code that did not check out is still sent: the server records it, and
    // an admin can place the person by hand. Refusing the signup over a typo
    // in someone else's link would just lose the investor.
    const body = { ...f, phone: f.phone || null, country: f.country || null, ref: ref || null };
    setDone((await api.signup(body)).message);
  });

  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand"><Mark size={44} /><h1>ALPHAVANTIQ<em>CAPITAL</em></h1></div>
        {done ? (
          <>
            <p className="center">Check your email</p>
            <div className="ok-box">{done}</div>
            <p className="muted small center">The link works once. If it does not arrive in a few minutes,
              look in your spam folder, or sign up again to get a new one.</p>
          </>
        ) : (
          <>
            <p className="muted center">Open an investor account</p>
            <Field label="Full name">
              <input autoComplete="name" value={f.name} required minLength={2} maxLength={120} onChange={set('name')} />
            </Field>
            <Field label="Email" hint="We send a link here to confirm it and choose your password.">
              <input type="email" autoComplete="email" value={f.email} required maxLength={255} onChange={set('email')} />
            </Field>
            <Field label="Phone (optional)">
              <input type="tel" autoComplete="tel" value={f.phone} maxLength={40} onChange={set('phone')} />
            </Field>
            <Field label="Country (optional)">
              <input autoComplete="country-name" value={f.country} maxLength={80} onChange={set('country')} />
            </Field>
            {/* people never see this; bots fill it in */}
            <input type="text" name="website" value={f.website} onChange={set('website')} tabIndex={-1}
                   autoComplete="off" aria-hidden="true" style={{ position: 'absolute', left: '-9999px' }} />
            {ref && refInfo && (
              refInfo.valid ? (
                refInfo.disclosure && <div className="ok-box small">{refInfo.disclosure}</div>
              ) : (
                <div className="muted small">This referral link was not recognised. You can still
                  sign up; we will connect you to the right person if you tell us who sent it.</div>
              )
            )}
            <label className="check">
              <input type="checkbox" checked={f.consent} required onChange={set('consent')} />
              <span>I understand that investing involves risk, that I can lose money, and that past
                performance does not guarantee future results.</span>
            </label>
            <Problem error={error} />
            <button className="btn primary" disabled={busy || !f.consent}>{busy ? 'Sending…' : 'Create account'}</button>
          </>
        )}
        <p className="muted small center auth-switch">Already have an account? <Link to="/login">Sign in</Link></p>
      </form>
    </div>
  );
}
