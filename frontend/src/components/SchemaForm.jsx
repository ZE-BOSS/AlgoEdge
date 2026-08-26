import { useMemo, useState } from 'react';
import { Search, RotateCcw, Info } from 'lucide-react';

/**
 * Schema-driven parameter form — tasks 7.2 through 7.5, 7.9, 3.2.
 *
 * Fields come from `/config/parameter_schema`, which `core/schema_introspection.py`
 * generates from the dataclasses themselves. That is the whole point: `DEFAULT_FORM`
 * in Backtester.jsx is a hand-maintained mirror of `RiskParams`, and hand-maintained
 * mirrors drift. A parameter added to a dataclass appears here automatically, with
 * its real default and its own docstring as help text.
 *
 * Nested values render as typed rows rather than raw JSON (7.3 / H2) — the previous
 * behaviour dumped a dict into a text input and asked you to edit JSON by hand.
 */

function Help({ text }) {
  if (!text) return null;
  return (
    <span title={text} style={{ display: 'inline-flex', color: 'var(--text-muted)', cursor: 'help', marginLeft: 4 }}>
      <Info size={11} />
    </span>
  );
}

function Field({ row, value, onChange }) {
  const v = value ?? row.default;
  const changed = JSON.stringify(v) !== JSON.stringify(row.default);
  const name = row.key.split('.').slice(1).join('.');

  const label = (
    <label style={{ display: 'flex', alignItems: 'center', fontSize: '0.72rem', color: 'var(--text-secondary)', marginBottom: 3 }}>
      {row.label}
      <Help text={row.help} />
      {changed && (
        <span
          title={`Default: ${JSON.stringify(row.default)}`}
          style={{ marginLeft: 'auto', fontSize: '0.6rem', color: 'var(--amber)', cursor: 'help' }}
        >
          changed
        </span>
      )}
    </label>
  );

  // Enum
  if (row.enum_options?.length) {
    return (
      <div>
        {label}
        <select className="input input-sm" style={{ width: '100%' }} value={v ?? ''} onChange={e => onChange(name, e.target.value)}>
          {row.enum_options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }

  // Boolean
  if (row.type === 'bool' || typeof row.default === 'boolean') {
    return (
      <div>
        {label}
        <button
          type="button"
          className={`btn btn-sm ${v ? 'btn-green' : 'btn-secondary'}`}
          onClick={() => onChange(name, !v)}
          style={{ width: '100%' }}
        >
          {v ? 'Enabled' : 'Disabled'}
        </button>
      </div>
    );
  }

  // List of numbers — e.g. tp_splits, vwap_band_sigmas. Editable as a comma list
  // rather than raw JSON, and validated back into an array.
  if (Array.isArray(row.default)) {
    return (
      <div>
        {label}
        <input
          className="input input-sm"
          style={{ width: '100%' }}
          value={Array.isArray(v) ? v.join(', ') : String(v ?? '')}
          onChange={e => {
            const parts = e.target.value.split(',').map(s => s.trim()).filter(Boolean);
            const nums = parts.map(Number);
            onChange(name, nums.every(n => !Number.isNaN(n)) ? nums : parts);
          }}
        />
      </div>
    );
  }

  // Dict — a per-symbol map (max_lot_sizes) rendered as key/value rows (7.10 / H5).
  if (row.default && typeof row.default === 'object') {
    const entries = Object.entries(v || {});
    return (
      <div style={{ gridColumn: '1 / -1' }}>
        {label}
        <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 8 }}>
          {entries.map(([k, val], i) => (
            <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 4 }}>
              <input
                className="input input-sm" style={{ flex: 1 }} value={k}
                onChange={e => {
                  const next = { ...(v || {}) };
                  delete next[k];
                  next[e.target.value] = val;
                  onChange(name, next);
                }}
              />
              <input
                className="input input-sm" style={{ width: 110 }} value={val}
                onChange={e => {
                  const n = Number(e.target.value);
                  onChange(name, { ...(v || {}), [k]: Number.isNaN(n) ? e.target.value : n });
                }}
              />
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => { const next = { ...(v || {}) }; delete next[k]; onChange(name, next); }}
              >×</button>
            </div>
          ))}
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => onChange(name, { ...(v || {}), '': 0 })}
          >+ Add row</button>
        </div>
      </div>
    );
  }

  // Number / string
  const isNum = row.type?.includes('float') || row.type?.includes('int') || typeof row.default === 'number';
  return (
    <div>
      {label}
      <input
        className="input input-sm"
        style={{ width: '100%' }}
        type={isNum ? 'number' : 'text'}
        step={row.type?.includes('int') ? 1 : 'any'}
        value={v ?? ''}
        onChange={e => {
          const raw = e.target.value;
          onChange(name, isNum ? (raw === '' ? null : Number(raw)) : raw);
        }}
      />
    </div>
  );
}

export default function SchemaForm({ schema, group, values, onChange, title }) {
  const [q, setQ] = useState('');

  const rows = useMemo(() => {
    const all = (schema || []).filter(r => r.group === group);
    if (!q) return all;
    const needle = q.toLowerCase();
    return all.filter(r =>
      r.label.toLowerCase().includes(needle) ||
      r.key.toLowerCase().includes(needle) ||
      (r.help || '').toLowerCase().includes(needle)
    );
  }, [schema, group, q]);

  const changedCount = useMemo(
    () => rows.filter(r => {
      const name = r.key.split('.').slice(1).join('.');
      return values?.[name] !== undefined &&
             JSON.stringify(values[name]) !== JSON.stringify(r.default);
    }).length,
    [rows, values],
  );

  const resetAll = () => {
    const next = { ...values };
    for (const r of rows) delete next[r.key.split('.').slice(1).join('.')];
    onChange(next, true);
  };

  if (!schema) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', padding: 12 }}>Loading schema…</div>;
  }
  if (!rows.length && !q) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', padding: 12 }}>No parameters in group “{group}”.</div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        {title && <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{title}</span>}
        <div style={{ position: 'relative', flex: 1, maxWidth: 260 }}>
          <Search size={12} style={{ position: 'absolute', left: 7, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input
            className="input input-sm" value={q} onChange={e => setQ(e.target.value)}
            placeholder={`Filter ${(schema || []).filter(r => r.group === group).length} parameters…`}
            style={{ width: '100%', paddingLeft: 24 }}
          />
        </div>
        {changedCount > 0 && (
          <>
            <span style={{ fontSize: '0.68rem', color: 'var(--amber)' }}>{changedCount} changed</span>
            <button className="btn btn-secondary btn-sm" onClick={resetAll} title="Reset visible fields to their dataclass defaults">
              <RotateCcw size={12} />
            </button>
          </>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(210px,1fr))', gap: 10 }}>
        {rows.map(r => {
          const name = r.key.split('.').slice(1).join('.');
          return (
            <Field
              key={r.key}
              row={r}
              value={values?.[name]}
              onChange={(k, val) => onChange({ ...values, [k]: val })}
            />
          );
        })}
      </div>

      {!rows.length && q && (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', padding: 12 }}>
          Nothing matches “{q}”.
        </div>
      )}
    </div>
  );
}
