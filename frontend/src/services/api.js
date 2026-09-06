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

// Standalone refresh call, reused by the 401 response interceptor below and
// by useAuthStore's `refreshToken` action (called from the WebSocket
// auth-failure handler in hooks/useBackendConnection.js). Stores the new
// tokens on success; clears auth and rethrows on failure so callers can
// decide how to react (e.g. force logout).
export const performTokenRefresh = async () => {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    clearAuth();
    throw new Error('No refresh token available');
  }
  try {
    const { data } = await axios.post(`${api.defaults.baseURL}/auth/refresh`, {
      refresh_token: refreshToken,
    });
    storeAuth(data.access_token, data.refresh_token, getStoredUser());
    return data.access_token;
  } catch (err) {
    clearAuth();
    throw err;
  }
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
        const accessToken = await performTokenRefresh();
        processQueue(null, accessToken);
        originalRequest.headers['Authorization'] = `Bearer ${accessToken}`;
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
export const getTradesSummary = (params) => api.get('/trades/summary', { params });
export const getPositions = () => api.get('/positions');
export const forceCloseAll = () => api.get('/force-close-all');

// ── Stats ───────────────────────────────────────────────────────────────────

export const getStats = () => api.get('/stats');

// ── Config ──────────────────────────────────────────────────────────────────

export const getConfig = () => api.get('/config');
export const updateConfig = (config) => api.put('/config', config);

// ── Charts ──────────────────────────────────────────────────────────────────

export const getChartData = (symbol, timeframe, count = 500) =>
  api.get(`/charts/${symbol}/${timeframe}`, { params: { count } });

// ── Signals ─────────────────────────────────────────────────────────────────

export const getSignals = (params) => api.get('/signals', { params });
export const getSignalDetail = (id) => api.get(`/signals/${id}`);

// ── Backtest ────────────────────────────────────────────────────────────────

export const runBacktest = (data) => api.post('/backtest', data, { timeout: 300000 });
export const runPortfolioBacktest = (data) => api.post('/portfolio_backtest', data, { timeout: 300000 });
export const getBacktestStatus = () => api.get('/backtest_status');
export const getLatestBacktestResult = () => api.get('/latest_result');
export const getUnsavedTradeChart = (groupId) => api.get(`/backtest_result/trade/${groupId}/chart`);
export const getSavedTradeChart = (backtestId, groupId) => api.get(`/backtests/${backtestId}/trade/${groupId}/chart`);
export const stopBacktest = () => api.post('/stop');
export const saveBacktest = (id, data) => api.post(`/backtests/${id}/save`, data);
export const getBacktests = () => api.get('/backtests');
export const getBulkBacktests = (ids) => api.post('/backtests/bulk', { ids });
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
export const syncHistoricalTrades = () => api.post('/bot/sync_history');

// ── MT5 Diagnostics ─────────────────────────────────────────────────────────

export const testMt5Entry = (data) => api.post('/mt5_test/entry', data);
export const testMt5Close = (data) => api.post('/mt5_test/close', data);
export const testMt5Breakeven = (data) => api.post('/mt5_test/breakeven', data);
export const testMt5Trail = (data) => api.post('/mt5_test/trail', data);
export const getBotStatus = () => api.get('/bot/status');
export const getBotLogs = (limit = 50) => api.get('/bot/logs', { params: { limit } });
export const getSymbolCosts = (symbol) => api.get(`/mt5_test/symbol-costs/${symbol}`);

// ── Broker Configuration ────────────────────────────────────────────────────

export const saveBrokerStandard = (data) => api.post('/broker/standard', data);
export const getBrokerStatus = () => api.get('/broker/status');

// ── Live account, Telegram diagnostics, and account-state reset ──────────────
// getBrokerStatus only ever returned the masked login + server name, so nothing
// in the UI could show the balance the risk percentage is actually applied to.
export const getLiveAccount = () => api.get('/account/live');
export const getAccountState = () => api.get('/account/state');
export const resetAccountState = (data) => api.post('/account/reset', data);
export const getTelegramStatus = () => api.get('/telegram/status');
export const sendTelegramTest = (message) => api.post('/telegram/test', { message: message || null });
export const testBrokerConnection = (data) => api.post('/broker/test', data);
export const removeBrokerStandard = () => api.delete('/broker/standard');
// Canonical instrument -> this broker's symbol (task 14.9). `refresh` re-runs
// discovery against the terminal instead of reading the cached map.
export const getInstrumentResolution = (refresh = false) =>
  api.get('/broker/instruments', { params: { refresh } });

// ── Analysis (Claude-powered) ───────────────────────────────────────────────

export const getAnalysisProviders = () => api.get('/analysis/providers');
// The model catalogue: id, label, context window, real output ceiling, price.
// Drives the model picker so the choice is made with the tradeoff visible.
export const getAnalysisModels = () => api.get('/analysis/models');
// Analysis can take minutes at a 128K ceiling with adaptive thinking on, so it
// gets its own timeout rather than the client default.
export const runAnalysis = (data) => api.post('/analysis/run', data, { timeout: 600000 });
export const getAnalysisHistory = (params) => api.get('/analysis/history', { params });
export const deleteAnalysis = (id) => api.delete(`/analysis/${id}`);

// ── Logs ────────────────────────────────────────────────────────────────────

export const getLogs = (params) => api.get('/logs', { params });
export const getLogSessions = () => api.get('/logs/sessions');
export const getLogFiles = () => api.get('/logs/files');
// On-disk history is the same endpoint with source=file — there is no separate
// /logs/file route (this previously pointed at one that does not exist).
export const getLogFile = (params) => api.get('/logs', { params: { ...params, source: 'file' } });
export const getLogStats = () => api.get('/logs/stats');

// ── Backtest replay ─────────────────────────────────────────────────────────

export const getReplaySeries = () => api.get('/backtest_result/replay');
export const getSavedReplaySeries = (backtestId) => api.get(`/backtests/${backtestId}/replay`);

// ── Schema introspection (drives the schema-driven parameter forms) ─────────

export const getParameterSchema = () => api.get('/config/parameter_schema');

// ── Fundamentals (provider-backed: free now, paid later, no code change) ────

export const getFundProviders = () => api.get('/fundamentals/providers');
export const selectFundProvider = (data) => api.post('/fundamentals/providers/select', data);
export const getOrderFlow = (params) => api.get('/fundamentals/orderflow', { params });
export const getOrderBook = (params) => api.get('/fundamentals/orderbook', { params });
export const getCorrelation = (params) => api.get('/fundamentals/correlation', { params });
export const getOptionsChain = (params) => api.get('/fundamentals/options', { params });
export const getGex = (params) => api.get('/fundamentals/gex', { params });
export const getEconCalendar = (params) => api.get('/fundamentals/calendar', { params });
export const getFundHealth = () => api.get('/fundamentals/health');

// ── Strategy Factory [Phase 14 Stream 3] ──────────────────────────────────

/** List all registered + generated strategies with status metadata. */
export const listFactoryStrategies = () => api.get('/strategy-factory/strategies');

// [L1] Per-strategy exit/session defaults, derived from the Phase 3 measurements.
// Trailing and session gating are NOT account-level settings: the trailing sweep
// improved 10 of 15 cells and made 5 worse, and session-gate contribution ranged
// from -0.170 to +0.126 by strategy. The Backtester reads this so the panel shows
// the measured-best values for whichever strategy is selected.
export const getStrategyDefaults = () => api.get('/strategy-factory/strategy-defaults');
/** Scaffold a new strategy from a spec object. */
export const generateStrategy = (data) => api.post('/strategy-factory/generate', data);
/** Activate a generated strategy: commit to dev branch, open GitHub PR. */
export const activateStrategy = (strategyId, data = {}) =>
  api.post(`/strategy-factory/activate/${strategyId}`, data);
/** Delete a generated (non-live) strategy scaffold. */
export const deleteStrategy = (strategyId) =>
  api.delete(`/strategy-factory/${strategyId}`);

export default api;
