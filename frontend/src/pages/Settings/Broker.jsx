import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Server, Save, Loader2, Check, Wifi, WifiOff, Trash2, Shield, Eye, EyeOff, MessageSquare, RefreshCw, Search, AlertTriangle } from 'lucide-react';
import { getBrokerStatus, saveBrokerStandard, testBrokerConnection, removeBrokerStandard, testMt5Entry, testMt5Close, testMt5Breakeven, testMt5Trail, getConfig, updateConfig, getInstrumentResolution, getTelegramStatus, sendTelegramTest, getAccountState, resetAccountState } from '../../services/api';
import { useConnectionStore, useAuthStore } from '../../store';
import { invalidateAccountDependents } from '../../utils/invalidate';

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

function Mt5DiagnosticCard() {
  const [symbol, setSymbol] = useState('Crash 1000 Index');
  const [direction, setDirection] = useState('BUY');
  const [ticket, setTicket] = useState(null);
  const [loading, setLoading] = useState('');
  const [resultMsg, setResultMsg] = useState(null);

  const handleError = (e) => {
    setResultMsg({ type: 'error', text: e.response?.data?.detail || e.message || "Unknown error" });
  };

  const handleEntry = async () => {
    setLoading('entry');
    setResultMsg(null);
    try {
      const res = await testMt5Entry({ symbol, direction });
      if (res.data.success) {
        setTicket(res.data.ticket);
        setResultMsg({ type: 'success', text: `Opened ticket #${res.data.ticket}` });
      } else {
        setResultMsg({ type: 'error', text: res.data.error || "Failed to open position" });
      }
    } catch (e) { handleError(e); }
    setLoading('');
  };

  const handleClose = async () => {
    if (!ticket) return;
    setLoading('close');
    setResultMsg(null);
    try {
      const res = await testMt5Close({ ticket });
      if (res.data.success) {
        setTicket(null);
        setResultMsg({ type: 'success', text: `Closed ticket #${ticket}` });
      } else {
        setResultMsg({ type: 'error', text: res.data.error || "Failed to close position" });
      }
    } catch (e) { handleError(e); }
    setLoading('');
  };

  const handleBreakeven = async () => {
    if (!ticket) return;
    setLoading('breakeven');
    setResultMsg(null);
    try {
      const res = await testMt5Breakeven({ ticket });
      if (res.data.success) {
        setResultMsg({ type: 'success', text: `Breakeven set at ${res.data.new_sl}` });
      } else {
        setResultMsg({ type: 'error', text: res.data.error || "Failed to set breakeven" });
      }
    } catch (e) { handleError(e); }
    setLoading('');
  };

  const handleTrail = async () => {
    if (!ticket) return;
    setLoading('trail');
    setResultMsg(null);
    try {
      const res = await testMt5Trail({ ticket });
      if (res.data.success) {
        setResultMsg({ type: 'success', text: `Trail SL updated to ${res.data.new_sl}` });
      } else {
        setResultMsg({ type: 'error', text: res.data.error || "Failed to trail SL" });
      }
    } catch (e) { handleError(e); }
    setLoading('');
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title"><Server size={14} /> MT5 Manual Diagnostics</span>
      </div>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
        Test MT5 connection by manually triggering entries and modifying orders using your saved Risk %.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <div>
          <label>Symbol</label>
          <input type="text" value={symbol} onChange={e => setSymbol(e.target.value)} />
        </div>
        <div>
          <label>Direction</label>
          <select value={direction} onChange={e => setDirection(e.target.value)} style={{ width: '100%', padding: '8px 12px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: 'var(--radius-sm)' }}>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {!ticket ? (
          <button className="btn btn-primary" onClick={handleEntry} disabled={!!loading}>
            {loading === 'entry' ? <Loader2 size={14} className="spin" /> : 'Trigger Entry'}
          </button>
        ) : (
          <>
            <button className="btn btn-secondary" onClick={handleBreakeven} disabled={!!loading}>
              {loading === 'breakeven' ? <Loader2 size={14} className="spin" /> : 'Set Breakeven'}
            </button>
            <button className="btn btn-secondary" onClick={handleTrail} disabled={!!loading}>
              {loading === 'trail' ? <Loader2 size={14} className="spin" /> : 'Trail SL'}
            </button>
            <button className="btn btn-danger" onClick={handleClose} disabled={!!loading} style={{ background: 'var(--red)', color: 'white', border: 'none', padding: '8px 16px', borderRadius: 'var(--radius-sm)', cursor: 'pointer' }}>
              {loading === 'close' ? <Loader2 size={14} className="spin" /> : 'Close Position'}
            </button>
          </>
        )}
      </div>

      {resultMsg && (
        <div style={{ marginTop: 16, padding: 12, borderRadius: 4, background: resultMsg.type === 'error' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(34, 197, 94, 0.1)', color: resultMsg.type === 'error' ? 'var(--red)' : 'var(--green)', fontSize: '0.85rem' }}>
          {resultMsg.text}
        </div>
      )}
    </div>
  );
}

/**
 * Symbol resolution strip (Phase 14 Part C.3, task 14.9).
 *
 * Answers "will my config run on this broker?" before a data fetch fails.
 * A config names the canonical instrument (GER40); this shows what the connected
 * broker actually calls it (Germany 40 on Deriv), or why it cannot be traded here.
 */
function InstrumentResolutionCard() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [filter, setFilter] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ['instrument-resolution'],
    queryFn: () => getInstrumentResolution().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
    staleTime: 5 * 60 * 1000,
  });

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const res = await getInstrumentResolution(true);
      queryClient.setQueryData(['instrument-resolution'], res.data);
    } catch { /* the error branch below already covers a failed load */ }
    setRefreshing(false);
  };

  const rows = data?.instruments || [];
  const q = filter.trim().toLowerCase();
  const filtered = rows.filter(r => {
    if (!showAll && !r.available) return false;
    if (!q) return true;
    return r.canonical.toLowerCase().includes(q)
      || (r.broker_symbol || '').toLowerCase().includes(q);
  });

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title"><Search size={14} /> Symbol Resolution</span>
        {data?.connected && (
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {data.available_count}/{data.total_count} available · {data.broker}
          </span>
        )}
      </div>

      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
        Your configs name the <strong>canonical</strong> instrument. This is the symbol that name
        resolves to on the connected broker — so a config written for one broker runs unchanged on
        another. Instruments this broker does not list are shown with the reason instead of failing
        later at data fetch.
      </div>

      {isLoading && (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          <Loader2 size={14} className="spin" /> Enumerating broker symbols…
        </div>
      )}

      {error && (
        <div style={{ fontSize: '0.85rem', color: 'var(--red)' }}>
          Could not load symbol resolution: {error.response?.data?.detail || error.message}
        </div>
      )}

      {data && !data.connected && (
        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <WifiOff size={14} /> {data.message}
        </div>
      )}

      {data?.connected && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Filter instruments…"
              style={{ flex: '1 1 180px', minWidth: 0 }}
            />
            <button className="btn btn-secondary" onClick={() => setShowAll(!showAll)}>
              {showAll ? 'Available only' : 'Show unavailable'}
            </button>
            <button className="btn btn-secondary" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
              Rediscover
            </button>
          </div>

          <div style={{ maxHeight: 340, overflowY: 'auto', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                  <th style={{ padding: '6px 8px', position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Canonical</th>
                  <th style={{ padding: '6px 8px', position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Broker symbol</th>
                  <th style={{ padding: '6px 8px', position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Note</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(r => {
                  const differs = r.available && r.broker_symbol !== r.canonical;
                  return (
                    <tr key={r.canonical} style={{ borderTop: '1px solid var(--border)', opacity: r.available ? 1 : 0.55 }}>
                      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{r.canonical}</td>
                      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap', color: differs ? 'var(--green)' : 'var(--text-primary)' }}>
                        {r.broker_symbol || '—'}
                        {differs && <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>(renamed)</span>}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-muted)' }}>
                        {r.ambiguous_with?.length > 0 && !r.available && (
                          <AlertTriangle size={12} style={{ verticalAlign: -2, marginRight: 4, color: 'var(--red)' }} />
                        )}
                        {r.reason}
                      </td>
                    </tr>
                  );
                })}
                {filtered.length === 0 && (
                  <tr><td colSpan={3} style={{ padding: 12, color: 'var(--text-muted)' }}>No instruments match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Switching MT5 logins used to leave three kinds of state behind: the circuit
 * breaker's persisted daily/weekly P&L, the sync loop's "how far back to read
 * deal history" marker, and journal rows the bot had adopted from the broker.
 * The first two are now scoped to an account number and self-invalidate, but
 * the journal rows are the user's data and only the user can say whether to
 * keep them. This card shows exactly what state exists, whose account it
 * belongs to, and clears the parts you choose.
 */
function StateRow({ label, value, warn }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: '0.8rem' }}>
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{ color: warn ? 'var(--red)' : 'var(--text-primary)' }}>{value}</span>
    </div>
  );
}

function AccountStateCard() {
  const queryClient = useQueryClient();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [opts, setOpts] = useState({
    clear_risk_state: true,
    clear_adopted_trades: true,
    clear_all_trades: false,
    clear_signals: false,
  });

  const refresh = () => getAccountState().then(r => setState(r.data)).catch(() => {});
  useEffect(() => { refresh(); }, []);

  const stale =
    state?.circuit_breaker_state?.stale || state?.sync_state?.stale;

  const handleReset = async () => {
    const parts = [];
    if (opts.clear_risk_state) parts.push('risk/circuit-breaker state');
    if (opts.clear_all_trades) parts.push('ALL journal trades');
    else if (opts.clear_adopted_trades) parts.push('broker-adopted journal trades');
    if (opts.clear_signals) parts.push('all signals');
    if (!parts.length) { setMsg({ type: 'error', text: 'Nothing selected.' }); return; }
    if (!window.confirm(`This will permanently delete: ${parts.join(', ')}.

Continue?`)) return;

    setBusy(true); setMsg(null);
    try {
      const { data } = await resetAccountState(opts);
      setMsg({ type: 'success', text: `Reset for account #${data.live_login ?? '?'}: ` +
        Object.entries(data.removed).map(([k, v]) => `${k}=${v}`).join(', ') });
      // Journal, signals, stats and the dashboard all still hold the previous
      // account's rows until they are dropped.
      invalidateAccountDependents(queryClient);
      await refresh();
    } catch (e) {
      setMsg({ type: 'error', text: 'Reset failed: ' + (e.response?.data?.detail || e.message) });
    }
    setBusy(false);
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title"><RefreshCw size={14} /> Account State &amp; Reset</span>
        {state?.live_login && <span className="badge badge-green">#{state.live_login}</span>}
      </div>

      {stale && (
        <div style={{ marginBottom: 12, padding: 12, borderRadius: 4, background: 'rgba(239,68,68,0.1)', color: 'var(--red)', fontSize: '0.82rem', display: 'flex', gap: 8 }}>
          <AlertTriangle size={16} style={{ flexShrink: 0 }} />
          <span>
            Saved state belongs to a different MT5 account. It is being ignored, but
            clearing it removes the confusion for good.
          </span>
        </div>
      )}

      {state && (
        <div style={{ marginBottom: 16 }}>
          <StateRow label="Connected MT5 login" value={state.live_login ?? 'not connected'} />
          <StateRow
            label="Risk state (cb_state.json)"
            value={state.circuit_breaker_state.exists
              ? `account #${state.circuit_breaker_state.mt5_account ?? 'untagged'} · daily P&L ${(state.circuit_breaker_state.daily_pnl ?? 0).toFixed(2)}${state.circuit_breaker_state.is_paused ? ' · PAUSED' : ''}`
              : 'none'}
            warn={state.circuit_breaker_state.stale}
          />
          <StateRow
            label="Sync state (bot_sync_state.json)"
            value={state.sync_state.exists ? `account #${state.sync_state.mt5_account ?? 'untagged'}` : 'none'}
            warn={state.sync_state.stale}
          />
          <StateRow label="Journal trades" value={state.journal.trades_total} />
          <StateRow
            label="…of which adopted from the broker"
            value={state.journal.trades_adopted_from_broker}
            warn={state.journal.trades_adopted_from_broker > 0}
          />
          <StateRow label="Signals recorded" value={state.journal.signals_total} />
        </div>
      )}

      <div style={{ display: 'grid', gap: 6, marginBottom: 14, fontSize: '0.82rem' }}>
        {[
          ['clear_risk_state', 'Clear risk / circuit-breaker + sync state (recommended after switching accounts)'],
          ['clear_adopted_trades', 'Delete journal trades adopted from the broker (MANUAL / MANUAL_OFFLINE)'],
          ['clear_all_trades', "Delete ALL journal trades, including the bot's own"],
          ['clear_signals', 'Delete all recorded signals'],
        ].map(([key, label]) => (
          <label key={key} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={opts[key]}
              onChange={e => setOpts(o => ({ ...o, [key]: e.target.checked }))}
              style={{ marginTop: 3, width: 'auto' }}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>

      <button className="btn btn-danger" onClick={handleReset} disabled={busy}>
        {busy ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}
        {busy ? 'Resetting...' : 'Reset selected state'}
      </button>

      {msg && (
        <div style={{ marginTop: 14, padding: 12, borderRadius: 4, fontSize: '0.82rem', background: msg.type === 'error' ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)', color: msg.type === 'error' ? 'var(--red)' : 'var(--green)' }}>
          {msg.text}
        </div>
      )}
    </div>
  );
}

function TelegramSettingsCard() {
  const [token, setToken] = useState('');
  const [chatId, setChatId] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [tgStatus, setTgStatus] = useState(null);
  const [resultMsg, setResultMsg] = useState(null);

  useEffect(() => {
    getConfig().then(res => {
      if (res.data && res.data.config) {
        setToken(res.data.config.telegram_bot_token || '');
        setChatId(res.data.config.telegram_chat_id || '');
      }
    }).catch(() => {});
  }, []);

  const refreshStatus = () => getTelegramStatus().then(r => setTgStatus(r.data)).catch(() => {});

  useEffect(() => { refreshStatus(); }, []);

  const handleSave = async () => {
    setSaving(true);
    setResultMsg(null);
    try {
      await updateConfig({ config: { telegram_bot_token: token, telegram_chat_id: chatId } });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
      setResultMsg({ type: 'success', text: 'Telegram settings saved.' });
      await refreshStatus();
    } catch (e) {
      setResultMsg({ type: 'error', text: 'Failed to save: ' + (e.response?.data?.detail || e.message) });
    }
    setSaving(false);
  };

  const handleTest = async () => {
    setTesting(true);
    setResultMsg(null);
    try {
      const { data } = await sendTelegramTest();
      setTgStatus(data.status || null);
      if (data.ok) {
        setResultMsg({ type: 'success', text: 'Test message delivered. Check your Telegram.' });
      } else {
        const detail = (data.results || []).filter(r => !r.ok)
          .map(r => `chat ${r.chat_id}: ${r.detail || r.status}`).join(' | ');
        setResultMsg({ type: 'error', text: `Not delivered — ${data.reason || detail || 'unknown error'}` });
      }
    } catch (e) {
      setResultMsg({ type: 'error', text: 'Test failed: ' + (e.response?.data?.detail || e.message) });
    }
    setTesting(false);
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title"><MessageSquare size={14} /> Telegram Notifications</span>
      </div>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
        Receive instant alerts for signals, executed trades, and breakeven/trailing stops.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <div>
          <label>Bot Token</label>
          <input type="text" value={token} onChange={e => setToken(e.target.value)} placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" />
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 4 }}>Get from @BotFather</div>
        </div>
        <div>
          <label>Chat ID(s)</label>
          <input type="text" value={chatId} onChange={e => setChatId(e.target.value)} placeholder="878410133, 987654321" />
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 4 }}>Separate multiple IDs with commas</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 size={14} className="spin" /> : saved ? <Check size={14} /> : <Save size={14} />}
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Telegram Settings'}
        </button>
        {/* Saving the token proved nothing: send_message swallowed every
            failure into the server log, so a bad token, a chat the bot has
            never been started in, and a TLS failure all looked the same from
            here — nothing happens. This sends a real message and shows
            Telegram's own answer. */}
        <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
          {testing ? <Loader2 size={14} className="spin" /> : <MessageSquare size={14} />}
          {testing ? 'Sending...' : 'Send Test Message'}
        </button>
      </div>

      {tgStatus && (
        <div style={{ marginTop: 12, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
          <div>
            Service state:{' '}
            <strong style={{ color: tgStatus.configured ? 'var(--green)' : 'var(--red)' }}>
              {tgStatus.configured ? `loaded (${tgStatus.chat_id_count} chat id${tgStatus.chat_id_count === 1 ? '' : 's'})` : 'NOT loaded — no alerts will be sent'}
            </strong>
            {' · '}sent {tgStatus.sent_count} · failed {tgStatus.failed_count}
          </div>
          {tgStatus.last_error && (
            <div style={{ color: 'var(--red)', marginTop: 4 }}>
              Last error: {tgStatus.last_error}
            </div>
          )}
        </div>
      )}
      {resultMsg && (
        <div style={{ marginTop: 16, padding: 12, borderRadius: 4, background: resultMsg.type === 'error' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(34, 197, 94, 0.1)', color: resultMsg.type === 'error' ? 'var(--red)' : 'var(--green)', fontSize: '0.85rem' }}>
          {resultMsg.text}
        </div>
      )}
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

  const handleRemoveStandard = async () => {
    await removeBrokerStandard();
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
      
      <InstrumentResolutionCard />
      <AccountStateCard />
      <TelegramSettingsCard />
      <Mt5DiagnosticCard />
    </div>
  );
}
