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

/** A password input with a button to show what was typed. */
export function PasswordInput({ value, onChange, ...rest }) {
  const [shown, setShown] = useState(false);
  return (
    <span className="pw">
      <input type={shown ? 'text' : 'password'} value={value} onChange={onChange}
             autoCapitalize="off" autoCorrect="off" spellCheck={false} {...rest} />
      <button type="button" className="pw-eye" onClick={() => setShown((s) => !s)}
              aria-label={shown ? 'Hide password' : 'Show password'} aria-pressed={shown}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {shown
            ? <path d="M3 3l18 18M10.6 5.1A9.7 9.7 0 0112 5c5 0 9 4.5 10 7-.4 1-1.3 2.4-2.6 3.7M6.6 6.6C4.4 8 2.8 10.2 2 12c1 2.5 5 7 10 7 1.9 0 3.6-.6 5-1.5M9.9 9.9a3 3 0 004.2 4.2" />
            : <path d="M2 12c1-2.5 5-7 10-7s9 4.5 10 7c-1 2.5-5 7-10 7S3 14.5 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z" />}
        </svg>
      </button>
    </span>
  );
}
