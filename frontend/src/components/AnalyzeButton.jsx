import { useState } from 'react';
import { Sparkles, Loader2, X, Copy, Check } from 'lucide-react';
import { runAnalysis } from '../services/api';
import ModelPicker from './ModelPicker';
import { useModelSelection } from '../hooks/useModelSelection';

/**
 * "Analyse with Claude" — one button, usable against any analysis target.
 *
 * Dropped into the Backtester, Journal, Signals, Logs, Strategy Lab,
 * Fundamentals and the trade chart. Every surface gets the same model picker
 * and the same result panel, so there is one place to fix behaviour rather
 * than six near-copies.
 *
 * `payload` is for targets whose context is not in the database — a strategy's
 * unsaved form values, a fundamentals panel's fetched data. Sending it means
 * the analysis sees exactly what you are looking at rather than a re-fetch that
 * may have moved.
 */
export default function AnalyzeButton({
  targetType,
  targetId = null,
  payload = null,
  label = 'Analyse with Claude',
  question = '',
  compact = false,
  disabled = false,
  onComplete,
}) {
  const selection = useModelSelection();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [q, setQ] = useState(question);
  const [copied, setCopied] = useState(false);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await runAnalysis({
        target_type: targetType,
        target_id: targetId,
        question: q || null,
        payload,
        save: true,
        ...selection.requestFields(),
      });
      setResult(res.data);
      onComplete?.(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Analysis failed');
    } finally {
      setBusy(false);
    }
  };

  const copy = () => {
    navigator.clipboard?.writeText(result?.analysis || '').then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };

  return (
    <>
      <button
        type="button"
        className={`btn ${compact ? 'btn-secondary btn-sm' : 'btn-primary'}`}
        onClick={() => { setOpen(true); setResult(null); setError(null); }}
        disabled={disabled}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}
      >
        <Sparkles size={compact ? 13 : 15} />
        {compact ? 'Claude' : label}
      </button>

      {open && (
        <div
          onClick={e => { if (e.target === e.currentTarget) setOpen(false); }}
          style={{
            position: 'fixed', inset: 0, zIndex: 500, background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
          }}
        >
          <div
            className="card"
            style={{ width: 'min(920px, 100%)', maxHeight: '86vh', display: 'flex', flexDirection: 'column', padding: 0 }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
              <Sparkles size={16} style={{ color: 'var(--blue)' }} />
              <span style={{ fontWeight: 600 }}>Analyse — {targetType.replace('_', ' ')}</span>
              {targetId && (
                <code style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>{String(targetId).slice(0, 24)}</code>
              )}
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                <ModelPicker selection={selection} compact />
                <button className="btn btn-secondary btn-sm" onClick={() => setOpen(false)}><X size={14} /></button>
              </div>
            </div>

            <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
              <label style={{ fontSize: '0.72rem', color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Question (optional — a sensible default is used when blank)
              </label>
              <textarea
                value={q}
                onChange={e => setQ(e.target.value)}
                rows={2}
                placeholder="e.g. Why is expectancy negative despite a 46% win rate?"
                style={{
                  width: '100%', resize: 'vertical', padding: 8, borderRadius: 6,
                  background: 'var(--bg-tertiary)', color: 'var(--text-primary)',
                  border: '1px solid var(--border)', fontSize: '0.82rem',
                }}
              />

              <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
                <button className="btn btn-primary" onClick={run} disabled={busy}>
                  {busy ? <><Loader2 size={14} className="spin" /> Analysing…</> : <><Sparkles size={14} /> Run</>}
                </button>
                {busy && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    Adaptive thinking is on — a deep analysis can take a few minutes.
                  </span>
                )}
              </div>

              {error && (
                <div style={{ marginTop: 12, padding: 10, borderRadius: 6, background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.35)', color: 'var(--red)', fontSize: '0.8rem' }}>
                  {error}
                </div>
              )}

              {result && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                    <span>{result.model}</span>
                    <span>·</span>
                    <span>{result.label}</span>
                    {result.max_tokens && <><span>·</span><span>{Math.round(result.max_tokens / 1000)}K ceiling</span></>}
                    {result.effort && <><span>·</span><span>effort {result.effort}</span></>}
                    <button className="btn btn-secondary btn-sm" onClick={copy} style={{ marginLeft: 'auto' }}>
                      {copied ? <Check size={12} /> : <Copy size={12} />}
                    </button>
                  </div>
                  <div
                    style={{
                      whiteSpace: 'pre-wrap', lineHeight: 1.6, fontSize: '0.86rem',
                      background: 'var(--bg-tertiary)', padding: 14, borderRadius: 8,
                      border: '1px solid var(--border)',
                    }}
                  >
                    {result.analysis}
                  </div>
                  <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 6 }}>
                    Saved to Analysis history{result.id ? ` (${result.id.slice(0, 8)})` : ''}.
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
