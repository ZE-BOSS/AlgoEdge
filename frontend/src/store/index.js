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
  circuitBreaker: { paused: false, reason: '' },
  setStats: (stats) => set({ stats }),
  setCircuitBreaker: (circuitBreaker) => set({ circuitBreaker }),
}));

export const useNotificationStore = create((set) => ({
  notifications: [],
  addNotification: (notification) => set((state) => {
    // Add unique ID and default properties if not provided
    const newNotif = {
      id: Date.now().toString() + Math.random().toString(36).substring(2, 9),
      duration: 5000,
      ...notification
    };
    
    // Automatically trigger Web Browser Notification
    if (typeof Notification !== 'undefined') {
      if (Notification.permission === "granted") {
        new Notification(newNotif.title, {
          body: newNotif.message,
          icon: '/favicon.ico' // Assuming standard vite icon location
        });
      } else if (Notification.permission !== "denied") {
        Notification.requestPermission().then(permission => {
          if (permission === "granted") {
          new Notification(newNotif.title, {
            body: newNotif.message,
            icon: '/favicon.ico'
          });
        }
      });
    }
    }
    
    return { notifications: [newNotif, ...state.notifications] };
  }),
  removeNotification: (id) => set((state) => ({
    notifications: state.notifications.filter(n => n.id !== id)
  })),
}));

export const useLoadingStore = create((set, get) => ({
  activeRequests: 0,
  startLoading: () => set((state) => ({ activeRequests: state.activeRequests + 1 })),
  stopLoading: () => set((state) => ({ activeRequests: Math.max(0, state.activeRequests - 1) })),
}));
