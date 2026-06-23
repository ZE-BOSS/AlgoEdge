import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  LayoutDashboard, BookOpen, FlaskConical, BarChart3,
  Zap, Settings, Wifi, WifiOff, Loader2, LogOut
} from 'lucide-react';
import { useBackendConnection } from './hooks/useBackendConnection';
import { useConnectionStore, useAuthStore } from './store';
import { ErrorBoundary } from './components/ErrorBoundary';
import Login from './pages/Login';
import './index.css';

// Code-split heavy pages (PERF-4 fix)
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Journal = lazy(() => import('./pages/Journal'));
const Backtester = lazy(() => import('./pages/Backtester'));
const Analytics = lazy(() => import('./pages/Analytics'));
const Signals = lazy(() => import('./pages/Signals'));
const SettingsPage = lazy(() => import('./pages/Settings/index'));

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30000, retry: 1 } },
});

function PageLoader() {
  return (
    <div className="page-loader">
      <Loader2 className="spin" size={32} />
      <span>Loading...</span>
    </div>
  );
}

function AuthGuard({ children }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return children;
}

function Sidebar() {
  const { status } = useConnectionStore();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const links = [
    { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/journal', icon: BookOpen, label: 'Journal' },
    { to: '/backtester', icon: FlaskConical, label: 'Backtester' },
    { to: '/analytics', icon: BarChart3, label: 'Analytics' },
    { to: '/signals', icon: Zap, label: 'Signals' },
    { to: '/settings', icon: Settings, label: 'Settings' },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="dot" />
        <h1>AlgoEdge</h1>
      </div>
      <nav className="sidebar-nav">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
            <Icon />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        {user && (
          <div className="sidebar-user">
            <span className="user-email">{user.email}</span>
            <button className="logout-btn" onClick={logout} title="Sign Out">
              <LogOut size={14} />
            </button>
          </div>
        )}
        <div className={`connection-status ${status === 'ONLINE' ? 'online' : 'offline'}`}>
          <span className={`status-dot ${status === 'ONLINE' ? 'pulse-green' : 'pulse-red'}`} />
          {status === 'ONLINE' ? <><Wifi size={14} /> Connected</> : <><WifiOff size={14} /> Offline</>}
        </div>
      </div>
    </aside>
  );
}

function AppContent() {
  useBackendConnection();

  return (
    <div className="app-layout">
      <Sidebar />
      <main className="main-content">
        <ErrorBoundary>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/journal" element={<Journal />} />
              <Route path="/backtester" element={<Backtester />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/signals" element={<Signals />} />
              <Route path="/settings/*" element={<SettingsPage />} />
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/*" element={
            <AuthGuard>
              <AppContent />
            </AuthGuard>
          } />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
