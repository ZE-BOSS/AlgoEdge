import axios from 'axios';
import { useLoadingStore } from '../store';
import {
  activeServerId, clearServerAuth, getServer, getServerRefresh, getServerToken,
  getServerUser, listServers, saveServers, storeServerAuth,
} from './servers';

const DEFAULT_URL = import.meta.env.VITE_DEFAULT_BACKEND_URL || 'http://localhost:8000';

// ONE instance, but the server is resolved PER REQUEST rather than stored on
// it. The alternative — mutating `baseURL` when you switch servers — breaks
// the moment two requests are in flight at once, which is the normal state of
// this app: the dashboard polls live while a backtest streams from demo, and
// whichever switch happened last decides where both of them go.
//
// Resolving at request time also gives a caller an escape hatch: pass
// `{ serverId }` on any call to aim it somewhere other than the active server.
// The Backtester uses that to run against demo without moving the whole UI.
const api = axios.create({
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

// Background fetches that must not flash the global loading indicator — result
// pages and trade charts arrive in many small requests by design.
const isSilentUrl = (url) => url && (url.includes('/latest_result') || url.includes('/status') || url.includes('/logs')
  || url.includes('/chart') || (url.includes('/backtests/') && url.includes('/trades')));

api.interceptors.request.use((config) => {
  // Which backend, and whose token. A token is signed with that backend's own
  // secret, so sending live's token to demo is not merely useless — it is the
  // kind of cross-wiring that makes a demo breach a live one if the secrets
  // are ever shared to "fix" it. They are not shared; you sign in per server.
  const serverId = config.serverId || activeServerId();
  const server = getServer(serverId);
  if (server) {
    config.baseURL = `${server.baseUrl.replace(/\/$/, '')}/api`;
    config.serverId = serverId;
  } else {
    config.baseURL = `${DEFAULT_URL}/api`;
  }
  const token = getServerToken(serverId) || getToken();
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
export const performTokenRefresh = async (serverId = null) => {
  // Refresh against the server that issued the token, not against whichever
  // one happens to be active now. Refreshing live's session because a demo
  // request 401'd would be both wrong and confusing.
  const id = serverId || activeServerId();
  const server = getServer(id);
  const refreshToken = getServerRefresh(id) || getRefreshToken();
  if (!refreshToken) {
    clearServerAuth(id);
    clearAuth();
    throw new Error('No refresh token available');
  }
  const base = `${(server?.baseUrl || DEFAULT_URL).replace(/\/$/, '')}/api`;
  try {
    const { data } = await axios.post(`${base}/auth/refresh`, {
      refresh_token: refreshToken,
    });
    storeServerAuth(id, data.access_token, data.refresh_token,
                    getServerUser(id) || getStoredUser());
    storeAuth(data.access_token, data.refresh_token, getStoredUser());
    return data.access_token;
  } catch (err) {
    clearServerAuth(id);
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
      const failedOn = originalRequest?.serverId || activeServerId();
      const refreshToken = getServerRefresh(failedOn) || getRefreshToken();

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
        const accessToken = await performTokenRefresh(failedOn);
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
  // Kept for the Connection screen and for anyone running a single server.
  // It now edits the ACTIVE registry entry rather than a global baseURL —
  // there is no global baseURL any more, because a request resolves its own.
  localStorage.setItem('backend_url', url);
  const id = activeServerId();
  saveServers(listServers().map((s) => (s.id === id ? { ...s, baseUrl: url } : s)));
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

// `serverId` aims this one request at a named server without moving the
// rest of the UI — the request interceptor resolves it per call. This is
// how a long run goes to demo while you keep watching live.
export const runBacktest = (data, serverId = null) =>
  api.post('/backtest', data, { timeout: 300000, ...(serverId ? { serverId } : {}) });
export const runPortfolioBacktest = (data) => api.post('/portfolio_backtest', data, { timeout: 300000 });
export const getBacktestStatus = () => api.get('/backtest_status');
export const getLatestBacktestResult = () => api.get('/latest_result');
// Paged delivery of a finished run — see utils/progressiveResult.js.
export const getLatestResultSummary = () => api.get('/latest_result/summary');
export const getLatestResultTrades = (offset, limit) => api.get('/latest_result/trades', { params: { offset, limit }, timeout: 30000 });
// `tf` asks for one timeframe, trimmed around the trade; omitted = legacy all-timeframes shape.
export const getUnsavedTradeChart = (groupId, tf) => api.get(`/backtest_result/trade/${groupId}/chart`, { params: tf ? { tf } : {} });
export const getSavedTradeChart = (backtestId, groupId, tf) => api.get(`/backtests/${backtestId}/trade/${groupId}/chart`, { params: tf ? { tf } : {} });
export const getBacktestSummary = (id) => api.get(`/backtests/${id}`, { params: { include_trades: false } });
export const getBacktestTrades = (id, offset, limit) => api.get(`/backtests/${id}/trades`, { params: { offset, limit }, timeout: 30000 });
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

// ── Investors (admin) — investor platform Phase 2 ───────────────────────────
// Money and units arrive as STRINGS and are sent back as strings: a JSON number
// is a float in the browser, and a float is how a statement ends up a cent out.

// What the ACCOUNT did, as opposed to what the bot did: deposits, withdrawals,
// commission, swap, and whether the journal adds up to the broker's balance.
// Separate from `inv` because this side may read MT5 and the investor side
// must never — see backend/investor/__init__.py.
export const cash = {
  accounts: () => api.get('/cash/accounts'),
  updateAccount: (login, data) => api.patch(`/cash/accounts/${login}`, data),
  sync: (login) => api.post('/cash/sync', null, { params: login ? { login } : {} }),
  summary: (login) => api.get('/cash/summary', { params: { login } }),
  ledger: (login, params) => api.get('/cash/ledger', { params: { login, ...params } }),
  periods: (login, grain) => api.get('/cash/periods', { params: { login, grain } }),
  reconciliation: (login, balance) => api.get('/cash/reconciliation',
    { params: { login, ...(balance ? { balance } : {}) } }),
  review: (login) => api.get('/cash/review', { params: login ? { login } : {} }),
  reclassify: (id, data) => api.post(`/cash/review/${id}`, data),
  curves: (login) => api.get('/cash/curves', { params: login ? { login } : {} }),
};

const INV = '/admin/investors';
export const inv = {
  overview: () => api.get(`${INV}/overview`),
  list: (params) => api.get(INV, { params }),
  create: (data) => api.post(INV, data),
  get: (id) => api.get(`${INV}/${id}`),
  update: (id, data) => api.patch(`${INV}/${id}`, data),
  recordDeposit: (id, data) => api.post(`${INV}/${id}/deposits`, data),
  setTerms: (id, data) => api.put(`${INV}/${id}/terms`, data),
  correct: (id, data) => api.post(`${INV}/${id}/corrections`, data),
  closureQuote: (id) => api.get(`${INV}/${id}/closure`),
  loginLink: (id, purpose, send = false) => api.post(`${INV}/${id}/login-link`, { purpose, send }),
  statementUrl: (id, year, month) => `${INV}/statements/${id}/${year}-${month}.pdf`,
  statementPdf: (id, year, month) => api.get(`${INV}/statements/${id}/${year}-${month}.pdf`, { responseType: 'blob' }),
  sendStatements: (data) => api.post(`${INV}/statements/send`, data),
  emails: (params) => api.get(`${INV}/audit/emails`, { params }),
  requestClosure: (id, data) => api.post(`${INV}/${id}/closure/request`, data || {}),
  cancelClosure: (id) => api.post(`${INV}/${id}/closure/cancel`),
  approveClosure: (id, data) => api.post(`${INV}/${id}/closure/approve`, data),
  depositQueue: (state) => api.get(`${INV}/queues/deposits`, { params: state ? { state } : {} }),
  confirmDeposit: (id, data) => api.post(`${INV}/deposits/${id}/confirm`, data),
  rejectDeposit: (id, data) => api.post(`${INV}/deposits/${id}/reject`, data),
  withdrawalQueue: (state) => api.get(`${INV}/queues/withdrawals`, { params: state ? { state } : {} }),
  approveWithdrawal: (id, data) => api.post(`${INV}/withdrawals/${id}/approve`, data || {}),
  payWithdrawal: (id, data) => api.post(`${INV}/withdrawals/${id}/pay`, data),
  declineWithdrawal: (id, data) => api.post(`${INV}/withdrawals/${id}/decline`, data),
  closureQueue: () => api.get(`${INV}/queues/closures`),
  navHistory: () => api.get(`${INV}/nav/history`),
  snapshot: (data) => api.post(`${INV}/nav/snapshot`, data),
  settings: () => api.get(`${INV}/settings/current`),
  changeSettings: (data) => api.post(`${INV}/settings`, data),
  // The legal identity every outward surface renders from: email footers, the
  // privacy policy, the terms. Versioned, so a save writes a new row.
  company: () => api.get(`${INV}/company`),
  saveCompany: (data) => api.post(`${INV}/company`, data),
  disclosures: (state) => api.get(`${INV}/disclosures`, { params: { state } }),
  publishTrade: (tradeId, data) => api.post(`${INV}/disclosures/${tradeId}/publish`, data || {}),
  hideTrade: (tradeId, data) => api.post(`${INV}/disclosures/${tradeId}/hide`, data || {}),
  disclosurePreview: () => api.get(`${INV}/disclosures/preview`),
  disclosureSplit: (id) => api.get(`${INV}/disclosures/${id}/split`),
  reconciliation: (poolEquity) => api.get(`${INV}/reconciliation`,
    { params: poolEquity ? { pool_equity: poolEquity } : {} }),
  auditLog: (params) => api.get(`${INV}/audit/log`, { params }),
  adjustments: (params) => api.get(`${INV}/audit/adjustments`, { params }),
  feePreview: (year, month) => api.get(`${INV}/fees/preview`, { params: { year, month } }),
  closeFees: (data) => api.post(`${INV}/fees/close`, data),
  feesPaid: (data) => api.post(`${INV}/fees/paid`, data),
  feePeriods: () => api.get(`${INV}/fees/periods`),
  applications: (status) => api.get(`${INV}/applications/list`, { params: status ? { status } : {} }),
  applicationStatus: (id, status) => api.post(`${INV}/applications/${id}/status`, { status }),
  acceptApplication: (id) => api.post(`${INV}/applications/${id}/accept`),
  releases: () => api.get(`${INV}/releases/list`),
  // an APK can be tens of MB: no JSON content type, a long timeout, progress reported
  uploadRelease: (form, onProgress) => api.post(`${INV}/releases`, form, {
    headers: { 'Content-Type': 'multipart/form-data' }, timeout: 15 * 60 * 1000,
    onUploadProgress: (e) => onProgress?.(e.total ? Math.round((e.loaded / e.total) * 100) : null),
  }),
  setCurrentRelease: (id) => api.post(`${INV}/releases/${id}/current`),
};

export default api;

// ── RL agents (W4) ─────────────────────────────────────────────────────────
//
// Admin-only on the server, enforced by `require_admin` on the router — these
// bindings are a convenience, not the access control.
//
// `approveCharter` is the one that matters: it is the only way an agent ever
// reaches a live slot, and the server refuses it unless a validation report
// exists, so the button cannot sign off an intention.
const RL = '/rl';
export const rl = {
  features: () => api.get(`${RL}/features`),
  strategies: () => api.get(`${RL}/strategies`),
  // What a spec WOULD be, for a form that has not been submitted. One
  // source of wording for the create screen and the saved agent.
  describe: (data) => api.post(`${RL}/describe`, data),
  leakcheck: () => api.get(`${RL}/leakcheck`),

  list: (params) => api.get(`${RL}/agents`, { params }),
  create: (data) => api.post(`${RL}/agents`, data),
  get: (id) => api.get(`${RL}/agents/${id}`),
  clone: (id, data) => api.post(`${RL}/agents/${id}/clone`, data),

  // A dataset is validated hard on upload: a file whose intervals do not match
  // its timeframe, or whose highs do not contain its closes, comes back as a
  // 400 with the reason rather than being quietly repaired.
  upload: (file, params) => {
    const body = new FormData();
    body.append('file', file);
    return api.post(`${RL}/datasets/upload`, body, {
      params, headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  overlap: (data) => api.post(`${RL}/overlap`, data),

  train: (id, data) => api.post(`${RL}/agents/${id}/train`, data),
  progress: (id) => api.get(`${RL}/agents/${id}/progress`),
  cancel: (id) => api.post(`${RL}/agents/${id}/cancel`),
  // For a run whose process died: the row still says TRAINING and
  // nothing else ever writes it back.
  resetTraining: (id) => api.post(`${RL}/agents/${id}/reset-training`),
  collect: (id) => api.post(`${RL}/agents/${id}/collect`),
  distil: (id, data) => api.post(`${RL}/agents/${id}/distil`, data),
  validate: (id, data) => api.post(`${RL}/agents/${id}/validate`, data),

  propose: (id) => api.post(`${RL}/agents/${id}/charter/propose`),
  approveCharter: (id, data = {}) => api.post(`${RL}/agents/${id}/charter/approve`, data),
  rejectCharter: (id, data) => api.post(`${RL}/agents/${id}/charter/reject`, data),
  approveProposal: (pid) => api.post(`${RL}/proposals/${pid}/approve`),
  rejectProposal: (pid, data) => api.post(`${RL}/proposals/${pid}/reject`, data),

  adherence: (id, days) => api.get(`${RL}/agents/${id}/adherence`, { params: { days } }),
  unsuspend: (id) => api.post(`${RL}/agents/${id}/unsuspend`),
  suspensions: () => api.get(`${RL}/suspensions`),
};
