import { useState } from 'react';
import { Wifi, WifiOff, Check, RefreshCw } from 'lucide-react';
import { setBackendUrl, getBackendUrl, checkHealth } from '../../services/api';
import { useConnectionStore } from '../../store';

export default function ConnectionSettings() {
  const { status } = useConnectionStore();
  const [url, setUrl] = useState(getBackendUrl());
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setBackendUrl(url);
      const res = await checkHealth();
      if (res.data?.status === 'ok') {
        setTestResult({ ok: true, msg: `Connected! Service: ${res.data.service} v${res.data.version}` });
      }
    } catch (e) {
      setTestResult({ ok: false, msg: `Connection failed: ${e.message}` });
    }
    setTesting(false);
  };

  const handleSave = () => {
    setBackendUrl(url);
    window.location.reload();
  };

  return (
    <div className="card" style={{ maxWidth: 600 }}>
      <div className="card-header">
        <span className="card-title">Backend Connection</span>
        <div className={`connection-status ${status === 'ONLINE' ? 'online' : 'offline'}`}>
          <span className="dot" />
          {status === 'ONLINE' ? 'Connected' : 'Disconnected'}
        </div>
      </div>

      <div style={{ display: 'grid', gap: 20 }}>
        <div>
          <label>Backend URL</label>
          <input type="text" value={url} onChange={e => setUrl(e.target.value)} placeholder="http://192.168.1.100:8000" />
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 4 }}>
            Your local backend IP. Use ngrok URL for remote access.
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
            <RefreshCw size={14} className={testing ? 'spinning' : ''} />
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
          <button className="btn btn-primary" onClick={handleSave}>
            <Check size={14} /> Save & Reconnect
          </button>
        </div>

        {testResult && (
          <div className={`connection-status ${testResult.ok ? 'online' : 'offline'}`} style={{ padding: 12 }}>
            {testResult.ok ? <Wifi size={14} /> : <WifiOff size={14} />}
            {testResult.msg}
          </div>
        )}

        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.8 }}>
            <strong>Setup Guide:</strong><br />
            1. Start backend on Windows: <code>uvicorn backend.main:app --host 0.0.0.0 --port 8000</code><br />
            2. For local access, use your PC's IP (e.g., <code>http://192.168.1.100:8000</code>)<br />
            3. For remote access, use ngrok: <code>ngrok http 8000</code> and paste the https URL above
          </div>
        </div>
      </div>
    </div>
  );
}
