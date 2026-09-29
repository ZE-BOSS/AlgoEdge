import { useState } from 'react';

export function Card({ title, aside, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || aside) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label = 'Loading' }) {
  return <div className="spinner" role="status"><span className="sr-only">{label}</span></div>;
}

export function Problem({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="problem" role="alert">
      {error.message}
      {onRetry && <button className="link" onClick={onRetry}>Try again</button>}
    </div>
  );
}

/** Loading / error / content, in that order. */
export function Loaded({ q, children }) {
  if (q.loading && !q.data) return <Spinner />;
  if (q.error && !q.data) return <Problem error={q.error} onRetry={q.reload} />;
  return children(q.data);
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Badge({ tone = 'neutral', children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Copy({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="copy" onClick={() => {
      navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); });
    }}>{done ? 'Copied' : 'Copy'}</button>
  );
}
