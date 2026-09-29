import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, setSession } from '../api';
import { Mark } from '../components/Brand';
import { useSubmit } from '../hooks';
import { Field, Problem } from '../components/ui';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const nav = useNavigate();
  const from = useLocation().state?.from || '/';
  const { busy, error, submit } = useSubmit(async () => {
    setSession(await api.login(email, password));
    nav(from, { replace: true });
  });
  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand"><Mark size={44} /><h1>ALPHAVANTIQ<em>CAPITAL</em></h1></div>
        <p className="muted center">Sign in to your investor account</p>
        <Field label="Email">
          <input type="email" autoComplete="username" value={email} required
                 onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <input type="password" autoComplete="current-password" value={password} required
                 onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Problem error={error} />
        <button className="btn primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="muted small center">
          No password yet? Use the invitation link we sent you. Forgotten it? Contact us and we
          will send you a new link.
        </p>
      </form>
    </div>
  );
}
