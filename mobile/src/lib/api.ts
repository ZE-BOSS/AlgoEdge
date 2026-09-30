import Constants from 'expo-constants';
import { Platform } from 'react-native';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { getItem, removeItem, setItem } from './storage';

// EXPO_PUBLIC_API_URL (set per build profile in eas.json) wins; then app.json's
// extra.apiUrl. A local backend is used only in development.
const configured = process.env.EXPO_PUBLIC_API_URL
  || (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl;
export const API_ROOT = (__DEV__ && !process.env.EXPO_PUBLIC_API_URL ? 'http://localhost:8000' : configured || '')
  .replace(/\/$/, '');
const BASE = `${API_ROOT}/api/investor`;
const KEY = 'avq_investor_session';

export type Session = {
  access_token: string;
  refresh_token: string;
  investor: { id: string; name: string; email: string };
};

export type NavPoint = { date: string; nav_per_unit: string; value: string | null; net_invested: string | null };

export type Trade = {
  id: string; symbol: string; direction: string; closed_on: string | null; result_pct: string | null;
  note: string | null; your_amount: string | null; your_share_pct: string | null;
};
export type TradesResponse = {
  trades: Trade[];
  summary: { your_total: string; wins: number; losses: number; win_rate_pct: string | null;
    by_symbol: { symbol: string; amount: string }[] };
};

export type Month = { month: string; start_value: string; money_in_out: string; end_value: string;
  profit: string; fund_return_pct: string | null };
export type Performance = {
  months: Month[];
  stats: null | { days_invested: number; first_deposit: string; best_month: Month; worst_month: Month;
    months_up: number; months_down: number; largest_fall_pct: string };
};

export type Prefs = {
  hide_balances: boolean; chart_range: '1M' | '3M' | '6M' | '1Y' | 'ALL'; compact_numbers: boolean;
  email: { statements: boolean; trades: boolean; live_trades: boolean; fees: boolean };
  push: { money: boolean; withdrawals: boolean; statements: boolean; trades: boolean; live_trades: boolean; fees: boolean };
};

export type Notice = { id: string; kind: string; title: string; body: string | null; link: string | null;
  created_at: string | null; read: boolean };
export type NoticeFeed = { unread: number; items: Notice[] };
export type LiveTrade = { id: string; symbol: string; direction: string; side: string; opened_at: string | null };
export type Live = { invested: boolean; trades: LiveTrade[] };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

let current: Session | null = null;
const listeners = new Set<(s: Session | null) => void>();

export function onSession(fn: (s: Session | null) => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export async function loadSession(): Promise<Session | null> {
  const raw = await getItem(KEY);
  try { current = raw ? JSON.parse(raw) : null; } catch { current = null; }
  return current;
}

export async function saveSession(s: Session | null) {
  current = s;
  if (s) await setItem(KEY, JSON.stringify(s)); else await removeItem(KEY);
  listeners.forEach((fn) => fn(s));
}

function detail(body: unknown, status: number): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).join('; ');
  if (typeof d === 'string') return d;
  return status >= 500 ? 'Something went wrong on our side. Try again shortly.' : 'Request failed';
}

let refreshing: Promise<Session> | null = null;
async function refresh(): Promise<Session> {
  if (!current?.refresh_token) throw new ApiError('Sign in again', 401);
  refreshing ||= (async () => {
    const r = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: current!.refresh_token }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { await saveSession(null); throw new ApiError(detail(body, r.status), r.status); }
    await saveSession(body as Session);
    return body as Session;
  })().finally(() => { refreshing = null; });
  return refreshing;
}

type Opts = { method?: string; body?: unknown; auth?: boolean; params?: Record<string, string | undefined> };

export async function call<T = any>(path: string, opts: Opts = {}, retry = true): Promise<T> {
  const { method = 'GET', body, auth = true, params } = opts;
  const qs = params ? Object.entries(params).filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v!)}`).join('&') : '';
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (auth && current?.access_token) headers.Authorization = `Bearer ${current.access_token}`;
  let r: Response;
  try {
    r = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError('No connection. Check your internet and try again.', 0);
  }
  if (r.status === 401 && auth && retry && current?.refresh_token) {
    await refresh();
    return call<T>(path, opts, false);
  }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && auth) await saveSession(null);
  if (!r.ok) throw new ApiError(detail(data, r.status), r.status);
  return data as T;
}

/** Download a statement PDF with the session, then open the share sheet so the
 *  investor can view it, save it to Files, or send it on. */
export async function shareStatement(year: number, month: number) {
  const name = `alphavantiq-statement-${year}-${String(month).padStart(2, '0')}.pdf`;
  const url = `${BASE}/statements/${year}-${month}.pdf`;
  const auth = async () => ({ Authorization: `Bearer ${current?.access_token ?? ''}` });
  if (Platform.OS === 'web') {
    const r = await fetch(url, { headers: await auth() });
    if (!r.ok) throw new ApiError('Could not download the statement', r.status);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click();
    return;
  }
  const dest = new File(Paths.cache, name);
  let file: File;
  try {
    file = await File.downloadFileAsync(url, dest, { headers: await auth(), idempotent: true });
  } catch {
    await refresh();                   // a stale token is the usual cause; try once more
    file = await File.downloadFileAsync(url, dest, { headers: await auth(), idempotent: true });
  }
  await Sharing.shareAsync(file.uri, { mimeType: 'application/pdf', dialogTitle: name, UTI: 'com.adobe.pdf' });
}

export const api = {
  login: (email: string, password: string) =>
    call<Session>('/auth/login', { method: 'POST', body: { email, password }, auth: false }),
  signup: (body: { name: string; email: string; phone: string | null; country: string | null; consent: boolean }) =>
    call<{ message: string }>('/auth/signup', { method: 'POST', body, auth: false }),
  forgot: (email: string) => call<{ message: string }>('/auth/forgot', { method: 'POST', body: { email }, auth: false }),
  changePassword: (current_password: string, new_password: string) =>
    call<Session>('/auth/password', { method: 'POST', body: { current_password, new_password } }),
  me: () => call('/me'),
  nav: () => call<NavPoint[]>('/nav'),
  activity: () => call('/activity'),
  trades: () => call<TradesResponse>('/trades'),
  performance: () => call<Performance>('/performance'),
  preferences: () => call<Prefs>('/preferences'),
  notifications: () => call<NoticeFeed>('/notifications'),
  readNotifications: (ids?: string[]) => call('/notifications/read', { method: 'POST', body: { ids: ids ?? null } }),
  live: () => call<Live>('/live'),
  savePreferences: (changes: Partial<Prefs>) => call<Prefs>('/preferences', { method: 'PUT', body: changes }),
  statements: () => call<{ year: number; month: number; label: string }[]>('/statements'),
  instructions: () => call('/deposits/instructions'),
  claimDeposit: (amount: string, note?: string) => call('/deposits', { method: 'POST', body: { amount, note: note || null } }),
  quote: (amount?: string) => call('/withdrawals/quote', { params: { amount } }),
  withdraw: (amount: string, justification?: string) =>
    call('/withdrawals', { method: 'POST', body: { amount, justification: justification || null } }),
  requestClosure: (reason?: string) => call('/closure', { method: 'POST', body: { reason: reason || null } }),
  registerDevice: (push_token: string, platform: string, app_version: string | null) =>
    call('/devices', { method: 'POST', body: { push_token, platform, app_version } }),
  forgetDevice: (push_token: string) => call('/devices', { method: 'DELETE', params: { push_token } }),
};

/** The current Android release, from the public API (no session needed). */
export async function latestRelease(): Promise<{ version: string; version_code: number; url: string; notes?: string } | null> {
  try {
    const r = await fetch(`${API_ROOT}/api/public/app`);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
