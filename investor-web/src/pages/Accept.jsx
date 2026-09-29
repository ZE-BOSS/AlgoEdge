import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, setSession } from '../api';
import { Mark } from '../components/Brand';
import { useSubmit } from '../hooks';
import { Field, Problem, PasswordInput } from '../components/ui';

/** Set a password from an invitation or reset link. The link works once. */
export default function Accept() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const nav = useNavigate();
  const mismatch = again && password !== again;
  const { busy, error, submit } = useSubmit(async () => {
    if (password !== again) throw new Error('The two passwords are different.');
    setSession(await api.accept(token, password));
    nav('/', { replace: true });
  });

  if (!token) {
    return (
      <div className="auth"><div className="auth-card">
        <p>This page needs the link from your invitation email. Open that link directly, or
          contact us for a new one.</p>
      </div></div>
    );
  }
  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand"><Mark size={44} /><h1>ALPHAVANTIQ<em>CAPITAL</em></h1></div>
        <p className="center">Choose a password for your account</p>
        <Field label="New password" hint="At least 10 characters. A short sentence works well.">
          <PasswordInput autoComplete="new-password" value={password} minLength={10} maxLength={72}
                 required onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="The same again">
          <PasswordInput autoComplete="new-password" value={again} required
                 aria-invalid={mismatch || undefined} onChange={(e) => setAgain(e.target.value)} />
        </Field>
        {mismatch && <p className="bad small">The two passwords are different.</p>}
        <Problem error={error} />
        <button className="btn primary" disabled={busy || mismatch}>{busy ? 'Saving…' : 'Set password and sign in'}</button>
      </form>
    </div>
  );
}
