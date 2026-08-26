import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, EyeOff, Eye } from 'lucide-react';

/**
 * [7.2 / H2] Schema-aware parameter renderer.
 *
 * Replaces the flat dump that rendered nested objects with `JSON.stringify` and
 * unset fields as the literal string "null". On a real run that produced a wall
 * of `{"prop_firm":{"account_mode":"personal",...}}` blobs interleaved with
 * forty `null`s — technically complete, practically unreadable.
 *
 * Three rules:
 *   - A nested object becomes its own collapsible group, not a JSON string.
 *   - Unset values (null/undefined/empty) are hidden behind a count, because
 *     "this was not set" is rarely what you came to read — but they stay
 *     reachable, because sometimes it is exactly what you came to read.
 *   - Values are formatted by type: numbers get thousands separators, booleans
 *     get on/off, arrays get a comma list.
 */

const isUnset = (v) =>
  v === null || v === undefined || v === '' ||
  (Array.isArray(v) && v.length === 0) ||
  (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0);

const isGroup = (v) =>
  v !== null && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length > 0;

function formatValue(v) {
  if (typeof v === 'boolean') return v ? 'on' : 'off';
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toLocaleString();
    // Trim float noise like 5.8999999999999995 without lying about precision.
    return String(Number(v.toPrecision(10)));
  }
  if (Array.isArray(v)) return v.join(', ');
  return String(v);
}

function Row({ label, value }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', gap: 10,
      fontSize: '0.74rem', padding: '2px 0', minWidth: 0,
    }}>
      <span style={{ color: 'var(--text-muted)', flexShrink: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
        {label}
      </span>
      <span style={{
        textAlign: 'right', fontVariantNumeric: 'tabular-nums',
        color: 'var(--text-primary)', flexShrink: 0, maxWidth: '60%', overflowWrap: 'anywhere',
      }}>
        {value}
      </span>
    </div>
  );
}

function Group({ name, obj, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const entries = Object.entries(obj).filter(([, v]) => !isUnset(v));
  if (!entries.length) return null;

  return (
    <div style={{
      gridColumn: '1 / -1', border: '1px solid var(--border)',
      borderRadius: 6, padding: '6px 8px', marginTop: 4,
    }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 5, width: '100%',
          background: 'none', border: 'none', cursor: 'pointer', padding: 0,
          color: 'var(--text-secondary)', fontSize: '0.72rem', fontWeight: 600,
        }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {name}
        <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
          ({entries.length})
        </span>
      </button>
      {open && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: '2px 16px', marginTop: 6,
        }}>
          {entries.map(([k, v]) => (
            isGroup(v)
              ? <Group key={k} name={k} obj={v} />
              : <Row key={k} label={k} value={formatValue(v)} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ParamsPanel({ params, title = 'Configuration parameters' }) {
  const [showUnset, setShowUnset] = useState(false);

  const { scalars, groups, unsetKeys } = useMemo(() => {
    const s = [], g = [], u = [];
    for (const [k, v] of Object.entries(params || {})) {
      if (isUnset(v)) u.push(k);
      else if (isGroup(v)) g.push([k, v]);
      else s.push([k, v]);
    }
    return { scalars: s, groups: g, unsetKeys: u };
  }, [params]);

  if (!params || !Object.keys(params).length) return null;

  return (
    <div style={{
      marginBottom: 16, padding: 12,
      background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)', minWidth: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--purple)' }}>{title}</span>
        <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
          {scalars.length + groups.length} set
        </span>
        {unsetKeys.length > 0 && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setShowUnset(v => !v)}
            style={{ marginLeft: 'auto', fontSize: '0.66rem', padding: '2px 8px' }}
            title="Fields left at their default — the engine resolves these itself"
          >
            {showUnset ? <Eye size={11} /> : <EyeOff size={11} />}
            {showUnset ? 'Hide' : 'Show'} {unsetKeys.length} unset
          </button>
        )}
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
        gap: '2px 16px',
      }}>
        {scalars.map(([k, v]) => <Row key={k} label={k} value={formatValue(v)} />)}
        {groups.map(([k, v]) => <Group key={k} name={k} obj={v} />)}
      </div>

      {showUnset && (
        <div style={{
          marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--border)',
          fontSize: '0.68rem', color: 'var(--text-muted)',
        }}>
          <div style={{ marginBottom: 4 }}>
            Not set — the engine resolves each of these from its own default:
          </div>
          <div style={{ overflowWrap: 'anywhere', lineHeight: 1.7 }}>
            {unsetKeys.map(k => (
              <code key={k} style={{
                display: 'inline-block', marginRight: 6, padding: '1px 5px',
                background: 'var(--bg-secondary)', borderRadius: 3,
              }}>{k}</code>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
