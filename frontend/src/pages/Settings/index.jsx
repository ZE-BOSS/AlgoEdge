import { NavLink, Routes, Route } from 'react-router-dom';
import { Settings, Sliders, Shield, Layers, Bot, Bell, Wifi } from 'lucide-react';
import ConnectionSettings from './Connection';
import RiskSettings from './Risk';
import StrategySettings from './Strategy';

function SettingsNav() {
  const tabs = [
    { to: '/settings', label: 'Connection', icon: Wifi, end: true },
    { to: '/settings/strategy', label: 'Strategy', icon: Sliders },
    { to: '/settings/risk', label: 'Risk', icon: Shield },
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
        <p>Configure backend connection, strategy parameters, and risk management</p>
      </div>

      <SettingsNav />

      <Routes>
        <Route index element={<ConnectionSettings />} />
        <Route path="strategy" element={<StrategySettings />} />
        <Route path="risk" element={<RiskSettings />} />
      </Routes>
    </>
  );
}
