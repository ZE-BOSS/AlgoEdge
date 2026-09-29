import { useCallback, useEffect, useRef, useState } from 'react';

/** Load from the API; `reload` re-runs it (pull-to-refresh, after an action). */
export function useLoad<T>(fn: () => Promise<T>, key = '') {
  const [state, setState] = useState<{ data: T | null; error: Error | null; loading: boolean }>(
    { data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => {
    let live = true;
    latest.current().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error: Error) => live && setState((s) => ({ data: s.data, error, loading: false })),
    );
    return () => { live = false; };
  }, [key, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}

/** A submit that cannot be double-sent, with its error kept for display. */
export function useSubmit(action: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const submit = async () => {
    if (busy) return;
    setBusy(true); setError(null);
    try { await action(); } catch (e) { setError(e as Error); } finally { setBusy(false); }
  };
  return { busy, error, submit };
}
