/**
 * frontend/src/hooks/useSymbolOptions.js
 *
 * One symbol list for the Backtester, the Trading Book and the live slots.
 *
 * It asks the connected broker first (`/broker/instruments`, the same map that
 * turns a canonical `GER40` into whatever this broker lists it as), because the
 * hardcoded list below carries canonical names and aliases, not broker names:
 * Deriv lists the Nasdaq as "US Tech 100", which appears nowhere in it. The
 * static list is the fallback for when MT5 is not connected.
 *
 * Nothing here restricts anything: every symbol field is free text, so a symbol
 * neither source knows can still be typed.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getInstrumentResolution } from '../services/api';
import { useAuthStore, useConnectionStore } from '../store';

export const FALLBACK_SYMBOLS = [
  'XAUUSD', 'Gold', 'XAU', 'XAGUSD', 'Silver', 'XAG', 'XPTUSD', 'Platinum', 'XPT',
  'EURUSD', 'GBPUSD', 'AUDUSD', 'Aussie', 'GBPJPY', 'Geppy', 'GJ',
  'GBPNZD', 'GBPAUD', 'GBPCHF', 'EURJPY', 'EURAUD', 'USDJPY',
  'USDCHF', 'USDCAD', 'NZDUSD', 'Kiwi', 'AUDJPY', 'CADJPY', 'GBPCAD', 'EURGBP',
  'US30', 'Wall Street 30', 'WS30', 'DJI', 'DOW', 'YM',
  'NAS100', 'US100', 'USTEC', 'US Tech 100', 'NDX', 'NQ',
  'SPX500', 'US500', 'SPX', 'SP500', 'S&P500', 'ES',
  'GER40', 'DAX', 'DE40', 'GER30', 'HK50', 'Hang Seng', 'HSI',
  'US2000', 'RUT', 'UK100', 'FTSE100', 'FRA40', 'CAC40',
  'EU50', 'EUSTX50', 'NTH25', 'AEX25', 'SWI20', 'SMI20',
  'AUS200', 'ASX200', 'JP225', 'Nikkei',
  'USOIL', 'WTI', 'Crude Oil', 'OIL', 'XTIUSD', 'US Oil',
  'UKOIL', 'Brent', 'UK Brent Oil', 'XCUUSD', 'Copper',
  'NG', 'XNGUSD', 'Natural Gas',
  'BTCUSD', 'Bitcoin', 'BTC', 'ETHUSD', 'ETH', 'Ethereum',
  'DOGUSD', 'Dogecoin', 'DOGE', 'SOLUSD', 'Solana', 'SOL',
  'XRPUSD', 'Ripple', 'XRP', 'LTCUSD', 'Litecoin', 'LTC',
  'Volatility 10 Index', 'Volatility 25 Index', 'Volatility 50 Index',
  'Volatility 75 Index', 'Volatility 100 Index', 'Volatility 150 Index', 'Volatility 250 Index',
  'Volatility 10 (1s) Index', 'Volatility 25 (1s) Index', 'Volatility 50 (1s) Index',
  'Volatility 75 (1s) Index', 'Volatility 100 (1s) Index', 'Volatility 150 (1s) Index',
  'Volatility 250 (1s) Index',
  'Boom 300 Index', 'Boom 500 Index', 'Boom 1000 Index',
  'Crash 300 Index', 'Crash 500 Index', 'Crash 1000 Index',
  'Jump 10 Index', 'Jump 25 Index', 'Jump 50 Index', 'Jump 75 Index', 'Jump 100 Index',
  'Step Index', 'Range Break 100 Index', 'Range Break 200 Index',
];

/**
 * @param {string[]} extra symbols to list first (e.g. the ones already configured).
 * @returns {string[]} broker symbols, then the configured ones, then the fallbacks.
 */
export function useSymbolOptions(extra = []) {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore(s => s.isAuthenticated);

  const { data } = useQuery({
    queryKey: ['instrumentResolution'],
    queryFn: () => getInstrumentResolution().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
    staleTime: 10 * 60 * 1000,
    retry: 0,
  });

  // A stable key: the hook is read by memoised children, so returning a fresh
  // array on every render would defeat their memo.
  const extraKey = extra.join('|');
  const brokerKey = (data?.instruments || []).map(r => r.broker_symbol).join('|');

  return useMemo(() => {
    const broker = (data?.instruments || [])
      .filter(r => r.available && r.broker_symbol)
      .map(r => r.broker_symbol);
    return [...new Set([...broker, ...extraKey.split('|'), ...FALLBACK_SYMBOLS].filter(Boolean))];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brokerKey, extraKey]);
}
