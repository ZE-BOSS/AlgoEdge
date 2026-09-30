import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { clearSession } from './api';
import { useLoad, useSession } from './hooks';
import { api } from './api';
import { PrefsProvider } from './prefs';
import { usePrefs } from './usePrefs';
import { greeting } from './format';
import { Mark } from './components/Brand';
import Icon from './components/Icons';
import Bell from './components/Bell';
import { disable as disableWebPush } from './webpush';
import Login from './pages/Login';
import Accept from './pages/Accept';
import Signup from './pages/Signup';
import Home from './pages/Home';
import Money from './pages/Money';
import Activity from './pages/Activity';
import Trades from './pages/Trades';
import How from './pages/How';
import Account from './pages/Account';

const TABS = [
  { to: '/', label: 'Overview', icon: 'home', end: true },
  { to: '/money', label: 'Money', icon: 'money' },
  { to: '/trades', label: 'Trades', icon: 'trades' },
  { to: '/activity', label: 'Activity', icon: 'activity' },
  { to: '/account', label: 'Settings', icon: 'settings' },
];

const TITLES = { '/': 'Overview', '/money': 'Money', '/trades': 'Trades', '/activity': 'Activity',
  '/account': 'Settings', '/how': 'How it is calculated' };

// this browser stops getting the account's notifications before the session goes
const signOut = () => disableWebPush().catch(() => {}).finally(clearSession);

function initials(name) {
  return String(name || '?').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
}

function PrivacyButton() {
  const { prefs, hidden, reveal, update } = usePrefs();
  // with "hide balances" saved, this reveals for the session; otherwise it hides
  const toggle = () => (prefs.hide_balances ? reveal() : update({ hide_balances: true }));
  return (
    <button className="icon-btn" onClick={toggle} aria-pressed={hidden}
            title={hidden ? 'Show amounts' : 'Hide amounts'} aria-label={hidden ? 'Show amounts' : 'Hide amounts'}>
      <Icon name={hidden ? 'eyeOff' : 'eye'} />
    </button>
  );
}

function Shell({ session, children }) {
  const loc = useLocation();
  const me = useLoad(api.me, 'shell');
  const name = me.data?.investor?.name || session.investor?.name;
  const title = TITLES[loc.pathname.replace(/\/(deposit|withdraw)$/, '').replace(/\/$/, '') || '/'] || '';
  return (
    <div className="shell">
      <aside className="side" aria-label="Main">
        <div className="side-brand"><Mark size={30} /><span>ALPHAVANTIQ<em>CAPITAL</em></span></div>
        <nav className="side-nav">
          {TABS.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end}><Icon name={t.icon} /><span>{t.label}</span></NavLink>
          ))}
          <NavLink to="/how"><Icon name="help" /><span>How it works</span></NavLink>
        </nav>
        <div className="side-foot">
          <div className="avatar" aria-hidden="true">{initials(name)}</div>
          <div className="who-name"><strong>{name}</strong><span className="muted small">Investor</span></div>
          <button className="icon-btn" onClick={signOut} title="Sign out" aria-label="Sign out"><Icon name="logout" /></button>
        </div>
      </aside>

      <div className="main">
        <header className="top">
          <div className="top-brand"><Mark size={26} /></div>
          <div className="top-title">
            <span className="muted small">{greeting(name)}</span>
            <h1>{title}</h1>
          </div>
          <div className="top-actions">
            <Bell />
            <PrivacyButton />
            <NavLink to="/account" className="avatar small-avatar" aria-label="Settings">{initials(name)}</NavLink>
          </div>
        </header>
        <main className="page">{children}</main>
      </div>

      <nav className="tabbar" aria-label="Main">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>
            <span className="tab-ic"><Icon name={t.icon} size={22} /></span>
            <span className="tab-label">{t.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function Private({ session, children }) {
  const loc = useLocation();
  if (!session?.access_token) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return <Shell session={session}>{children}</Shell>;
}

export default function App() {
  const session = useSession();
  const guard = (el) => <Private session={session}>{el}</Private>;
  return (
    <PrefsProvider signedIn={!!session?.access_token}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={session?.access_token ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/signup" element={session?.access_token ? <Navigate to="/" replace /> : <Signup />} />
          <Route path="/accept" element={<Accept />} />
          <Route path="/" element={guard(<Home />)} />
          <Route path="/money/*" element={guard(<Money />)} />
          <Route path="/activity" element={guard(<Activity />)} />
          <Route path="/trades" element={guard(<Trades />)} />
          <Route path="/how" element={guard(<How />)} />
          <Route path="/account" element={guard(<Account />)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </PrefsProvider>
  );
}
