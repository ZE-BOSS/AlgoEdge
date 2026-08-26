import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles, Check, Trash2, Loader2, AlertTriangle, Cpu, ShieldCheck } from 'lucide-react';
import api, { getAnalysisModels } from '../../services/api';

/**
 * AI provider settings — where the Claude API key is entered.
 *
 * The key goes straight to the backend's encrypted store
 * (`services/api_key_store.py`, Fernet at rest) and is never returned by any
 * endpoint afterwards: this page can only ever learn *whether* a key exists,
 * not what it is. The browser never talks to Anthropic — your backend does.
 *
 * An environment variable still works and takes no UI step; when one is set,
 * it is reported here so "it works and I never entered it" is explainable
 * rather than mysterious.
 */

const PROVIDERS = [
  { id: 'anthropic', label: 'Anthropic (Claude)', env: 'ANTHROPIC_API_KEY', primary: true },
  { id: 'openai', label: 'OpenAI', env: 'OPENAI_API_KEY' },
  { id: 'gemini', label: 'Google Gemini', env: 'GEMINI_API_KEY' },
];

export default function AISettings() {
  const qc = useQueryClient();
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(null);

  const { data: keys } = useQuery({
    queryKey: ['ai-keys'],
    queryFn: () => api.get('/analysis/keys').then(r => r.data),
  });
  const { data: catalogue } = useQuery({
    queryKey: ['analysis-models'],
    queryFn: () => getAnalysisModels().then(r => r.data),
    staleTime: 5 * 60 * 1000,
  });

  const save = async (provider) => {
    const value = (drafts[provider] || '').trim();
    if (!value) return;
    setBusy(provider);
    setError(null);
    try {
      await api.post('/analysis/keys', { provider, api_key: value });
      // Drop the plaintext from component state the moment it is stored.
      setDrafts(d => ({ ...d, [provider]: '' }));
      setSaved(provider);
      setTimeout(() => setSaved(null), 2500);
      qc.invalidateQueries({ queryKey: ['ai-keys'] });
      qc.invalidateQueries({ queryKey: ['analysis-models'] });
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Could not store the key');
    } finally {
      setBusy(null);
    }
  };

  const remove = async (provider) => {
    setBusy(provider);
    try {
      await api.delete(`/analysis/keys/${provider}`);
      qc.invalidateQueries({ queryKey: ['ai-keys'] });
      qc.invalidateQueries({ queryKey: ['analysis-models'] });
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(null);
    }
  };

  const models = catalogue?.providers?.anthropic?.models || [];

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Sparkles size={16} style={{ color: 'var(--blue)' }} />
          <span style={{ fontWeight: 600 }}>AI providers</span>
        </div>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 0, marginBottom: 14 }}>
          Claude analyses backtests, portfolio runs, single trades, live trades, signals, logs,
          strategy configs and fundamentals data. It needs a key for that.
        </p>

        {error && (
          <div style={{ display: 'flex', gap: 6, padding: 10, marginBottom: 12, borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--red)', fontSize: '0.78rem' }}>
            <AlertTriangle size={14} style={{ flexShrink: 0 }} /> {error}
          </div>
        )}

        {PROVIDERS.map(p => {
          const state = keys?.[p.id];
          const active = state?.stored || state?.from_env;
          return (
            <div key={p.id} style={{ padding: '12px 0', borderTop: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: active ? 'var(--green)' : 'var(--text-muted)',
                }} />
                <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{p.label}</span>
                {p.primary && <span className="badge badge-blue" style={{ fontSize: '0.6rem' }}>primary</span>}
                {state?.from_env && (
                  <span className="badge badge-green" style={{ fontSize: '0.6rem' }}
                        title={`Set via the ${p.env} environment variable — no UI step needed`}>
                    from environment
                  </span>
                )}
                {state?.stored && (
                  <span className="badge badge-green" style={{ fontSize: '0.6rem' }}>stored &amp; encrypted</span>
                )}
                {saved === p.id && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: 'var(--green)', fontSize: '0.7rem' }}>
                    <Check size={12} /> saved
                  </span>
                )}
              </div>

              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  type="password"
                  className="input input-sm"
                  style={{ flex: 1, fontFamily: 'ui-monospace, monospace' }}
                  placeholder={state?.stored ? 'A key is stored — enter a new one to replace it' : `Paste your ${p.label} API key`}
                  value={drafts[p.id] || ''}
                  onChange={e => setDrafts(d => ({ ...d, [p.id]: e.target.value }))}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => save(p.id)}
                  disabled={busy === p.id || !(drafts[p.id] || '').trim()}
                >
                  {busy === p.id ? <Loader2 size={13} className="spin" /> : 'Save'}
                </button>
                {state?.stored && (
                  <button className="btn btn-secondary btn-sm" onClick={() => remove(p.id)} disabled={busy === p.id} title="Delete the stored key">
                    <Trash2 size={13} />
                  </button>
                )}
              </div>

              {!active && (
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 5 }}>
                  Alternatively set <code>{p.env}</code> in the backend environment and restart.
                </div>
              )}
            </div>
          );
        })}

        <div style={{ display: 'flex', gap: 6, marginTop: 14, padding: 10, borderRadius: 6, background: 'rgba(59,130,246,0.07)', fontSize: '0.7rem', color: 'var(--text-secondary)' }}>
          <ShieldCheck size={14} style={{ flexShrink: 0, color: 'var(--blue)', marginTop: 1 }} />
          <span>
            Keys are encrypted at rest with Fernet and are never sent back to the browser —
            this page can only see <em>whether</em> a key exists. Your backend calls Anthropic;
            the browser never does. Storing requires <code>ENCRYPTION_KEY</code> to be set in the
            backend environment.
          </span>
        </div>
      </div>

      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <Cpu size={15} style={{ color: 'var(--blue)' }} />
          <span style={{ fontWeight: 600 }}>Available models</span>
          <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
            selectable from the model picker on every Claude surface
          </span>
        </div>
        {models.length === 0 ? (
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>Loading…</div>
        ) : (
          <table style={{ width: '100%', fontSize: '0.74rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                <th style={{ padding: '4px 6px' }}>Model</th>
                <th style={{ padding: '4px 6px', textAlign: 'right' }}>Context</th>
                <th style={{ padding: '4px 6px', textAlign: 'right' }} title="The real ceiling — what 'uncapped' means for this model">Max output</th>
                <th style={{ padding: '4px 6px', textAlign: 'right' }}>$/1M in</th>
                <th style={{ padding: '4px 6px', textAlign: 'right' }}>$/1M out</th>
                <th style={{ padding: '4px 6px' }}>Thinking</th>
              </tr>
            </thead>
            <tbody>
              {models.map(m => (
                <tr key={m.id} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={{ padding: '4px 6px' }}>
                    <div style={{ fontWeight: 600 }}>{m.label}</div>
                    <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>{m.note}</div>
                  </td>
                  <td style={{ padding: '4px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {m.context ? `${Math.round(m.context / 1000)}K` : '—'}
                  </td>
                  <td style={{ padding: '4px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--green)' }}>
                    {Math.round(m.max_output / 1000)}K
                  </td>
                  <td style={{ padding: '4px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>${m.input_per_mtok}</td>
                  <td style={{ padding: '4px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>${m.output_per_mtok}</td>
                  <td style={{ padding: '4px 6px', color: m.supports_effort ? 'var(--green)' : 'var(--text-muted)' }}>
                    {m.supports_effort ? `adaptive · ${(catalogue?.effort_levels || []).join('/')}` : 'not supported'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 10 }}>
          Output ceilings are each model&apos;s real maximum, not a house cap. The picker&apos;s
          slider only lets you ask for <em>less</em> — an over-large request is rejected by the
          API rather than producing a longer answer.
        </div>
      </div>
    </div>
  );
}
