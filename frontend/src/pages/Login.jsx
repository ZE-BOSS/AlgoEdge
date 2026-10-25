import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogIn, Eye, EyeOff, ShieldCheck, UserPlus } from 'lucide-react';
import { Mark } from '../components/Brand';
import { useAuthStore } from '../store';
import { login as apiLogin, register as apiRegister } from '../services/api';

// Operator accounts are not self-service: the server closes sign-up once the
// first account exists (ALLOW_REGISTRATION). The one exception is a brand-new
// server with no accounts at all - its first account becomes the admin, and
// without a way to create it here nobody could ever sign in (2026-10-22, the
// move to a new VPS). The server refuses this once any account exists.
export default function Login() {
  const [setup, setSetup] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const authLogin = useAuthStore((s) => s.login);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = setup
        ? await apiRegister({ name: name.trim(), email: email.trim(), password })
        : await apiLogin({ email: email.trim(), password });
      const { access_token, refresh_token, user } = res.data;
      authLogin(access_token, refresh_token, user);
      navigate('/');
    } catch (err) {
      const detail = err.response?.data?.detail;
      const msg = (Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : detail)
        || err.message || 'Something went wrong';
      setError(msg === 'Network Error'
        ? 'Cannot reach the server. Check Settings → Connection points at https://api.alphavantiqcapital.com.'
        : msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <Mark size={46} />
          <div className="login-wordmark">ALPHAVANTIQ <span>CAPITAL</span></div>
          <div className="login-badge"><ShieldCheck size={13} /> Admin console</div>
          <p className="login-subtitle">
            {setup ? 'Create the first account on this server - it becomes the administrator'
                   : 'Sign in to manage the fund, investors and trading'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="login-form">
          {setup && (
            <div className="form-group">
              <label htmlFor="name">Name</label>
              <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)}
                     required autoComplete="name" maxLength={100} />
            </div>
          )}
          <div className="form-group">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                   required autoComplete="username" autoFocus />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <div className="password-input">
              <input id="password" type={showPassword ? 'text' : 'password'} value={password}
                     onChange={(e) => setPassword(e.target.value)} required minLength={setup ? 8 : undefined}
                     autoComplete={setup ? 'new-password' : 'current-password'}
                     autoCapitalize="off" spellCheck={false} />
              <button type="button" className="password-toggle" onClick={() => setShowPassword(!showPassword)}
                      aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword}>
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>

          {error && <div className="login-error" role="alert">{error}</div>}

          <button type="submit" className="btn login-btn" disabled={loading}>
            {loading ? <span className="spinner" />
              : setup ? <><UserPlus size={16} /> Create admin account</> : <><LogIn size={16} /> Sign in</>}
          </button>
        </form>

        <p className="login-footer">
          <button type="button" className="link-button" onClick={() => { setSetup(!setup); setError(''); }}>
            {setup ? 'Back to sign in' : 'New server with no accounts yet? Create the first one'}
          </button>
        </p>

        <p className="login-footer">
          Accounts are created by an administrator. Investors sign in at{' '}
          <a href="https://app.alphavantiqcapital.com">app.alphavantiqcapital.com</a>.
        </p>
      </div>
    </div>
  );
}
