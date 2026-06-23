import { create } from 'zustand';
import { getToken, getStoredUser, storeAuth, clearAuth } from '../services/api';

export const useAuthStore = create((set) => ({
  user: getStoredUser(),
  token: getToken(),
  isAuthenticated: !!getToken(),
  login: (accessToken, refreshToken, user) => {
    storeAuth(accessToken, refreshToken, user);
    set({ user, token: accessToken, isAuthenticated: true });
  },
  logout: () => {
    clearAuth();
    set({ user: null, token: null, isAuthenticated: false });
  },
}));

export const useConnectionStore = create((set) => ({
  status: 'CHECKING',
  lastSeen: null,
  wsConnected: false,
  setStatus: (status) => set({ status, lastSeen: status === 'ONLINE' ? new Date() : undefined }),
  setWsConnected: (wsConnected) => set({ wsConnected }),
}));

export const useTradesStore = create((set) => ({
  trades: [],
  positions: [],
  signals: [],
  setTrades: (trades) => set({ trades }),
  setPositions: (positions) => set({ positions }),
  setSignals: (signals) => set({ signals }),
  addTrade: (trade) => set((s) => ({ trades: [trade, ...s.trades] })),
}));

export const useRiskStore = create((set) => ({
  stats: null,
  compounding: null,
  circuitBreaker: { paused: false, reason: '' },
  setStats: (stats) => set({ stats }),
  setCompounding: (compounding) => set({ compounding }),
  setCircuitBreaker: (circuitBreaker) => set({ circuitBreaker }),
}));

