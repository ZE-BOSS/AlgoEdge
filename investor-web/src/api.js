// The investor API client. Plain fetch — no admin code, no admin endpoints.
//
// Tokens live under their own keys, never the admin console's, so the two apps
// cannot pick up each other's session even when served from the same machine.

const BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '') + '/api/investor';
const KEY = 'avq_investor_session';

export function getSession() {
  try { return JSON.parse(localStorage.getItem(KEY)); } catch { return null; }
}
export function setSession(s) {
  localStorage.setItem(KEY, JSON.stringify(s));
  window.dispatchEvent(new Event('avq-session'));
}
export function clearSession() {
  localStorage.removeItem(KEY);
  window.dispatchEvent(new Event('avq-session'));
}

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function detail(body, status) {
  const d = body?.detail;
  if (Array.isArray(d)) return d.map((x) => x.msg).join('; ');
  return d || (status >= 500 ? 'Something went wrong on our side. Try again shortly.' : 'Request failed');
}

let refreshing = null;
async function refresh() {
  const s = getSession();
  if (!s?.refresh_token) throw new ApiError('Sign in again', 401);
  // One refresh at a time: parallel 401s all wait on the same one.
  refreshing ||= fetch(`${BASE}/auth/refresh`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: s.refresh_token }),
  }).then(async (r) => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { clearSession(); throw new ApiError(detail(body, r.status), r.status); }
    setSession(body);
    return body;
  }).finally(() => { refreshing = null; });
  return refreshing;
}

export async function call(path, { method = 'GET', body, auth = true, params, retry = true } = {}) {
  const url = new URL(BASE + path);
  if (params) Object.entries(params).forEach(([k, v]) => v != null && v !== '' && url.searchParams.set(k, v));
  const headers = { 'Content-Type': 'application/json' };
  const s = getSession();
  if (auth && s?.access_token) headers.Authorization = `Bearer ${s.access_token}`;
  let r;
  try {
    r = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError('No connection. Check your internet and try again.', 0);
  }
  if (r.status === 401 && auth && retry && s?.refresh_token) {
    await refresh();
    return call(path, { method, body, auth, params, retry: false });
  }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && auth) clearSession();
  if (!r.ok) throw new ApiError(detail(data, r.status), r.status);
  return data;
}

/** Fetch a file with the session (a statement PDF) and hand it to the browser. */
export async function download(path, filename, retry = true) {
  const s = getSession();
  let r;
  try {
    r = await fetch(BASE + path, { headers: s?.access_token ? { Authorization: `Bearer ${s.access_token}` } : {} });
  } catch {
    throw new ApiError('No connection. Check your internet and try again.', 0);
  }
  if (r.status === 401 && retry) { await refresh(); return download(path, filename, false); }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(detail(body, r.status), r.status);
  }
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const api = {
  login: (email, password) => call('/auth/login', { method: 'POST', body: { email, password }, auth: false }),
  signup: (body) => call('/auth/signup', { method: 'POST', body, auth: false }),
  forgot: (email) => call('/auth/forgot', { method: 'POST', body: { email }, auth: false }),
  statements: () => call('/statements'),
  accept: (token, password) => call('/auth/accept', { method: 'POST', body: { token, password }, auth: false }),
  changePassword: (current_password, new_password) =>
    call('/auth/password', { method: 'POST', body: { current_password, new_password } }),
  me: () => call('/me'),
  nav: () => call('/nav'),
  activity: () => call('/activity'),
  trades: () => call('/trades'),
  performance: () => call('/performance'),
  instructions: () => call('/deposits/instructions'),
  claimDeposit: (amount, note) => call('/deposits', { method: 'POST', body: { amount, note: note || null } }),
  quote: (amount) => call('/withdrawals/quote', { params: { amount } }),
  withdraw: (amount, justification) =>
    call('/withdrawals', { method: 'POST', body: { amount, justification: justification || null } }),
  requestClosure: (reason) => call('/closure', { method: 'POST', body: { reason: reason || null } }),
};
