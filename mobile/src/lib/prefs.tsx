import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, type Prefs } from './api';

/**
 * The investor's own settings, stored on the server so the phone and the web
 * dashboard agree; plus `revealed`, which shows hidden balances until the app
 * restarts without changing the saved setting.
 */
export const DEFAULT_PREFS: Prefs = {
  hide_balances: false, chart_range: 'ALL', compact_numbers: false,
  email: { statements: true, trades: true, live_trades: true, fees: true },
  push: { money: true, withdrawals: true, statements: true, trades: true, live_trades: true, fees: true },
};

type Ctx = { prefs: Prefs; hidden: boolean; update: (c: Partial<Prefs>) => Promise<Prefs>; reveal: () => void };
const PrefsContext = createContext<Ctx>({ prefs: DEFAULT_PREFS, hidden: false, update: async () => DEFAULT_PREFS, reveal: () => {} });

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    let live = true;
    api.preferences().then((p) => { if (live) setPrefs(p); }).catch(() => {});
    return () => { live = false; };
  }, []);
  const update = useCallback(async (changes: Partial<Prefs>) => {
    const saved = await api.savePreferences(changes);
    setPrefs(saved);
    return saved;
  }, []);
  const value = useMemo(() => ({
    prefs, update, hidden: prefs.hide_balances && !revealed, reveal: () => setRevealed((r) => !r),
  }), [prefs, update, revealed]);
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export const usePrefs = () => useContext(PrefsContext);
