import { useEffect, useState } from 'react';
import { Cpu, Gauge, Coins, ChevronDown } from 'lucide-react';

/**
 * Shared Claude model / effort / output-ceiling picker.
 *
 * One component wherever Claude is invoked — Analysis, Strategy Lab,
 * Fundamentals, Run Report, the log viewer, a trade chart. The catalogue comes
 * from the backend (`/analysis/models`) rather than being hardcoded here, so a
 * model added server-side appears in every picker at once and there is exactly
 * one place where a model id can be wrong.
 *
 * Output ceiling defaults to the model's real maximum — 128K on the Claude 5
 * family, 64K on Haiku 4.5 — not a house cap. The slider only lets you ask for
 * LESS: an over-large max_tokens is a 400 from the API, not a longer answer.
 */

const TIER_COLOR = {
  frontier: 'var(--purple, #a855f7)',
  flagship: 'var(--blue, #3b82f6)',
  balanced: 'var(--green, #10b981)',
  fast: 'var(--text-muted, #94a3b8)',
};

export default function ModelPicker({ selection, compact = false }) {
  const s = selection;
  const [open, setOpen] = useState(false);
  const { model, models, catalogue } = s;

  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!e.target.closest?.('[data-model-picker]')) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  if (!model) {
    return <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Loading models…</span>;
  }

  const configured = catalogue?.providers?.[s.provider]?.configured;

  return (
    <div data-model-picker style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}
        title={configured === false ? 'No API key configured for this provider' : model.note}
      >
        <Cpu size={13} style={{ color: TIER_COLOR[model.tier] || 'var(--text-muted)' }} />
        <span>{model.label}</span>
        {!compact && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.68rem' }}>
            {Math.round(s.resolvedMaxTokens / 1000)}K
            {model.supports_effort ? ` · ${s.rawEffort}` : ''}
          </span>
        )}
        {configured === false && (
          <span title="No API key" style={{ color: 'var(--amber, #f59e0b)' }}>●</span>
        )}
        <ChevronDown size={12} />
      </button>

      {open && (
        <div
          className="card"
          style={{
            position: 'absolute', top: '100%', right: 0, marginTop: 6, zIndex: 200,
            width: 380, maxHeight: 460, overflowY: 'auto', padding: 10,
            boxShadow: '0 8px 28px rgba(0,0,0,0.45)',
          }}
        >
          <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>
            Model
          </div>
          {models.map(m => (
            <button
              key={m.id}
              type="button"
              onClick={() => { s.update({ model: m.id, maxTokens: null }); setOpen(false); }}
              style={{
                display: 'block', width: '100%', textAlign: 'left', padding: '7px 8px',
                marginBottom: 3, borderRadius: 6, cursor: 'pointer',
                border: `1px solid ${m.id === model.id ? 'var(--blue)' : 'transparent'}`,
                background: m.id === model.id ? 'rgba(59,130,246,0.10)' : 'transparent',
                color: 'var(--text-primary)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: TIER_COLOR[m.tier] || 'var(--text-muted)' }} />
                <span style={{ fontWeight: 600, fontSize: '0.82rem' }}>{m.label}</span>
                <span style={{ marginLeft: 'auto', fontSize: '0.66rem', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
                  {m.context ? `${Math.round(m.context / 1000)}K ctx` : ''} · {Math.round(m.max_output / 1000)}K out
                </span>
              </div>
              {m.note && (
                <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: 2, paddingLeft: 12 }}>
                  {m.note}
                </div>
              )}
              {m.input_per_mtok != null && (
                <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginTop: 1, paddingLeft: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Coins size={10} /> ${m.input_per_mtok}/${m.output_per_mtok} per 1M in/out
                </div>
              )}
            </button>
          ))}

          {model.supports_effort && (
            <>
              <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', margin: '12px 0 6px', display: 'flex', alignItems: 'center', gap: 5 }}>
                <Gauge size={11} /> Thinking effort
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                {(catalogue?.effort_levels || ['low', 'medium', 'high', 'xhigh', 'max']).map(lvl => (
                  <button
                    key={lvl}
                    type="button"
                    className={`btn btn-sm ${s.rawEffort === lvl ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => s.update({ effort: lvl })}
                    style={{ flex: 1, fontSize: '0.68rem', padding: '4px 2px' }}
                  >
                    {lvl}
                  </button>
                ))}
              </div>
              <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', marginTop: 4 }}>
                Higher effort means deeper reasoning and more tokens. <code>high</code> is the API default.
              </div>
            </>
          )}

          <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', margin: '12px 0 6px' }}>
            Output ceiling
          </div>
          <input
            type="range"
            min={4000}
            max={model.max_output}
            step={4000}
            value={s.resolvedMaxTokens}
            onChange={e => {
              const v = +e.target.value;
              // Selecting the top of the range means "uncapped" — store null so
              // the request carries no explicit limit and the backend resolves
              // the model's own ceiling.
              s.update({ maxTokens: v >= model.max_output ? null : v });
            }}
            style={{ width: '100%' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.66rem', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
            <span>4K</span>
            <span style={{ color: s.resolvedMaxTokens >= model.max_output ? 'var(--green)' : 'var(--text-primary)' }}>
              {s.resolvedMaxTokens >= model.max_output
                ? `Max (${Math.round(model.max_output / 1000)}K — uncapped)`
                : `${Math.round(s.resolvedMaxTokens / 1000)}K`}
            </span>
            <span>{Math.round(model.max_output / 1000)}K</span>
          </div>

          {configured === false && (
            <div style={{ marginTop: 10, padding: 8, borderRadius: 6, background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.3)', fontSize: '0.68rem' }}>
              No API key for <b>{s.provider}</b>. Set <code>ANTHROPIC_API_KEY</code> in the backend
              environment — the key stays server-side and is never sent to the browser.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
