import { useState } from 'react';
import { HelpCircle, ChevronDown, ChevronRight } from 'lucide-react';

/**
 * [Phase 14 Part F] Collapsible "what this is / how to use it" note.
 *
 * Every panel in this app assumed knowledge it never supplied — order flow's
 * "volume by price", depth's "empty book", the GEX ticker format. Each one is
 * obvious once you know and opaque until then, and the fix is not more UI but
 * a sentence in the right place.
 *
 * Collapsed by default so it never competes with the data, and the state is
 * remembered per `id` so a note you have read stays shut.
 */
export default function HelpNote({ id, title = 'What this is', children, defaultOpen = false }) {
  const key = `algoedge_help_${id}`;
  const [open, setOpen] = useState(() => {
    try {
      const v = localStorage.getItem(key);
      return v === null ? defaultOpen : v === '1';
    } catch { return defaultOpen; }
  });

  const toggle = () => {
    setOpen(o => {
      try { localStorage.setItem(key, o ? '0' : '1'); } catch { /* quota */ }
      return !o;
    });
  };

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 6,
      marginBottom: 10, background: 'rgba(59,130,246,0.04)',
    }}>
      <button
        type="button"
        onClick={toggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, width: '100%',
          padding: '6px 10px', background: 'none', border: 'none',
          cursor: 'pointer', color: 'var(--text-secondary)', fontSize: '0.72rem',
          textAlign: 'left',
        }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <HelpCircle size={12} style={{ color: 'var(--blue)' }} />
        <span style={{ fontWeight: 600 }}>{title}</span>
      </button>
      {open && (
        <div style={{
          padding: '2px 12px 10px 28px', fontSize: '0.74rem',
          lineHeight: 1.6, color: 'var(--text-secondary)',
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

/** Shared bits of formatting so the notes read consistently. */
export const H = {
  /** A term being defined. */
  t: ({ children }) => (
    <b style={{ color: 'var(--text-primary)' }}>{children}</b>
  ),
  /** A concrete "how to trade on it" line — the part usually missing. */
  use: ({ children }) => (
    <div style={{
      marginTop: 6, paddingLeft: 8,
      borderLeft: '2px solid var(--green, #10b981)',
    }}>
      <b style={{ color: 'var(--green, #10b981)' }}>Use it: </b>{children}
    </div>
  ),
  /** A limitation stated plainly rather than discovered later. */
  caveat: ({ children }) => (
    <div style={{
      marginTop: 6, paddingLeft: 8,
      borderLeft: '2px solid var(--amber, #f59e0b)',
    }}>
      <b style={{ color: 'var(--amber, #f59e0b)' }}>Caveat: </b>{children}
    </div>
  ),
};
