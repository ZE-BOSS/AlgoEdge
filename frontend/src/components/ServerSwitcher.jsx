import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { AlertTriangle, Check, Plus, Server, Trash2, X } from 'lucide-react';
import {
  DEMO, LIVE, ROLE_COLOUR, activeServerId, clearServerAuth, getServerUser,
  isSignedIn, listServers, saveServers, setActiveServerId, storeServerAuth,
} from '../services/servers';

/**
 * Which backend am I looking at, and is it the one I think it is?
 *
 * The whole risk of running several servers from one front end is acting on
 * the wrong box, so three things are always on screen: the active server's
 * name, its role colour, and whether the server AGREES with how the registry
 * describes it.
 *
 * That last one matters more than it sounds. A registry row is a list someone
 * typed; a row labelled "Demo" pointing at the live machine is a mislabelling
 * no amount of client-side care prevents. `/api/health/identity` makes the
 * server state its own role and MT5 login, and a disagreement is shown here
 * rather than discovered by placing a trade.
 */

const HEALTH_MS = 20000;

// Room the panel wants below the button before it gives up and opens upward.
const WANTS_PX = 280;
const PANEL_PX = 340;
const GAP_PX = 6;

/**
 * Where to draw a panel hanging off `ref`, in viewport coordinates.
 *
 * `position: absolute; top: 110%; right: 0` is what this used to be, and it
 * was wrong in both directions for the only place it is mounted. The switcher
 * lives in the sidebar FOOTER, so "110% from the top of the button" is below
 * the bottom of the screen — the panel was there, you just could not see it.
 * And a 340px panel anchored to the right edge of a 240px sidebar hangs 100px
 * off the left of the window.
 *
 * So it is measured rather than assumed: fixed positioning from the button's
 * own rect, flipped upward when there is no room below, clamped to the
 * viewport on both axes, and capped in height with its own scrollbar. Fixed
 * rather than absolute also means no ancestor's `overflow` can clip it.
 */
function useAnchoredPanel(ref, showing) {
  const [pos, setPos] = useState(null);

  const place = useCallback(() => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(PANEL_PX, window.innerWidth - 16);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    const below = window.innerHeight - r.bottom - GAP_PX * 2;
    const above = r.top - GAP_PX * 2;
    const up = below < WANTS_PX && above > below;
    setPos({
      position: 'fixed', left, width,
      ...(up ? { bottom: window.innerHeight - r.top + GAP_PX }
        : { top: r.bottom + GAP_PX }),
      maxHeight: Math.max(160, up ? above : below),
      overflowY: 'auto',
    });
  }, [ref]);

  useLayoutEffect(() => {
    if (!showing) return undefined;
    place();
    // `true` so a scroll in any ancestor re-places it, not only the window's.
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    // A window resize does not move a fixed-width sidebar's button, so the
    // resize listener alone misses the case that actually moves it: the
    // sidebar collapsing from 240px to 64px.
    const ro = new ResizeObserver(place);
    if (ref.current) ro.observe(ref.current);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      ro.disconnect();
    };
  }, [showing, place, ref]);

  return pos;
}

function rolePill(role) {
  const colour = ROLE_COLOUR[role] || ROLE_COLOUR.unknown;
  return (
    <span style={{
      fontSize: '0.62rem', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase',
      color: colour, border: `1px solid ${colour}`, borderRadius: 4, padding: '1px 5px',
    }}>{role || 'unset'}</span>
  );
}

/** A disagreement between what the registry claims and what the server says. */
function mismatch(server, identity) {
  if (!identity) return null;
  if (identity.role && server.role && identity.role !== server.role) {
    return `this server calls itself "${identity.role}", the list says "${server.role}"`;
  }
  if (server.expectMt5Account && identity.mt5_account
      && Number(server.expectMt5Account) !== Number(identity.mt5_account)) {
    return `MT5 account ${identity.mt5_account}, expected ${server.expectMt5Account}`;
  }
  return null;
}

function SignIn({ server, onDone }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const go = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      // Straight to that server, not through the shared client: this is how
      // the token for THIS server is obtained in the first place.
      const base = server.baseUrl.replace(/\/$/, '');
      const { data } = await axios.post(`${base}/api/auth/login`, { email, password });
      storeServerAuth(server.id, data.access_token, data.refresh_token, data.user);
      onDone();
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not sign in');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={go} style={{ display: 'grid', gap: 6, marginTop: 8 }}>
      <div className="inv-hint">
        Each server signs its own tokens, so signing in to one does not sign you
        in to the others.
      </div>
      <input type="email" placeholder="Email" value={email} autoComplete="username"
             onChange={(e) => setEmail(e.target.value)} />
      <input type="password" placeholder="Password" value={password}
             autoComplete="current-password"
             onChange={(e) => setPassword(e.target.value)} />
      <button className="btn btn-primary btn-sm" disabled={busy || !email || !password}>
        {busy ? 'Signing in…' : `Sign in to ${server.label}`}
      </button>
      {error && <div style={{ color: 'var(--red)', fontSize: '0.78rem' }}>{error}</div>}
    </form>
  );
}

function AddServer({ onAdd, onCancel }) {
  const [form, setForm] = useState({ label: '', baseUrl: '', role: DEMO });
  const ok = form.label.trim() && form.baseUrl.trim();
  return (
    <div style={{ display: 'grid', gap: 6, padding: 10, borderTop: '1px solid var(--border)' }}>
      <input placeholder="Name, e.g. Demo VPS" value={form.label}
             onChange={(e) => setForm({ ...form, label: e.target.value })} />
      <input placeholder="https://api.example.com/s/demo" value={form.baseUrl}
             onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} />
      <div className="inv-hint">
        A page served over HTTPS cannot call an <code>http://</code> address —
        browsers block it. Behind the live domain, use the proxied path
        (<code>/s/&lt;name&gt;</code>); on a laptop, <code>http://localhost:8000</code>.
      </div>
      <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
        <option value={DEMO}>Demo — safe to experiment</option>
        <option value={LIVE}>Live — real money</option>
      </select>
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="btn btn-primary btn-sm" disabled={!ok}
                onClick={() => onAdd({ ...form, id: form.label.trim().toLowerCase().replace(/\W+/g, '-') })}>
          Add
        </button>
        <button className="btn btn-secondary btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function ServerSwitcher() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [servers, setServers] = useState(listServers);
  const [activeId, setActiveId] = useState(activeServerId);
  const anchor = useRef(null);
  const panel = useRef(null);

  useEffect(() => {
    const sync = () => { setServers(listServers()); setActiveId(activeServerId()); };
    window.addEventListener('servers-changed', sync);
    return () => window.removeEventListener('servers-changed', sync);
  }, []);

  // A dropdown that covers the whole nav and only closes from the button it
  // came out of is a trap. Escape and a click anywhere else both close it.
  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => {
      if (panel.current?.contains(e.target) || anchor.current?.contains(e.target)) return;
      setOpen(false);
    };
    const key = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', key);
    };
  }, [open]);

  // Health is asked of EVERY server, not only the active one: the point of
  // the strip is to see at a glance that demo is up before switching to it.
  // Unauthenticated on purpose — identity must be answerable before sign-in.
  const health = useQueries({
    queries: servers.map((s) => ({
      queryKey: ['server-identity', s.id, s.baseUrl],
      queryFn: () => axios
        .get(`${s.baseUrl.replace(/\/$/, '')}/api/health/identity`, { timeout: 8000 })
        .then((r) => r.data),
      refetchInterval: HEALTH_MS,
      retry: false,
    })),
  });

  const active = servers.find((s) => s.id === activeId) || servers[0];
  const activeHealth = health[servers.indexOf(active)]?.data;
  const activeMismatch = active && mismatch(active, activeHealth);
  const colour = ROLE_COLOUR[active?.role] || ROLE_COLOUR.unknown;
  // Measured whenever a panel is about to be shown, and kept above the early
  // return below because a hook cannot be conditional. Measuring once at
  // mount instead would go stale the moment the sidebar collapsed.
  const place = useAnchoredPanel(anchor, Boolean(open || activeMismatch));

  const switchTo = (id) => {
    setActiveServerId(id);
    // Cancel BEFORE clearing. A request already in flight to the old server
    // resolves into the cache after the clear, and then sits under the new
    // server's name — the exact failure the clear exists to prevent, arriving
    // a few hundred milliseconds later.
    qc.cancelQueries();
    qc.clear();
    setOpen(false);
  };

  if (!active) return null;

  return (
    <div style={{ position: 'relative' }}>
      <button ref={anchor} className="btn btn-secondary btn-sm"
              aria-expanded={open} aria-haspopup="menu"
              onClick={() => setOpen((v) => !v)}
              style={{
                borderColor: colour, color: colour, gap: 6,
                // The sidebar is 240px and collapses to 64px. Without this the
                // label and both pills push the button wider than its own
                // column and the row spills over the edge.
                maxWidth: '100%', overflow: 'hidden',
              }}>
        <Server size={13} />
        {/* Hidden in the collapsed rail, the same way `.nav-link span` and
            `.user-email` are. The icon and the warning triangle stay: a
            64px rail still has to be able to say "this is the wrong
            server". */}
        <span className="switcher-label">
          {active.label}
          {rolePill(active.role)}
        </span>
        {activeMismatch && <AlertTriangle size={13} color="var(--red)" />}
        {!isSignedIn(active.id) && (
          <span className="inv-hint switcher-label" style={{ fontSize: '0.65rem' }}>
            signed out
          </span>
        )}
      </button>

      {activeMismatch && (
        <div style={{
          ...place, zIndex: 60,
          background: 'var(--bg-card)', border: '1px solid var(--red)', borderRadius: 8,
          padding: 10, fontSize: '0.76rem', lineHeight: 1.5,
        }}>
          <AlertTriangle size={13} color="var(--red)" style={{ verticalAlign: -2 }} />{' '}
          <strong>This is not the server the list describes.</strong>
          <div style={{ marginTop: 4 }}>{activeMismatch}</div>
        </div>
      )}

      {open && (
        <div ref={panel} role="menu" style={{
          ...place, zIndex: 50,
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 8, boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
        }}>
          {servers.map((s, i) => {
            const h = health[i];
            const bad = mismatch(s, h?.data);
            const up = !!h?.data;
            return (
              <div key={s.id} style={{
                padding: 10,
                borderBottom: '1px solid var(--border-subtle)',
                background: s.id === activeId ? 'var(--bg-hover)' : undefined,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    width: 7, height: 7, borderRadius: '50%',
                    background: up ? 'var(--text-accent)' : 'var(--red)',
                  }} />
                  <button className="btn btn-sm"
                          style={{ flex: 1, justifyContent: 'flex-start', background: 'none',
                                   border: 'none', color: 'var(--text-primary)' }}
                          onClick={() => switchTo(s.id)}>
                    {s.label} {rolePill(s.role)}
                    {s.id === activeId && <Check size={12} style={{ marginLeft: 4 }} />}
                  </button>
                  {servers.length > 1 && (
                    <button className="btn btn-sm" title="Remove from the list"
                            style={{ background: 'none', border: 'none', color: 'var(--text-muted)' }}
                            onClick={() => {
                              clearServerAuth(s.id);
                              const rest = servers.filter((x) => x.id !== s.id);
                              saveServers(rest);
                              if (s.id === activeId) switchTo(rest[0].id);
                            }}>
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
                <div className="inv-hint" style={{ fontSize: '0.68rem', marginTop: 2 }}>
                  {s.baseUrl}
                  {h?.data?.mt5_account ? ` · MT5 ${h.data.mt5_account}` : ''}
                  {h?.data?.commit ? ` · ${h.data.commit}` : ''}
                  {h?.data?.bot_running ? ' · bot running' : ''}
                  {!up && ' · unreachable'}
                </div>
                {bad && (
                  <div style={{ color: 'var(--red)', fontSize: '0.7rem', marginTop: 3 }}>
                    <AlertTriangle size={11} style={{ verticalAlign: -1 }} /> {bad}
                  </div>
                )}
                {isSignedIn(s.id) ? (
                  <div className="inv-hint" style={{ fontSize: '0.68rem', marginTop: 3 }}>
                    Signed in as {getServerUser(s.id)?.email || 'you'}
                    <button className="btn btn-sm"
                            style={{ background: 'none', border: 'none', color: 'var(--text-muted)' }}
                            onClick={() => clearServerAuth(s.id)}>
                      <X size={11} /> sign out
                    </button>
                  </div>
                ) : up ? <SignIn server={s} onDone={() => switchTo(s.id)} /> : null}
              </div>
            );
          })}

          {adding ? (
            <AddServer onCancel={() => setAdding(false)}
                       onAdd={(s) => { saveServers([...servers, s]); setAdding(false); }} />
          ) : (
            <button className="btn btn-sm"
                    style={{ width: '100%', background: 'none', border: 'none',
                             color: 'var(--text-secondary)', padding: 9 }}
                    onClick={() => setAdding(true)}>
              <Plus size={12} /> Add a server
            </button>
          )}
        </div>
      )}
    </div>
  );
}
