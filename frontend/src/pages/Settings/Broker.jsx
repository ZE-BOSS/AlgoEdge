import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Server, Save, Loader2, Check, Wifi, WifiOff, Trash2, Shield, Eye, EyeOff } from 'lucide-react';
import { getBrokerStatus, saveBrokerStandard, saveBrokerDeriv, testBrokerConnection, removeBrokerStandard, removeBrokerDeriv } from '../../services/api';
import { useConnectionStore, useAuthStore } from '../../store';

function BrokerCard({ title, description, type, brokerStatus, onSave, onTest, onRemove }) {
  const [form, setForm] = useState({ account: '', password: '', server: '', path: '' });
  const [showPassword, setShowPassword] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const isConfigured = brokerStatus?.configured;

  useEffect(() => {
    if (brokerStatus?.server) {
      setForm(prev => ({ ...prev, server: brokerStatus.server || '', path: brokerStatus.path || '' }));
    }
  }, [brokerStatus]);

  const handleTest = async () => {
    if (!form.account || !form.password || !form.server) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testBrokerConnection({
        account: parseInt(form.account),
        password: form.password,
        server: form.server,
        path: form.path,
      });
      setTestResult(res.data);
    } catch (e) {
      setTestResult({ connected: false, message: e.response?.data?.detail || e.message });
    }
    setTesting(false);
  };

  const handleSave = async () => {
    if (!form.account || !form.password || !form.server) return;
    setSaving(true);
    try {
      await onSave({
        account: parseInt(form.account),
        password: form.password,
        server: form.server,
        path: form.path,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setTestResult({ connected: false, message: 'Save failed: ' + (e.response?.data?.detail || e.message) });
    }
    setSaving(false);
  };

  const handleRemove = async () => {
    if (!window.confirm(`Remove ${title} credentials? This cannot be undone.`)) return;
    try {
      await onRemove();
      setForm({ account: '', password: '', server: '', path: '' });
      setTestResult(null);
    } catch (e) {
      setTestResult({ connected: false, message: 'Remove failed: ' + e.message });
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title"><Server size={14} /> {title}</span>
        <div className={`connection-status ${isConfigured ? 'online' : 'offline'}`} style={{ fontSize: '0.75rem' }}>
          <span className="dot" />
          {isConfigured ? `Connected — ${brokerStatus.account_masked}` : 'Not configured'}
        </div>
      </div>

      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
        {description}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <label>Account Number</label>
          <input
            type="number"
            value={form.account}
            onChange={e => setForm({ ...form, account: e.target.value })}
            placeholder="12345678"
          />
        </div>
        <div>
          <label>Server</label>
          <input
            type="text"
            value={form.server}
            onChange={e => setForm({ ...form, server: e.target.value })}
            placeholder={type === 'deriv' ? 'Deriv-Server' : 'Exness-MT5Real6'}
          />
        </div>
        <div style={{ position: 'relative' }}>
          <label>Password</label>
          <div style={{ position: 'relative' }}>
            <input
              type={showPassword ? 'text' : 'password'}
              value={form.password}
              onChange={e => setForm({ ...form, password: e.target.value })}
              placeholder="MT5 trading password"
              style={{ paddingRight: 36 }}
            />
            <button
              onClick={() => setShowPassword(!showPassword)}
              style={{
                position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--text-muted)', padding: 4,
              }}
            >
              {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
        </div>
        <div>
          <label>MT5 Path <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>(optional)</span></label>
          <input
            type="text"
            value={form.path}
            onChange={e => setForm({ ...form, path: e.target.value })}
            placeholder="C:\Program Files\MT5\terminal64.exe"
          />
        </div>
      </div>

      {/* Test result */}
      {testResult && (
        <div
          className={`connection-status ${testResult.connected ? 'online' : 'offline'}`}
          style={{ padding: 12, marginTop: 16, borderRadius: 'var(--radius-xs)' }}
        >
          {testResult.connected ? <Wifi size={14} /> : <WifiOff size={14} />}
          <div style={{ flex: 1 }}>
            <div>{testResult.message}</div>
            {testResult.account_info && (
              <div style={{ fontSize: '0.75rem', marginTop: 4, opacity: 0.8 }}>
                Balance: ${testResult.account_info.balance?.toLocaleString()} |
                Equity: ${testResult.account_info.equity?.toLocaleString()} |
                Leverage: 1:{testResult.account_info.leverage} |
                {testResult.account_info.company}
              </div>
            )}
            {testResult.mock_mode && (
              <div style={{ fontSize: '0.75rem', marginTop: 4, opacity: 0.7 }}>
                MT5 not installed on this machine. Connection will work on the Windows PC running the backend.
              </div>
            )}
          </div>
        </div>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button
          className="btn btn-secondary"
          onClick={handleTest}
          disabled={testing || !form.account || !form.password || !form.server}
        >
          {testing ? <Loader2 size={14} className="spin" /> : <Wifi size={14} />}
          {testing ? 'Testing...' : 'Test Connection'}
        </button>
        <button
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || !form.account || !form.password || !form.server}
        >
          {saving ? <Loader2 size={14} className="spin" /> : saved ? <Check size={14} /> : <Save size={14} />}
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Credentials'}
        </button>
        {isConfigured && (
          <button className="btn btn-secondary" onClick={handleRemove} style={{ marginLeft: 'auto' }}>
            <Trash2 size={14} /> Remove
          </button>
        )}
      </div>
    </div>
  );
}

export default function BrokerSettings() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();

  const { data: brokerData } = useQuery({
    queryKey: ['broker-status'],
    queryFn: () => getBrokerStatus().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
    refetchInterval: 30000,
  });

  const handleSaveStandard = async (data) => {
    await saveBrokerStandard(data);
    queryClient.invalidateQueries({ queryKey: ['broker-status'] });
  };

  const handleSaveDeriv = async (data) => {
    await saveBrokerDeriv(data);
    queryClient.invalidateQueries({ queryKey: ['broker-status'] });
  };

  const handleRemoveStandard = async () => {
    await removeBrokerStandard();
    queryClient.invalidateQueries({ queryKey: ['broker-status'] });
  };

  const handleRemoveDeriv = async () => {
    await removeBrokerDeriv();
    queryClient.invalidateQueries({ queryKey: ['broker-status'] });
  };

  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 800 }}>
      <BrokerCard
        title="Standard Broker"
        description="For forex pairs (EURUSD, GBPUSD), commodities (XAUUSD), and indices (US30). Supports Exness, IC Markets, and other standard MT5 brokers."
        type="standard"
        brokerStatus={brokerData?.standard}
        onSave={handleSaveStandard}
        onTest={testBrokerConnection}
        onRemove={handleRemoveStandard}
      />

      <BrokerCard
        title="Deriv Broker (Synthetics)"
        description="For synthetic indices — Volatility 75, Boom/Crash, Jump indices. Requires a separate Deriv MT5 account."
        type="deriv"
        brokerStatus={brokerData?.deriv}
        onSave={handleSaveDeriv}
        onTest={testBrokerConnection}
        onRemove={handleRemoveDeriv}
      />

      <div style={{
        padding: 16,
        background: 'var(--bg-tertiary)',
        borderRadius: 'var(--radius-sm)',
        fontSize: '0.8rem',
        color: 'var(--text-secondary)',
        display: 'flex',
        gap: 8,
        alignItems: 'flex-start',
      }}>
        <Shield size={16} style={{ flexShrink: 0, marginTop: 2 }} />
        <div>
          <strong>Security:</strong> Passwords are encrypted with AES-256 (Fernet) before storage.
          They are never stored in plaintext. The backend must run on the same Windows machine as
          your MT5 terminal for live trading to work.
        </div>
      </div>
    </div>
  );
}
