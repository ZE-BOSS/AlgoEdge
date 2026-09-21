/**
 * frontend/src/components/SymbolPicker.jsx
 *
 * A symbol field that suggests but does not restrict.
 *
 * The broker list is not the whole truth: a symbol can be missing from it
 * (US Tech 100 was), renamed, or not visible on the account the app is logged
 * into. A <select> makes those symbols untradable from the UI, so this accepts
 * anything typed and uses the list only to help find it.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react';

const SymbolPicker = memo(function SymbolPicker({ value, onChange, options, placeholder = 'Type to find symbol', autoFocus = false, className = '' }) {
  const [query, setQuery] = useState(value || '');
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const wrapRef = useRef(null);

  useEffect(() => { setQuery(value || ''); }, [value]);

  useEffect(() => {
    const onClickOutside = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options.slice(0, 50);
    const starts = options.filter(o => o.toLowerCase().startsWith(q));
    const contains = options.filter(o => !o.toLowerCase().startsWith(q) && o.toLowerCase().includes(q));
    return [...starts, ...contains].slice(0, 50);
  }, [query, options]);

  const commit = (val) => {
    setQuery(val);
    onChange(val);
    setOpen(false);
  };

  const handleKeyDown = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return; }
    if (!open) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setHighlight(h => Math.min(h + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight(h => Math.max(h - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (filtered[highlight]) commit(filtered[highlight]); else { onChange(query); setOpen(false); } }
    else if (e.key === 'Escape') { setOpen(false); }
  };

  return (
    <div ref={wrapRef} className={className} style={{ position: 'relative' }}>
      <input
        type="text"
        className="input"
        autoFocus={autoFocus}
        value={query}
        placeholder={placeholder}
        onChange={e => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); setHighlight(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        style={{ width: '100%' }}
      />
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 50,
          background: 'var(--bg-tertiary)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
          maxHeight: 220, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
        }}>
          {filtered.length === 0 ? (
            <div style={{ padding: '8px 10px', fontSize: '0.8rem', color: 'var(--text-muted)' }}>No matching symbols</div>
          ) : filtered.map((opt, i) => (
            <div
              key={opt}
              onMouseDown={(e) => { e.preventDefault(); commit(opt); }}
              onMouseEnter={() => setHighlight(i)}
              style={{
                padding: '6px 10px', fontSize: '0.8rem', cursor: 'pointer',
                background: i === highlight ? 'var(--bg-hover, rgba(255,255,255,0.06))' : 'transparent',
                color: 'var(--text-primary)',
              }}
            >
              {opt}
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

export default SymbolPicker;
