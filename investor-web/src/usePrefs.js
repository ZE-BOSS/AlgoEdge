import { createContext, useContext } from 'react';

export const DEFAULTS = {
  hide_balances: false, chart_range: 'ALL', compact_numbers: false,
  email: { statements: true },
  push: { money: true, withdrawals: true, statements: true, trades: true },
};

export const PrefsContext = createContext({ prefs: DEFAULTS, hidden: false, update: async () => {}, reveal: () => {} });

/** The investor's settings, and whether amounts are hidden right now. */
export const usePrefs = () => useContext(PrefsContext);
