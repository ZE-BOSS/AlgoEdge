import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { errorText } from './format';

const STATE_TONE = {
  active: 'green', confirmed: 'green', paid: 'green', published: 'green', ok: 'green',
  pending: 'yellow', requested: 'yellow', claimed_sent: 'yellow', approved: 'blue',
  exception_pending: 'yellow', closing: 'yellow', undisclosed: 'blue',
  rejected: 'red', declined: 'red', closed: 'red', hidden: 'red',
  new: 'yellow', contacted: 'blue', accepted: 'green',
};

export function StateBadge({ state }) {
  const tone = STATE_TONE[state] || 'blue';
  return <span className={`badge badge-${tone}`}>{String(state).replace(/_/g, ' ')}</span>;
}

export function ErrorLine({ error }) {
  if (!error) return null;
  return (
    <div style={{ color: 'var(--red)', fontSize: '0.78rem', marginTop: 6 }} role="alert">
      {errorText(error)}
    </div>
  );
}

/**
 * A button that opens a small inline form. Used for every state change that
 * needs input — a reason, a reference, an amount — so the admin types it next
 * to the row it applies to rather than into a browser prompt.
 *
 * fields: [{ name, label, type='text', required, placeholder, defaultValue, hint }]
 * With no fields it is a single-click action (still showing any refusal inline).
 */
export function ActionForm({ label, fields = [], submitLabel, onSubmit, tone = 'secondary',
                             icon: Icon, confirm, disabled, compact }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e?.preventDefault();
    setPending(true);
    setError(null);
    try {
      const body = {};
      for (const f of fields) {
        const raw = values[f.name] ?? f.defaultValue ?? '';
        if (raw !== '' && raw !== null) body[f.name] = f.type === 'number' ? String(raw) : raw;
      }
      await onSubmit(body);
      setOpen(false);
      setValues({});
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  const confirmOk = !confirm || (values.__confirm || '') === confirm.match;

  if (!fields.length && !confirm) {
    return (
      <span style={{ display: 'inline-flex', flexDirection: 'column' }}>
        <button className={`btn btn-${tone} btn-sm`} disabled={disabled || pending} onClick={submit}>
          {pending ? <Loader2 size={12} className="spin" /> : Icon && <Icon size={12} />} {label}
        </button>
        <ErrorLine error={error} />
      </span>
    );
  }

  if (!open) {
    return (
      <button className={`btn btn-${tone} btn-sm`} disabled={disabled} onClick={() => setOpen(true)}>
        {Icon && <Icon size={12} />} {label}
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="inv-action-form" style={compact ? { minWidth: 0 } : undefined}>
      {fields.map(f => (
        <div key={f.name}>
          <label>{f.label}{f.required && ' *'}</label>
          {f.type === 'textarea' ? (
            <textarea rows={2} value={values[f.name] ?? f.defaultValue ?? ''} placeholder={f.placeholder}
                      onChange={e => setValues(v => ({ ...v, [f.name]: e.target.value }))} />
          ) : (
            <input type={f.type === 'number' ? 'text' : (f.type || 'text')}
                   inputMode={f.type === 'number' ? 'decimal' : undefined}
                   value={values[f.name] ?? f.defaultValue ?? ''} placeholder={f.placeholder}
                   required={f.required}
                   onChange={e => setValues(v => ({ ...v, [f.name]: e.target.value }))} />
          )}
          {f.hint && <div className="inv-hint">{f.hint}</div>}
        </div>
      ))}
      {confirm && (
        <div>
          <label>{confirm.label}</label>
          <input value={values.__confirm || ''} placeholder={confirm.match}
                 onChange={e => setValues(v => ({ ...v, __confirm: e.target.value }))} />
        </div>
      )}
      <div style={{ display: 'flex', gap: 6 }}>
        <button type="submit" className={`btn btn-${tone === 'secondary' ? 'primary' : tone} btn-sm`}
                disabled={pending || !confirmOk}>
          {pending && <Loader2 size={12} className="spin" />} {submitLabel || label}
        </button>
        <button type="button" className="btn btn-secondary btn-sm"
                onClick={() => { setOpen(false); setError(null); }}>Cancel</button>
      </div>
      <ErrorLine error={error} />
    </form>
  );
}

export function Loading() {
  return <div className="empty-state" style={{ padding: 30 }}><Loader2 className="spin" size={20} /></div>;
}

export function Empty({ children }) {
  return <div className="empty-state" style={{ padding: 30 }}>{children}</div>;
}

export function QueryState({ q, children }) {
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorLine error={q.error} />;
  return children(q.data?.data);
}
