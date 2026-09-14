import { CONFLUENCE_FIELDS, CONFLUENCE_DEFAULTS } from './confluenceSpec';

/** Renders the optional-confluence controls for one params block. */
export default function ConfluenceFields({ block, values, onChange, labelStyle }) {
  const fields = CONFLUENCE_FIELDS[block] || [];
  const v = { ...(CONFLUENCE_DEFAULTS[block] || {}), ...(values || {}) };
  return (
    <>
      <div style={{ gridColumn: '1 / -1', fontSize: '0.7rem', fontWeight: 600, marginTop: 8, color: 'var(--text-secondary)' }}>
        Confluences (off = engine as before)
      </div>
      {fields.map(([key, label, kind]) => (
        <div key={key}>
          {kind === 'bool' ? (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.72rem', marginTop: 14 }}>
              <input type="checkbox" checked={!!v[key]} onChange={e => onChange(key, e.target.checked)} /> {label}
            </label>
          ) : (
            <>
              <label style={labelStyle}>{label}</label>
              {Array.isArray(kind)
                ? <select value={v[key]} onChange={e => onChange(key, e.target.value)}>{kind.map(o => <option key={o} value={o}>{o}</option>)}</select>
                : <input type="number" step="0.05" min="0" value={v[key] ?? 0} onChange={e => onChange(key, +e.target.value)} />}
            </>
          )}
        </div>
      ))}
    </>
  );
}
