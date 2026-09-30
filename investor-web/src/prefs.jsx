import { useCallback, useEffect, useMemo, useState } from 'react';
import { DEFAULTS, PrefsContext as Ctx } from './usePrefs';
import { call } from './api';

/**
 * The investor's own settings (server-side, so the web app and the phone app
 * agree), plus one per-session switch: `revealed`, which shows hidden
 * balances until the page is closed without changing the saved setting.
 */

export function PrefsProvider({ signedIn, children }) {
  const [prefs, setPrefs] = useState(DEFAULTS);
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    if (!signedIn) return undefined;
    let live = true;
    call('/preferences').then((p) => live && setPrefs(p)).catch(() => {});
    return () => { live = false; };
  }, [signedIn]);
  const update = useCallback(async (changes) => {
    const saved = await call('/preferences', { method: 'PUT', body: changes });
    setPrefs(saved);
    return saved;
  }, []);
  const value = useMemo(() => ({
    prefs, update,
    hidden: prefs.hide_balances && !revealed,
    reveal: () => setRevealed((r) => !r),
  }), [prefs, update, revealed]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

