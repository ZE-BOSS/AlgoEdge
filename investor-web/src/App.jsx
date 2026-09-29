import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { clearSession } from './api';
import { useSession } from './hooks';
import { Mark } from './components/Brand';
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
  { to: '/', label: 'Overview', icon: 'M3 12l9-8 9 8M5 10v10h14V10', end: true },
  { to: '/money', label: 'Money', icon: 'M12 3v18M17 7H9.5a3 3 0 000 6h5a3 3 0 010 6H6' },
  { to: '/activity', label: 'Activity', icon: 'M4 6h16M4 12h16M4 18h10' },
  { to: '/trades', label: 'Trades', icon: 'M3 17l6-6 4 4 8-8M15 7h6v6' },
  { to: '/account', label: 'Account', icon: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0' },
];

function Icon({ d }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function Shell({ session, children }) {
  return (
    <div className="shell">
      <header className="top">
        <div className="brand"><Mark size={26} /><span>ALPHAVANTIQ<em>CAPITAL</em></span></div>
        <nav className="top-nav" aria-label="Main">
          {TABS.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end}>{t.label}</NavLink>
          ))}
        </nav>
        <div className="who">
          <span className="muted">{session.investor?.name}</span>
          <button className="link" onClick={clearSession}>Sign out</button>
        </div>
      </header>
      <main className="page">{children}</main>
      <nav className="tabbar" aria-label="Main">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}><Icon d={t.icon} /><span>{t.label}</span></NavLink>
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
  );
}
