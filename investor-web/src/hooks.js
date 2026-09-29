import { useCallback, useEffect, useRef, useState } from 'react';
import { getSession } from './api';

/** Load something from the API; `reload` re-runs it after an action.
 *  `key` re-runs it when an input changes (e.g. the amount being quoted). */
export function useLoad(fn, key = '') {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => {
    let live = true;
    latest.current().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error) => live && setState((s) => ({ data: s.data, error, loading: false })),
    );
    return () => { live = false; };
  }, [key, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}

/** The signed-in session, kept in step with sign-in / sign-out in any tab. */
export function useSession() {
  const [session, set] = useState(getSession);
  useEffect(() => {
    const sync = () => set(getSession());
    window.addEventListener('avq-session', sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener('avq-session', sync);
      window.removeEventListener('storage', sync);
    };
  }, []);
  return session;
}

/** A submit button that shows it is working and cannot be double-sent. */
export function useSubmit(action) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e?.preventDefault();
    if (busy) return;
    setBusy(true); setError(null);
    try { await action(); } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return { busy, error, submit, setError };
}
