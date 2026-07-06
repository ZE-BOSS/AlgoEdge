import axios from 'axios';
import { useLoadingStore } from '../store';

const DEFAULT_URL = import.meta.env.VITE_DEFAULT_BACKEND_URL || 'http://localhost:8000';
const BACKEND_URL = localStorage.getItem('backend_url') || DEFAULT_URL;

const api = axios.create({
  baseURL: `${BACKEND_URL}/api`,
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

// ── JWT Token Management ────────────────────────────────────────────────────

const TOKEN_KEY = 'algoedge_token';
const REFRESH_KEY = 'algoedge_refresh';
const USER_KEY = 'algoedge_user';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const getRefreshToken = () => localStorage.getItem(REFRESH_KEY);
export const getStoredUser = () => {
  try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
};

export const storeAuth = (accessToken, refreshToken, user) => {
  localStorage.setItem(TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_KEY, refreshToken);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
};

export const clearAuth = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
};

// ── Request Interceptor: Attach JWT ─────────────────────────────────────────

const isSilentUrl = (url) => url && (url.includes('/latest_result') || url.includes('/status') || url.includes('/logs'));

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  
  if (!isSilentUrl(config.url)) {
    if (useLoadingStore) useLoadingStore.getState().startLoading();
  }
  
  return config;
});

// ── Response Interceptor: Auto-refresh on 401 ───────────────────────────────

let isRefreshing = false;
let failedQueue = [];

const processQueue = (error, token = null) => {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error);
    else resolve(token);
  });
  failedQueue = [];
};

api.interceptors.response.use(
  (res) => {
    if (!isSilentUrl(res.config?.url) && useLoadingStore) useLoadingStore.getState().stopLoading();
    return res;
  },
  async (err) => {
    if (!isSilentUrl(err.config?.url) && useLoadingStore) useLoadingStore.getState().stopLoading();
    const originalRequest = err.config;

    // If 401 and not already retrying
    if (err.response?.status === 401 && !originalRequest._retry) {
      const refreshToken = getRefreshToken();

      if (!refreshToken) {
        clearAuth();
        window.location.href = '/login';
        return Promise.reject(err);
      }

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then((token) => {
          originalRequest.headers['Authorization'] = `Bearer ${token}`;
          return api(originalRequest);
        }).catch(err => Promise.reject(err));
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const { data } = await axios.post(`${api.defaults.baseURL}/auth/refresh`, {
          refresh_token: refreshToken,
        });
        storeAuth(data.access_token, data.refresh_token, getStoredUser());
        processQueue(null, data.access_token);
        originalRequest.headers['Authorization'] = `Bearer ${data.access_token}`;
        return api(originalRequest);
      } catch (refreshErr) {
        processQueue(refreshErr, null);
        clearAuth();
        window.location.href = '/login';
        return Promise.reject(refreshErr);
      } finally {
        isRefreshing = false;
      }
    }

    console.error('API Error:', err.message);
    return Promise.reject(err);
  }
);

// ── Backend URL ─────────────────────────────────────────────────────────────

export const setBackendUrl = (url) => {
  localStorage.setItem('backend_url', url);
  api.defaults.baseURL = `${url}/api`;
};

export const getBackendUrl = () => localStorage.getItem('backend_url') || DEFAULT_URL;

// ── Auth ────────────────────────────────────────────────────────────────────

export const register = (data) => api.post('/auth/register', data);
export const login = (data) => api.post('/auth/login', data);
export const refreshTokenApi = (data) => api.post('/auth/refresh', data);
export const getMe = () => api.get('/auth/me');

// ── Dashboard ───────────────────────────────────────────────────────────────

export const getDashboardData = () => api.get('/dashboard');

// ── Health ──────────────────────────────────────────────────────────────────

export const checkHealth = () => api.get('/health');

// ── Trades (JWT-authed, no user_id) ─────────────────────────────────────────

export const getTrades = (params) => api.get('/trades', { params });
export const getPositions = () => api.get('/positions');

// ── Stats ───────────────────────────────────────────────────────────────────

export const getStats = () => api.get('/stats');

// ── Config ──────────────────────────────────────────────────────────────────

export const getConfig = () => api.get('/config');
export const updateConfig = (config) => api.put('/config', config);

// ── Charts ──────────────────────────────────────────────────────────────────

export const getChartData = (symbol, timeframe, count = 500) =>
  api.get(`/charts/${symbol}/${timeframe}`, { params: { count } });

// ── Compounding ─────────────────────────────────────────────────────────────

export const getCompounding = () => api.get('/compounding');
export const getCompoundingEvents = () => api.get('/compounding/events');

// ── Signals ─────────────────────────────────────────────────────────────────

export const getSignals = (params) => api.get('/signals', { params });
export const getSignalDetail = (id) => api.get(`/signals/${id}`);

// ── Backtest ────────────────────────────────────────────────────────────────

export const runBacktest = (data) => api.post('/backtest', data, { timeout: 300000 });
export const getBacktestStatus = () => api.get('/backtest_status');
export const getLatestBacktestResult = () => api.get('/latest_result');
export const getUnsavedTradeChart = (groupId) => api.get(`/backtest_result/trade/${groupId}/chart`);
export const getSavedTradeChart = (backtestId, groupId) => api.get(`/backtests/${backtestId}/trade/${groupId}/chart`);
export const stopBacktest = () => api.post('/stop');
export const saveBacktest = (id, data) => api.post(`/backtests/${id}/save`, data);
export const getBacktests = () => api.get('/backtests');
export const getBacktest = (id) => api.get(`/backtests/${id}`);
export const deleteBacktest = (id) => api.delete(`/backtests/${id}`);

// ── LLM ─────────────────────────────────────────────────────────────────────

export const analyzeTrade = (data) => api.post('/llm/analyze-trade', data);
export const askQuestion = (data) => api.post('/llm/custom', data);

// ── Admin ───────────────────────────────────────────────────────────────────

export const getUsers = () => api.get('/admin/users');

// ── Push ────────────────────────────────────────────────────────────────────

export const subscribePush = (data) => api.post('/push/subscribe', data);

// ── Bot Control ─────────────────────────────────────────────────────────────

export const startBot = (data) => api.post('/bot/start', data || {});
export const stopBot = () => api.post('/bot/stop');
export const getBotStatus = () => api.get('/bot/status');
export const getBotLogs = (limit = 50) => api.get('/bot/logs', { params: { limit } });

// ── Broker Configuration ────────────────────────────────────────────────────

export const saveBrokerStandard = (data) => api.post('/broker/standard', data);
export const saveBrokerDeriv = (data) => api.post('/broker/deriv', data);
export const getBrokerStatus = () => api.get('/broker/status');
export const testBrokerConnection = (data) => api.post('/broker/test', data);
export const removeBrokerStandard = () => api.delete('/broker/standard');
export const removeBrokerDeriv = () => api.delete('/broker/deriv');

export default api;
