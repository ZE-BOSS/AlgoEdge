import { NavLink, Routes, Route } from 'react-router-dom';
import { Settings, Sliders, Shield, Wifi, Server, Sparkles } from 'lucide-react';
import ConnectionSettings from './Connection';
import RiskSettings from './Risk';
import StrategySettings from './Strategy';
import BrokerSettings from './Broker';
import AISettings from './AI';

function SettingsNav() {
  const tabs = [
    { to: '/settings', label: 'Connection', icon: Wifi, end: true },
    { to: '/settings/broker', label: 'Broker', icon: Server },
    { to: '/settings/strategy', label: 'Strategy', icon: Sliders },
    { to: '/settings/risk', label: 'Risk', icon: Shield },
    { to: '/settings/ai', label: 'AI', icon: Sparkles },
  ];

  return (
    <div style={{ display: 'flex', gap: 4, marginBottom: 24, flexWrap: 'wrap' }}>
      {tabs.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className={({ isActive }) => `btn ${isActive ? 'btn-primary' : 'btn-secondary'} btn-sm`}>
          <Icon size={14} /> {label}
        </NavLink>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  return (
    <>
      <div className="page-header">
        <h2><Settings size={22} style={{ display: 'inline', marginRight: 8 }} />Settings</h2>
        <p>Configure backend connection, broker credentials, strategy parameters, risk management, and AI providers</p>
      </div>

      <SettingsNav />

      <Routes>
        <Route index element={<ConnectionSettings />} />
        <Route path="broker" element={<BrokerSettings />} />
        <Route path="strategy" element={<StrategySettings />} />
        <Route path="risk" element={<RiskSettings />} />
        <Route path="ai" element={<AISettings />} />
      </Routes>
    </>
  );
}
