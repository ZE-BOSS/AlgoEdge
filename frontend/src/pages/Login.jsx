import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogIn, Eye, EyeOff, ShieldCheck } from 'lucide-react';
import { Mark } from '../components/Brand';
import { useAuthStore } from '../store';
import { login as apiLogin } from '../services/api';

// Operator accounts are not self-service: the server closes sign-up once the
// first account exists (ALLOW_REGISTRATION), so this page only signs in.
export default function Login() {
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
      const res = await apiLogin({ email: email.trim(), password });
      const { access_token, refresh_token, user } = res.data;
      authLogin(access_token, refresh_token, user);
      navigate('/');
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Something went wrong';
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
          <p className="login-subtitle">Sign in to manage the fund, investors and trading</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                   required autoComplete="username" autoFocus />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <div className="password-input">
              <input id="password" type={showPassword ? 'text' : 'password'} value={password}
                     onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password"
                     autoCapitalize="off" spellCheck={false} />
              <button type="button" className="password-toggle" onClick={() => setShowPassword(!showPassword)}
                      aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword}>
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>

          {error && <div className="login-error" role="alert">{error}</div>}

          <button type="submit" className="btn login-btn" disabled={loading}>
            {loading ? <span className="spinner" /> : <><LogIn size={16} /> Sign in</>}
          </button>
        </form>

        <p className="login-footer">
          Accounts are created by an administrator. Investors sign in at{' '}
          <a href="https://app.alphavantiqcapital.com">app.alphavantiqcapital.com</a>.
        </p>
      </div>
    </div>
  );
}
