/**
 * Signal Groups: the General / VIP / VVIP Telegram groups the group bot runs.
 *
 * Groups   - every chat the bot has been added to; set its tier, switch posting on.
 * Routes   - which strategy or copy channel feeds which group (admin only: members
 *            never see a source), with sampling, delay and how much of each trade's
 *            life is posted.
 * Feed     - what was posted, queued or failed.
 * Drafts   - LLM posts waiting for approval.
 * Members  - VIP requests and active members; VVIP clients and payments.
 * Settings - persona, schedule, VIP rules, links.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, CheckCircle2, Megaphone, Pin, Plus, RefreshCw, Send, Settings2, Trash2, Users, X } from 'lucide-react';

import { groupsApi } from '../services/api';
import { qk } from '../services/servers';
import './CopyTrading.css';

function apiError(e) {
  const said = e?.response?.data?.detail;
  if (said) return typeof said === 'string' ? said : JSON.stringify(said);
  if (e?.response) return `The server returned ${e.response.status}.`;
  return 'The request never reached the server.';
}

const when = (iso) => (iso ? new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString() : '-');
const TIERS = ['GENERAL', 'VIP', 'VVIP'];
const PILL = { SENT: 'green', QUEUED: 'blue', FAILED: 'red', PENDING: 'yellow', ACTIVE: 'green', WARNED: 'yellow',
  DENIED: 'muted', REMOVED: 'muted', POSTED: 'green', SKIPPED: 'muted', OPEN: 'blue', CLOSED: 'muted',
  CANCELLED: 'muted', WITHDRAWN: 'muted', NEW: 'muted', ASKED: 'yellow', PAID: 'green' };

const Pill = ({ s, children }) => <span className={`ct-pill ct-pill--${PILL[s] || 'muted'}`}>{children || s}</span>;

function Switch({ checked, onChange, disabled, label }) {
  return (
    <label className="ct-switch">
      <input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="ct-switch__track" />
      {label && <span className="ct-switch__text">{label}</span>}
    </label>
  );
}

function Card({ icon, title, sub, actions, children }) {
  return (
    <section className="ct-card">
      <header className="ct-card__head">
        <span className="ct-card__icon">{icon}</span>
        <div style={{ minWidth: 0 }}>
          <h3 className="ct-card__title">{title}</h3>
          {sub && <p className="ct-card__sub">{sub}</p>}
        </div>
        {actions && <div className="ct-card__actions">{actions}</div>}
      </header>
      <div className="ct-card__body">{children}</div>
    </section>
  );
}

const Field = ({ label, hint, children }) => (
  <div className="ct-field">
    {label && <label>{label}</label>}
    {children}
    {hint && <p className="ct-hint">{hint}</p>}
  </div>
);

export default function Groups() {
  const qc = useQueryClient();
  const [tab, setTab] = useState('groups');
  const [msg, setMsg] = useState(null);
  const status = useQuery({ queryKey: qk('groups', 'status'), queryFn: () => groupsApi.status().then((r) => r.data), refetchInterval: 15000 });
  const refresh = () => qc.invalidateQueries({ queryKey: qk('groups') });
  const act = (fn, ok) => ({
    mutationFn: fn,
    onSuccess: (r) => { setMsg({ ok: true, text: typeof ok === 'function' ? ok(r?.data) : ok }); refresh(); },
    onError: (e) => setMsg({ ok: false, text: apiError(e) }),
  });

  const d = status.data;
  const bot = d?.bot || {};
  return (
    <div className="ct-page">
      <header className="ct-header">
        <div>
          <h2><Megaphone size={20} /> Signal Groups</h2>
          <p>The group bot posts signals from the strategies and copy channels you route to each group, follows every
            trade to its result, and runs the VIP join chat. Members never see where a signal came from.</p>
          <div className="ct-status">
            {status.isLoading ? <Pill s="QUEUED">Loading…</Pill> : status.isError
              ? <Pill s="FAILED">Could not load: {apiError(status.error)}</Pill> : !bot.configured
              ? <Pill s="FAILED">The group bot runs on the Live server - switch to Live (bottom left)</Pill>
              : <Pill s={bot.running ? 'SENT' : 'FAILED'}>{bot.running ? `@${bot.username || 'bot'} running` : 'Bot stopped'}</Pill>}
            {d && <Pill s="QUEUED">{d.queued} queued</Pill>}
            {d?.failed_24h > 0 && <Pill s="FAILED">{d.failed_24h} failed today</Pill>}
            {bot.last_error && <span className="ct-pill ct-pill--yellow" title={bot.last_error}>Last error: {bot.last_error.slice(0, 60)}</span>}
          </div>
        </div>
        <button className="btn btn-sm btn-secondary" onClick={refresh}><RefreshCw size={14} /> Refresh</button>
      </header>

      {bot.configured && !(d?.settings?.admin_ids || []).length && (
        <div className="ct-note ct-note--warn">
          Link your Telegram account as the bot's admin: open <b>@{bot.username || 'the bot'}</b> in Telegram and send{' '}
          <code className="ct-mono">/claim {bot.claim_code}</code>. You'll then get join requests, drafts and alerts there.
        </div>
      )}
      {msg && <div className={`ct-note ${msg.ok ? 'ct-note--ok' : 'ct-note--err'}`} onClick={() => setMsg(null)}>{msg.text}</div>}

      <div className="ct-tabs">
        {[['groups', 'Groups'], ['routes', 'Routes'], ['feed', 'Feed'], ['drafts', 'Drafts'], ['members', 'Members'], ['vvip', 'VVIP'], ['orderflow', 'Order flow'], ['settings', 'Settings']].map(([k, l]) => (
          <button key={k} className={tab === k ? 'is-on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === 'groups' && <GroupsTab d={d} act={act} />}
      {tab === 'routes' && <RoutesTab d={d} act={act} />}
      {tab === 'feed' && <FeedTab d={d} />}
      {tab === 'drafts' && <DraftsTab act={act} />}
      {tab === 'members' && <MembersTab act={act} />}
      {tab === 'vvip' && <VvipTab act={act} />}
      {tab === 'orderflow' && <OrderFlowTab />}
      {tab === 'settings' && d && <SettingsTab settings={d.settings} act={act} />}
    </div>
  );
}

function GroupsTab({ d, act }) {
  const patch = useMutation(act(({ id, data }) => groupsApi.patchGroup(id, data), 'Saved.'));
  const pin = useMutation(act((id) => groupsApi.post({ group_id: Number(id), text: 'RISK_PIN', pin: true }), 'Risk notice posted and pinned.'));
  const [text, setText] = useState({});
  const post = useMutation(act(({ id, t }) => groupsApi.post({ group_id: Number(id), text: t }), 'Posted.'));
  const groups = d?.groups || [];
  const [nf, setNf] = useState({ title: '', tier: 'VIP', broadcast: false });
  const [made, setMade] = useState(null);
  const create = useMutation(act((data) => groupsApi.createGroup(data), (r) => {
    setMade(r);
    return `Created "${r?.title}" with the bot as admin. Set posting on when its routes are ready.`;
  }));
  return (
    <>
    <Card icon={<Plus size={16} />} title="Create a group" sub="Made with the Telegram account logged in on the Copy Trading page. The bot is added as admin and the group is recorded with its tier.">
      <form style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end' }}
        onSubmit={(e) => { e.preventDefault(); create.mutate(nf); setNf({ ...nf, title: '' }); }}>
        <Field label="Name"><input className="ct-input" required placeholder="AlphaEdge VIP" value={nf.title} onChange={(e) => setNf({ ...nf, title: e.target.value })} /></Field>
        <Field label="Tier">
          <select className="ct-select" value={nf.tier} onChange={(e) => setNf({ ...nf, tier: e.target.value })}>
            {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="Type">
          <select className="ct-select" value={nf.broadcast ? 'channel' : 'group'} onChange={(e) => setNf({ ...nf, broadcast: e.target.value === 'channel' })}>
            <option value="group">Group (members can chat)</option>
            <option value="channel">Channel (only you and the bot post)</option>
          </select>
        </Field>
        <div className="ct-field"><button className="btn btn-primary" disabled={create.isPending}><Plus size={14} /> {create.isPending ? 'Creating…' : 'Create'}</button></div>
      </form>
      {made?.invite_link && <p className="ct-hint">Invite link for {made.title}: <span className="ct-mono">{made.invite_link}</span> (members of VIP and VVIP get their own one-use links from the bot).</p>}
    </Card>
    <Card icon={<Users size={16} />} title="Groups" sub="Add the bot to a group as an admin and it appears here. New groups start with posting off.">
      {!groups.length ? <p className="ct-empty">No groups yet. In Telegram, add the bot to your group and make it an admin.</p> : (
        <table className="ct-table">
          <thead><tr><th>Group</th><th>Bot</th><th>Tier</th><th>Posting</th><th>Post as the bot</th><th /></tr></thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id}>
                <td><b style={{ color: 'var(--text-primary)' }}>{g.title || '(untitled)'}</b><div className="ct-mono ct-hint">{g.chat_id} · {g.chat_type}</div></td>
                <td>
                  <Pill s={g.bot_status === 'administrator' ? 'SENT' : 'FAILED'}>{g.bot_status || '?'}</Pill>
                  <div className="ct-hint">{[!g.can_post && 'no post', !g.can_invite && 'no invite', !g.can_delete && 'no delete'].filter(Boolean).join(', ') || 'all rights'}</div>
                </td>
                <td>
                  <select className="ct-select ct-select--sm" value={g.tier || ''} onChange={(e) => patch.mutate({ id: g.id, data: { tier: e.target.value } })}>
                    <option value="">Choose…</option>
                    {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td><Switch checked={g.posting} disabled={!g.tier} onChange={(v) => patch.mutate({ id: g.id, data: { posting: v } })} /></td>
                <td>
                  <form style={{ display: 'flex', gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (text[g.id]) { post.mutate({ id: g.id, t: text[g.id] }); setText({ ...text, [g.id]: '' }); } }}>
                    <input className="ct-input" placeholder="Message" value={text[g.id] || ''} onChange={(e) => setText({ ...text, [g.id]: e.target.value })} />
                    <button className="ct-iconbtn" title="Send" disabled={!text[g.id]}><Send size={14} /></button>
                  </form>
                </td>
                <td><button className="btn btn-sm btn-secondary" onClick={() => pin.mutate(g.id)} title="Post and pin the risk notice"><Pin size={13} /> Risk pin</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
    </>
  );
}

const EMPTY_ROUTE = { group_id: '', source: '', symbol: '', sample_every: 1, delay_s: 0, lifecycle: 'full', headsups: true, max_losing_streak: 5 };

function RoutesTab({ d, act }) {
  const sources = useQuery({ queryKey: qk('groups', 'sources'), queryFn: () => groupsApi.sources().then((r) => r.data) });
  const [f, setF] = useState(EMPTY_ROUTE);
  const add = useMutation(act((data) => groupsApi.addRoute(data), 'Route added.'));
  const patch = useMutation(act(({ id, data }) => groupsApi.patchRoute(id, data), 'Saved.'));
  const del = useMutation(act((id) => groupsApi.deleteRoute(id), 'Route removed.'));
  const groups = useMemo(() => d?.groups || [], [d]);
  const gname = useMemo(() => Object.fromEntries(groups.map((g) => [g.id, `${g.tier || '?'} · ${g.title}`])), [groups]);
  const sname = useMemo(() => {
    const m = {};
    (sources.data?.channels || []).forEach((c) => { m[`channel:${c.id}`] = `Channel: ${c.title}`; });
    (sources.data?.strategies || []).forEach((s) => { m[`strategy:${s.strategy_id}`] = `Strategy: ${s.strategy_id}`; });
    return m;
  }, [sources.data]);
  const submit = (e) => {
    e.preventDefault();
    const [source_type, ...rest] = f.source.split(':');
    add.mutate({ ...f, group_id: Number(f.group_id), source_type, source_key: rest.join(':'), symbol: f.symbol || null,
      sample_every: Number(f.sample_every), delay_s: Number(f.delay_s), max_losing_streak: Number(f.max_losing_streak) });
    setF(EMPTY_ROUTE);
  };
  const strategies = [...new Set((sources.data?.strategies || []).map((s) => s.strategy_id))];
  return (
    <>
      <Card icon={<Plus size={16} />} title="Add a route" sub="A route sends one source's signals to one group. General is usually a sampled, delayed copy of VIP.">
        <form className="ct-grid" onSubmit={submit} style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', display: 'grid', gap: 12 }}>
          <Field label="Group">
            <select className="ct-select" required value={f.group_id} onChange={(e) => setF({ ...f, group_id: e.target.value })}>
              <option value="">Choose…</option>
              {groups.filter((g) => g.tier).map((g) => <option key={g.id} value={g.id}>{gname[g.id]}</option>)}
            </select>
          </Field>
          <Field label="Source">
            <select className="ct-select" required value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>
              <option value="">Choose…</option>
              <optgroup label="Strategies">{strategies.map((s) => <option key={s} value={`strategy:${s}`}>{s}</option>)}</optgroup>
              <optgroup label="Copy channels">{(sources.data?.channels || []).map((c) => <option key={c.id} value={`channel:${c.id}`}>{c.title}{c.copying ? '' : ' (not copying)'}</option>)}</optgroup>
            </select>
          </Field>
          <Field label="Only symbol" hint="Blank = all"><input className="ct-input" placeholder="XAUUSD" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} /></Field>
          <Field label="Post every Nth" hint="1 = every signal"><input className="ct-input" type="number" min="1" value={f.sample_every} onChange={(e) => setF({ ...f, sample_every: e.target.value })} /></Field>
          <Field label="Delay (seconds)"><input className="ct-input" type="number" min="0" value={f.delay_s} onChange={(e) => setF({ ...f, delay_s: e.target.value })} /></Field>
          <Field label="Updates">
            <select className="ct-select" value={f.lifecycle} onChange={(e) => setF({ ...f, lifecycle: e.target.value })}>
              <option value="full">Full: TPs, breakeven, result</option>
              <option value="basic">Basic: entry and result</option>
            </select>
          </Field>
          <Field label="Pause after losses" hint="0 = never"><input className="ct-input" type="number" min="0" value={f.max_losing_streak} onChange={(e) => setF({ ...f, max_losing_streak: e.target.value })} /></Field>
          <Field label="Heads-ups"><Switch checked={f.headsups} onChange={(v) => setF({ ...f, headsups: v })} label="Pre-signal alerts" /></Field>
          <div style={{ alignSelf: 'end' }}><button className="btn btn-primary" disabled={add.isPending}><Plus size={14} /> Add route</button></div>
        </form>
      </Card>
      <Card icon={<Bot size={16} />} title="Routes" sub="Only you see these. A route pauses itself after the set number of losses in a row.">
        {!(d?.routes || []).length ? <p className="ct-empty">No routes yet.</p> : (
          <table className="ct-table">
            <thead><tr><th>Source</th><th>Group</th><th>Symbol</th><th>Every</th><th>Delay</th><th>Updates</th><th>On</th><th /></tr></thead>
            <tbody>
              {d.routes.map((r) => (
                <tr key={r.id}>
                  <td>{sname[`${r.source_type}:${r.source_key}`] || `${r.source_type}: ${r.source_key}`}
                    {r.paused_reason && <div><Pill s="FAILED">Paused: {r.paused_reason}</Pill> <button className="btn btn-sm btn-secondary" onClick={() => patch.mutate({ id: r.id, data: { resume: true } })}>Resume</button></div>}
                  </td>
                  <td>{gname[r.group_id] || r.group_id}</td>
                  <td className="num">{r.symbol || 'all'}</td>
                  <td className="num">{r.sample_every}</td>
                  <td className="num">{r.delay_s}s</td>
                  <td>{r.lifecycle}{r.headsups ? ' + heads-ups' : ''}</td>
                  <td><Switch checked={r.enabled} onChange={(v) => patch.mutate({ id: r.id, data: { enabled: v } })} /></td>
                  <td><button className="ct-iconbtn ct-iconbtn--danger" title="Remove" onClick={() => del.mutate(r.id)}><Trash2 size={14} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function FeedTab({ d }) {
  const feed = useQuery({ queryKey: qk('groups', 'feed'), queryFn: () => groupsApi.feed().then((r) => r.data), refetchInterval: 10000 });
  const chat = useMemo(() => Object.fromEntries((d?.groups || []).map((g) => [g.chat_id, g.tier || g.title])), [d]);
  return (
    <Card icon={<Send size={16} />} title="Feed" sub="Newest first. Status-line edits are hidden.">
      <table className="ct-table">
        <thead><tr><th>When</th><th>Group</th><th>Kind</th><th>Status</th><th>Text</th></tr></thead>
        <tbody>
          {(feed.data?.posts || []).filter((p) => p.kind !== 'edit').map((p) => (
            <tr key={p.id}>
              <td className="ct-mono">{when(p.sent_at || p.send_at)}</td>
              <td>{chat[p.chat_id] || p.chat_id}</td>
              <td>{p.kind}</td>
              <td><Pill s={p.status} />{p.error && <div className="ct-hint">{p.error}</div>}</td>
              <td><div className="ct-trunc" style={{ maxWidth: 520 }} title={p.text.replace(/<[^>]+>/g, '')}>{p.text.replace(/<[^>]+>/g, '')}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function DraftsTab({ act }) {
  const drafts = useQuery({ queryKey: qk('groups', 'drafts'), queryFn: () => groupsApi.drafts().then((r) => r.data) });
  const decide = useMutation(act(({ id, a }) => groupsApi.decideDraft(id, a), (r) => r?.result || 'Done.'));
  return (
    <Card icon={<CheckCircle2 size={16} />} title="Drafts" sub="Posts the writer drafted. Approve here or with the buttons the bot sends you.">
      {!(drafts.data || []).length ? <p className="ct-empty">No drafts.</p> : (drafts.data || []).map((x) => (
        <div key={x.id} className="ct-msg" style={{ padding: '10px 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div className="ct-msg__meta"><Pill s={x.status} /> {x.kind} · {when(x.created_at)}</div>
          <p style={{ whiteSpace: 'pre-wrap', margin: '6px 0' }}>{x.text}</p>
          {x.status === 'PENDING' && (
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-sm btn-primary" onClick={() => decide.mutate({ id: x.id, a: 'approve' })}>Post it</button>
              <button className="btn btn-sm btn-secondary" onClick={() => decide.mutate({ id: x.id, a: 'skip' })}>Skip</button>
            </div>
          )}
        </div>
      ))}
    </Card>
  );
}

function MembersTab({ act }) {
  const members = useQuery({ queryKey: qk('groups', 'members'), queryFn: () => groupsApi.members().then((r) => r.data) });
  const decide = useMutation(act(({ id, a }) => groupsApi.decideMember(id, a), (r) => r?.result || 'Done.'));
  const patch = useMutation(act(({ id, data }) => groupsApi.patchMember(id, data), 'Saved.'));
  return (
    <Card icon={<Users size={16} />} title="Members" sub="People who used the join chat. Approving sends a one-use invite link for every group of that tier.">
      {!(members.data || []).length ? <p className="ct-empty">Nobody yet. Share the bot's link: people tap Start to join.</p> : (
        <table className="ct-table">
          <thead><tr><th>Member</th><th>Tier</th><th>MT5 login</th><th>Status</th><th>Expires</th><th /></tr></thead>
          <tbody>
            {members.data.map((m) => (
              <tr key={m.id}>
                <td>{m.name || '-'}<div className="ct-hint">@{m.username || '-'} · {m.telegram_id}</div></td>
                <td>{m.tier}{m.level ? ` ${m.level}` : ''}</td>
                <td className="num">{m.mt5_login || '-'}</td>
                <td><Pill s={m.status} /></td>
                <td className="ct-mono">{m.expires_at ? when(m.expires_at).split(',')[0] : '-'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {m.status === 'PENDING' && <>
                    <button className="btn btn-sm btn-primary" onClick={() => decide.mutate({ id: m.id, a: 'approve' })}>Approve</button>{' '}
                    <button className="btn btn-sm btn-secondary" onClick={() => decide.mutate({ id: m.id, a: 'deny' })}><X size={12} /></button>
                  </>}
                  {['ACTIVE', 'WARNED'].includes(m.status) && <button className="btn btn-sm btn-secondary" onClick={() => patch.mutate({ id: m.id, data: { days: 30, status: 'ACTIVE' } })}>+30 days</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function VvipTab({ act }) {
  const q = useQuery({ queryKey: qk('groups', 'clients'), queryFn: () => groupsApi.clients().then((r) => r.data) });
  const patch = useMutation(act(({ id, data }) => groupsApi.patchClient(id, data), 'Saved.'));
  const close = useMutation(act(({ id, s }) => groupsApi.closeRequest(id, s), 'Updated.'));
  const [inst, setInst] = useState({});
  return (
    <>
      <div className="ct-note ct-note--warn">
        Each VVIP account gets its own terminal that copies live's trades at the client's risk. To start one, run on the server:{' '}
        <code className="ct-mono">sudo bash /opt/algoedge/src/deploy/linux/add_client.sh ID</code> (ID from the table). It logs their MT5
        in, copies live's strategies and starts trading. <code className="ct-mono">--remove ID</code> stops it. Payments stay off until the
        Paystack key is set on the server.
      </div>
      <Card icon={<Bot size={16} />} title="Client accounts" sub="Risk is capped at 2% per trade, daily loss at 10%, drawdown stop at 30%, whatever is entered.">
        {!(q.data?.clients || []).length ? <p className="ct-empty">No VVIP clients yet.</p> : (
          <table className="ct-table">
            <thead><tr><th>ID</th><th>Account</th><th>Level</th><th>Risk</th><th>Limits</th><th>Live</th><th>Instance</th><th>State</th></tr></thead>
            <tbody>
              {q.data.clients.map((c) => (
                <tr key={c.id}>
                  <td className="ct-mono ct-hint">{c.id}</td>
                  <td className="num">{c.mt5_login}<div className="ct-hint">{c.broker} · {c.mt5_server}</div></td>
                  <td>{c.level}</td>
                  <td className="num">{c.risk_pct}%</td>
                  <td className="ct-hint">day {c.daily_loss_pct}% · dd {c.max_dd_pct}%</td>
                  <td>
                    {c.live?.equity != null ? <>
                      <span className="num">{Number(c.live.equity).toLocaleString()} {c.live.currency}</span>
                      <div className="ct-hint">dd {c.live.drawdown_pct}% · today {c.live.day_pnl} · {c.live.open_positions} open</div>
                    </> : <span className="ct-hint">-</span>}
                    {c.guard_state && c.guard_state !== 'ACTIVE' && <div><Pill s="FAILED">{c.guard_state}</Pill></div>}
                  </td>
                  <td>
                    <form style={{ display: 'flex', gap: 6 }} onSubmit={(e) => { e.preventDefault(); patch.mutate({ id: c.id, data: { instance: inst[c.id] ?? c.instance } }); }}>
                      <input className="ct-input ct-mono" style={{ width: 120 }} placeholder="client1" value={inst[c.id] ?? c.instance ?? ''} onChange={(e) => setInst({ ...inst, [c.id]: e.target.value })} />
                      <button className="ct-iconbtn" title="Save"><CheckCircle2 size={14} /></button>
                    </form>
                  </td>
                  <td>
                    <select className="ct-select ct-select--sm" value={c.state} onChange={(e) => patch.mutate({ id: c.id, data: { state: e.target.value } })}>
                      {['NEW', 'ACTIVE', 'PAUSED', 'STOPPED'].map((s) => <option key={s}>{s}</option>)}
                    </select>
                    {c.paused_reason && <div className="ct-hint">{c.paused_reason}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card icon={<RefreshCw size={16} />} title="Backtest requests" sub="Pro and Elite clients see the newest saved backtest of each strategy live trades (names hidden). Run what they ask for, save it, then mark it done.">
        {!(q.data?.requests || []).length ? <p className="ct-empty">No requests.</p> : (
          <table className="ct-table">
            <thead><tr><th>When</th><th>Market</th><th>Months</th><th>Note</th><th>Status</th><th /></tr></thead>
            <tbody>{q.data.requests.map((r) => (
              <tr key={r.id}><td className="ct-mono">{when(r.created_at)}</td><td>{r.market}</td><td className="num">{r.months}</td>
                <td>{r.note}</td><td><Pill s={r.status === 'OPEN' ? 'PENDING' : 'SENT'}>{r.status}</Pill></td>
                <td>{r.status === 'OPEN' && <>
                  <button className="btn btn-sm btn-primary" onClick={() => close.mutate({ id: r.id, s: 'done' })}>Done</button>{' '}
                  <button className="btn btn-sm btn-secondary" onClick={() => close.mutate({ id: r.id, s: 'declined' })}>Decline</button></>}</td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      <Card icon={<CheckCircle2 size={16} />} title="Payments">
        {!(q.data?.payments || []).length ? <p className="ct-empty">No payments yet.</p> : (
          <table className="ct-table">
            <thead><tr><th>When</th><th>Reference</th><th>Amount</th><th>Level</th><th>Status</th></tr></thead>
            <tbody>{q.data.payments.map((p) => (
              <tr key={p.id}><td className="ct-mono">{when(p.created_at)}</td><td className="ct-mono">{p.reference}</td>
                <td className="num">{Number(p.amount).toLocaleString()} {p.currency}</td><td>{p.level}</td><td><Pill s={p.status} /></td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function OrderFlowTab() {
  const q = useQuery({ queryKey: qk('groups', 'orderflow'), queryFn: () => groupsApi.orderflow().then((r) => r.data), refetchInterval: 15000 });
  const d = q.data;
  const n = (v, k = 2) => (v == null ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: k }));
  return (
    <Card icon={<RefreshCw size={16} />} title="Order flow" sub="CME futures trades from Databento: session volume profile, delta and large prints, also in the broker's CFD prices. Elite clients see the CFD levels.">
      {!d ? <p className="ct-empty">Loading…</p> : !d.enabled ? (
        <div className="ct-note ct-note--warn">Off. On the server, add <code className="ct-mono">DATABENTO_API_KEY=…</code> and <code className="ct-mono">ORDERFLOW=1</code> to
          {' '}<code className="ct-mono">/opt/algoedge/live/instance.env</code> and restart live.</div>
      ) : (
        <>
          <div className="ct-status" style={{ marginBottom: 10 }}>
            <Pill s={d.connected ? 'SENT' : 'FAILED'}>{d.connected ? 'Streaming' : 'Not connected'}</Pill>
            {d.error && <span className="ct-pill ct-pill--red">{d.error.slice(0, 80)}</span>}
          </div>
          <table className="ct-table">
            <thead><tr><th>Market</th><th>Last</th><th>POC</th><th>Value area</th><th>VWAP</th><th>Delta</th><th>30 min</th><th>Basis</th><th>Large prints</th></tr></thead>
            <tbody>{(d.markets || []).map((m) => {
              const f = m.futures || {}; const c = m.cfd_levels || {};
              return (
                <tr key={m.future}>
                  <td>{m.name}<div className="ct-hint">{m.future} → {m.cfd} · {f.session}</div></td>
                  <td className="num">{n(f.last)}<div className="ct-hint">{n(c.last)}</div></td>
                  <td className="num">{n(f.poc)}<div className="ct-hint">{n(c.poc)}</div></td>
                  <td className="num">{n(f.val)} – {n(f.vah)}<div className="ct-hint">{n(c.val)} – {n(c.vah)}</div></td>
                  <td className="num">{n(f.vwap)}<div className="ct-hint">{n(c.vwap)}</div></td>
                  <td className="num">{n(f.delta, 0)}</td>
                  <td className="num">{n(f.delta_30m, 0)}</td>
                  <td className="num">{n(m.basis)}</td>
                  <td className="ct-hint">{(f.big_prints || []).slice(-4).reverse().map((b) => `${b.side} ${b.size}@${n(b.price)}`).join(', ')}</td>
                </tr>
              );
            })}</tbody>
          </table>
          <p className="ct-hint">Top number: futures price. Below: the same level in the CFD's price (futures minus the basis).</p>
        </>
      )}
    </Card>
  );
}

function SettingsTab({ settings, act }) {
  const [s, setS] = useState(() => ({
    ...settings,
    admin_ids_text: (settings.admin_ids || []).join(', '),
    links_text: Object.entries(settings.affiliate_links || {}).map(([b, u]) => `${b} = ${u}`).join('\n'),
    faq_text: (settings.faq || []).map((x) => `${x.q} = ${x.a}`).join('\n'),
  }));
  const save = useMutation(act((patch) => groupsApi.saveSettings(patch), 'Settings saved.'));
  const set = (k) => (e) => setS({ ...s, [k]: e?.target ? e.target.value : e });
  const setSched = (k) => (e) => setS({ ...s, schedule: { ...s.schedule, [k]: e.target.value } });
  const submit = (e) => {
    e.preventDefault();
    const pairs = (t) => t.split('\n').map((l) => l.split('=')).filter((p) => p.length >= 2 && p[0].trim())
      .map(([a, ...b]) => [a.trim(), b.join('=').trim()]);
    save.mutate({
      brand: s.brand, timezone: s.timezone, persona: s.persona, approval: s.approval, schedule: s.schedule,
      moderate: s.moderate, answer_faq: s.answer_faq, content_alerts: s.content_alerts,
      vip_min_balance: Number(s.vip_min_balance), vip_months: Number(s.vip_months), vvip_period_days: Number(s.vvip_period_days),
      vvip_levels: Object.fromEntries(Object.entries(s.vvip_levels || {}).map(([k, v]) => [k, Number(v)])),
      admin_contact: s.admin_contact, portal_url: s.portal_url, llm_provider: s.llm_provider,
      admin_ids: s.admin_ids_text.split(/[\s,]+/).filter(Boolean).map(Number).filter(Boolean),
      affiliate_links: Object.fromEntries(pairs(s.links_text)),
      faq: pairs(s.faq_text).map(([q, a]) => ({ q, a })),
    });
  };
  return (
    <form onSubmit={submit} className="ct-page">
      <Card icon={<Settings2 size={16} />} title="Bot" actions={<button className="btn btn-sm btn-primary" disabled={save.isPending}>Save settings</button>}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
          <Field label="Brand"><input className="ct-input" value={s.brand || ''} onChange={set('brand')} /></Field>
          <Field label="Time zone"><input className="ct-input" value={s.timezone || ''} onChange={set('timezone')} /></Field>
          <Field label="Admin Telegram ids" hint="Or send the bot /claim CODE"><input className="ct-input ct-mono" value={s.admin_ids_text} onChange={set('admin_ids_text')} /></Field>
          <Field label="Money questions go to" hint="Shown when someone asks about payments"><input className="ct-input" placeholder="@yourname" value={s.admin_contact || ''} onChange={set('admin_contact')} /></Field>
          <Field label="Writer">
            <select className="ct-select" value={s.llm_provider} onChange={set('llm_provider')}>
              <option value="anthropic">Claude, Grok as fallback</option><option value="xai">Grok, Claude as fallback</option>
            </select>
          </Field>
          <Field label="Written posts">
            <select className="ct-select" value={s.approval} onChange={set('approval')}>
              <option value="drafts">Send me drafts to approve</option><option value="auto">Post automatically</option>
            </select>
          </Field>
        </div>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginTop: 12 }}>
          <Switch checked={s.moderate} onChange={set('moderate')} label="Delete links, phone numbers and 'DM me' posts" />
          <Switch checked={s.answer_faq} onChange={set('answer_faq')} label="Answer FAQ questions in groups" />
          <Switch checked={s.content_alerts} onChange={set('content_alerts')} label="Send me video ideas after big moments" />
        </div>
        <Field label="Persona (how the writer sounds)"><textarea className="ct-textarea" rows={4} value={s.persona || ''} onChange={set('persona')} /></Field>
      </Card>
      <Card icon={<RefreshCw size={16} />} title="Schedule" sub="Local time. Summary and weekly results are always automatic; the rest follow the setting above.">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
          {Object.entries(s.schedule || {}).map(([k, v]) => (
            <Field key={k} label={k}><input className="ct-input ct-mono" value={v} onChange={setSched(k)} /></Field>
          ))}
        </div>
      </Card>
      <Card icon={<Users size={16} />} title="Membership">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
          <Field label="VIP minimum balance ($)"><input className="ct-input" type="number" value={s.vip_min_balance} onChange={set('vip_min_balance')} /></Field>
          <Field label="VIP months per approval"><input className="ct-input" type="number" value={s.vip_months} onChange={set('vip_months')} /></Field>
          <Field label="VVIP period (days)"><input className="ct-input" type="number" value={s.vvip_period_days} onChange={set('vvip_period_days')} /></Field>
          {Object.entries(s.vvip_levels || {}).map(([k, v]) => (
            <Field key={k} label={`VVIP ${k} ($)`}><input className="ct-input" type="number" value={v} onChange={(e) => setS({ ...s, vvip_levels: { ...s.vvip_levels, [k]: e.target.value } })} /></Field>
          ))}
          <Field label="VVIP sign-up page"><input className="ct-input" value={s.portal_url || ''} onChange={set('portal_url')} /></Field>
        </div>
        <Field label="Partner links" hint="One per line: Exness = https://…"><textarea className="ct-textarea ct-mono" rows={3} value={s.links_text} onChange={set('links_text')} /></Field>
        <Field label="FAQ" hint="One per line: key words = answer. Answered when every key word is in a question."><textarea className="ct-textarea" rows={5} value={s.faq_text} onChange={set('faq_text')} /></Field>
      </Card>
    </form>
  );
}
