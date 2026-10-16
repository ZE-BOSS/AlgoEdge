/**
 * Several backends, one front end.
 *
 * WHAT THIS REPLACES
 * `api.js` had exactly one seam: a single `backend_url` in localStorage and a
 * single axios instance whose `baseURL` was mutated by `setBackendUrl`. That
 * is fine for one server and actively dangerous for two — the dashboard polls
 * live while a backtest streams from demo, and a mutated shared baseURL sends
 * one of them to the wrong box.
 *
 * So: a registry of servers, and ONE AXIOS INSTANCE PER SERVER, each holding
 * its own token. Nothing is mutated after creation.
 *
 * THREE RULES THAT ARE NOT NEGOTIABLE
 *
 * 1. A token belongs to one server. Each backend signs with its own
 *    `JWT_SECRET_KEY`, so a live token is meaningless on demo — and sharing a
 *    secret so they interoperate would make a demo compromise a live
 *    compromise. You sign in per server.
 *
 * 2. Every React Query key carries the server id. Without that, switching
 *    servers shows the previous server's cached numbers under the new
 *    server's name, which is a quiet, confident lie about a live account.
 *
 * 3. The server states its own identity (`/api/health/identity`) and the
 *    registry is checked against it. A row labelled "demo" pointing at live is
 *    a mislabelling the client cannot prevent but the server can expose.
 *
 * TRANSPORT
 * The admin site is served over HTTPS, and a page on HTTPS cannot call
 * `http://<ip>:8000` — browsers block it as mixed content and no backend
 * change fixes that. Production therefore reaches every server through one
 * origin via a path prefix (`https://api.…/s/demo`), proxied by Caddy; see
 * deploy/Caddyfile.multi. Direct `http://…` URLs still work from a local
 * development page, which is why both shapes are allowed here.
 */

const KEY = 'algoedge_servers_v1';
const ACTIVE_KEY = 'algoedge_active_server';

export const LIVE = 'live';
export const DEMO = 'demo';

// Colour is not decoration. It is the one thing visible at a glance on every
// screen, and the whole risk of this feature is acting on the wrong box.
export const ROLE_COLOUR = {
  [LIVE]: '#f0883e',      // amber: you are touching real money
  [DEMO]: '#58a6ff',      // blue: safe to experiment
  unknown: '#8b949e',
};

const DEFAULT_URL = import.meta.env.VITE_DEFAULT_BACKEND_URL || 'http://localhost:8000';

function read() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY));
    if (Array.isArray(raw) && raw.length) return raw;
  } catch { /* fall through to the seed below */ }
  return null;
}

/** Everything we know about, seeded from the single-server setup if needed. */
export function listServers() {
  const saved = read();
  if (saved) return saved;
  // Migrate the old single `backend_url` rather than losing it. Someone who
  // has been using this for months should not have to re-enter their server
  // because a feature arrived.
  const legacy = localStorage.getItem('backend_url') || DEFAULT_URL;
  return [{ id: 'live', label: 'Live', baseUrl: legacy, role: LIVE }];
}

export function saveServers(servers) {
  localStorage.setItem(KEY, JSON.stringify(servers));
  window.dispatchEvent(new CustomEvent('servers-changed'));
}

export function activeServerId() {
  const id = localStorage.getItem(ACTIVE_KEY);
  const all = listServers();
  return all.some((s) => s.id === id) ? id : all[0]?.id;
}

export function setActiveServerId(id) {
  localStorage.setItem(ACTIVE_KEY, id);
  window.dispatchEvent(new CustomEvent('servers-changed'));
}

export function getServer(id) {
  return listServers().find((s) => s.id === (id || activeServerId()));
}

export function activeServer() {
  return getServer(activeServerId());
}

export function isLive(server) {
  return (server || activeServer())?.role === LIVE;
}

export function serverColour(server) {
  return ROLE_COLOUR[(server || activeServer())?.role] || ROLE_COLOUR.unknown;
}

// ── per-server credentials ─────────────────────────────────────────────────
//
// Namespaced by server id. The un-namespaced `algoedge_token` is left alone so
// a half-migrated tab cannot find itself signed in to nothing.

const tokenKey = (id, kind) => `algoedge_${kind}_${id}`;

export const getServerToken = (id) => localStorage.getItem(tokenKey(id, 'token'));
export const getServerRefresh = (id) => localStorage.getItem(tokenKey(id, 'refresh'));
export const getServerUser = (id) => {
  try { return JSON.parse(localStorage.getItem(tokenKey(id, 'user'))); } catch { return null; }
};

export function storeServerAuth(id, access, refresh, user) {
  localStorage.setItem(tokenKey(id, 'token'), access);
  if (refresh) localStorage.setItem(tokenKey(id, 'refresh'), refresh);
  if (user) localStorage.setItem(tokenKey(id, 'user'), JSON.stringify(user));
  window.dispatchEvent(new CustomEvent('servers-changed'));
}

export function clearServerAuth(id) {
  ['token', 'refresh', 'user'].forEach((k) => localStorage.removeItem(tokenKey(id, k)));
  window.dispatchEvent(new CustomEvent('servers-changed'));
}

// The same rule requests use (`getServerToken(id) || getToken()` in api.js):
// the plain login stores the un-namespaced token, and requests to the active
// server send it. Checking only the namespaced key put "signed out" beside a
// server the page was successfully talking to.
export const isSignedIn = (id) => !!getServerToken(id)
  || (id === activeServerId() && !!localStorage.getItem('algoedge_token'));

// ── query keys ─────────────────────────────────────────────────────────────

/**
 * Prefix a React Query key with the server it belongs to.
 *
 * `qk('trades', filter)` -> `['srv', 'demo', 'trades', filter]`
 *
 * Every query that hits a backend must go through this. A key without the
 * server id is shared between servers, and the cache will happily hand demo's
 * answer to a live screen.
 */
export function qk(...parts) {
  return ['srv', activeServerId(), ...parts];
}

/** The same, for a query deliberately aimed at a named server. */
export function qkFor(id, ...parts) {
  return ['srv', id, ...parts];
}

/**
 * Ask before doing something irreversible on a LIVE server.
 *
 * Not a generic "are you sure". The point is to name the server, because the
 * mistake this guards against is not "I did not mean to start the bot" — it is
 * "I did not realise I was looking at live". So the prompt states which box,
 * and only appears for a live one; on demo it does nothing and returns true.
 *
 *   if (!confirmOnLive('Start the trading bot')) return;
 */
export function confirmOnLive(action) {
  const s = activeServer();
  if (!s || s.role !== LIVE) return true;
  // eslint-disable-next-line no-alert
  return window.confirm(
    `${action}

This is ${s.label} — a LIVE server trading real money.
`
    + `${s.baseUrl}

Continue?`,
  );
}
