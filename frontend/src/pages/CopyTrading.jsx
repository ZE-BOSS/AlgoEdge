/**
 * Copy Trading: copy signals from Telegram channels into MT5.
 *
 * The server logs in to Telegram as the USER (a bot cannot read someone else's
 * channel), watches the channels added here, and for every message:
 *   a signal   -> opens the trade, split across its TP1..TP10 by the weights below
 *   a follow-up (BE, move SL/TP, close all/part, cancel) -> applies it to the
 *               signal it replies to, or the latest one on that channel
 * Dry run (the default) reads, plans against live prices and logs, but sends
 * nothing to MT5.
 */
import { Fragment, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { History, LogOut, MessageSquare, Play, Plus, RefreshCw, Send, Settings2, Trash2, Wand2 } from 'lucide-react';

import { copytrade } from '../services/api';
import { qk } from '../services/servers';

const C = {
  dim: '#8b949e', faint: '#6e7681', green: '#3fb950', red: '#f85149', amber: '#d29922',
  blue: '#58a6ff', purple: '#bc8cff', line: '#30363d',
};
const STATUS_COLOUR = {
  EXECUTED: C.green, PARTIAL: C.amber, DRY_RUN: C.blue, SKIPPED: C.amber, ERROR: C.red,
  IGNORED: C.faint, CLOSED: C.dim, SEEN: C.dim, SUPERSEDED: C.faint,
};
const KIND_COLOUR = {
  OPEN: C.green, BREAKEVEN: C.blue, MOVE_SL: C.purple, MOVE_TP: C.purple, CLOSE: C.red, CANCEL: C.amber,
  INFO: C.dim, NONE: C.faint,
};

function apiError(e) {
  const said = e?.response?.data?.detail;
  if (said) return typeof said === 'string' ? said : JSON.stringify(said);
  if (e?.response) return `the server returned ${e.response.status}`;
  return 'the request never reached the server';
}

function Card({ title, icon, why, children, right }) {
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: why ? 4 : 10 }}>
        {icon}<b style={{ fontSize: 14 }}>{title}</b>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>{right}</div>
      </div>
      {why && <p style={{ color: C.dim, fontSize: 12, margin: '0 0 10px', lineHeight: 1.5 }}>{why}</p>}
      {children}
    </div>
  );
}

const Badge = ({ text, colour }) => (
  <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, border: `1px solid ${colour}`, color: colour,
    whiteSpace: 'nowrap' }}>{text}</span>
);

const Field = ({ label, children, hint }) => (
  <label style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12, color: C.dim, minWidth: 0 }}>
    <span title={hint}>{label}</span>{children}
  </label>
);

// ── Telegram login ──────────────────────────────────────────────────────────
function TelegramLogin({ tg, onDone }) {
  const [f, setF] = useState({ api_id: tg?.api_id || '', api_hash: '', phone: tg?.phone || '' });
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState(tg?.awaiting_code ? 'code' : 'creds');
  const [needPw, setNeedPw] = useState(false);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const send = async () => {
    setBusy(true); setMsg(null);
    try {
      const { data } = await copytrade.loginStart({ api_id: Number(f.api_id), api_hash: f.api_hash, phone: f.phone });
      setStep('code');
      setMsg({ ok: true, text: `Code sent${data.via ? ` (via ${data.via})` : ''} - check Telegram.` });
    } catch (e) { setMsg({ ok: false, text: apiError(e) }); }
    setBusy(false);
  };
  const login = async () => {
    setBusy(true); setMsg(null);
    try {
      const { data } = await copytrade.loginCode({ code: needPw ? null : code, password: password || null });
      if (data.need_password) { setNeedPw(true); setMsg({ ok: true, text: 'Two-step verification is on - enter your Telegram password.' }); }
      else if (data.ok) { setMsg({ ok: true, text: `Logged in as ${data.user}` }); onDone(); }
      else setMsg({ ok: false, text: data.error || 'login failed' });
    } catch (e) { setMsg({ ok: false, text: apiError(e) }); }
    setBusy(false);
  };

  return (
    <div>
      {step === 'creds' ? (
        <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr 160px auto', gap: 8, alignItems: 'end' }}>
          <Field label="api_id"><input value={f.api_id} onChange={(e) => setF({ ...f, api_id: e.target.value })} /></Field>
          <Field label="api_hash"><input type="password" value={f.api_hash} onChange={(e) => setF({ ...f, api_hash: e.target.value })} /></Field>
          <Field label="Phone (+234...)"><input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <button className="btn btn-primary btn-sm" disabled={busy || !f.api_id || !f.api_hash || !f.phone} onClick={send}>
            <Send size={12} /> Send code
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 8, alignItems: 'end', flexWrap: 'wrap' }}>
          {!needPw && <Field label="Code from Telegram"><input value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 140 }} /></Field>}
          {needPw && <Field label="Telegram 2FA password"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={{ width: 200 }} /></Field>}
          <button className="btn btn-primary btn-sm" disabled={busy || (!code && !password)} onClick={login}>Log in</button>
          <button className="btn btn-sm" onClick={() => { setStep('creds'); setNeedPw(false); }}>Back</button>
        </div>
      )}
      {msg && <p style={{ fontSize: 12, color: msg.ok ? C.green : C.red, margin: '8px 0 0' }}>{msg.text}</p>}
      <p style={{ fontSize: 11, color: C.faint, margin: '8px 0 0', lineHeight: 1.5 }}>
        Get api_id and api_hash at <b>my.telegram.org</b> → API development tools (any app name). They and the
        session are stored encrypted on this server and never shown again. Log out here, or end the session from
        Telegram → Settings → Devices, to revoke it.
      </p>
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────
function Channels({ loggedIn, onHistory }) {
  const qc = useQueryClient();
  const { data: channels = [] } = useQuery({ queryKey: qk('ct-channels'), queryFn: () => copytrade.channels().then((r) => r.data) });
  const [picking, setPicking] = useState(false);
  const [search, setSearch] = useState('');
  const [link, setLink] = useState('');
  const [err, setErr] = useState(null);
  const dialogs = useQuery({ queryKey: qk('ct-dialogs'), queryFn: () => copytrade.dialogs().then((r) => r.data),
    enabled: picking && loggedIn, staleTime: 60000 });
  const refresh = () => qc.invalidateQueries({ queryKey: qk('ct-channels') });
  const add = useMutation({ mutationFn: (d) => copytrade.addChannel(d), onSuccess: () => { setErr(null); setLink(''); refresh(); },
    onError: (e) => setErr(apiError(e)) });
  const patch = useMutation({ mutationFn: ({ id, d }) => copytrade.patchChannel(id, d), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id) => copytrade.deleteChannel(id), onSuccess: refresh });
  const watched = new Set(channels.map((c) => String(c.chat_id)));
  const shown = (dialogs.data || []).filter((d) => !watched.has(String(d.chat_id))
    && (!search || (d.title || '').toLowerCase().includes(search.toLowerCase())));

  return (
    <Card icon={<MessageSquare size={15} />} title="Channels"
      why="The channels and groups whose signals are copied. Pick one you are already in, or paste a @username / t.me link (it joins the channel on your account). Per-channel risk overrides the rules below."
      right={<button className="btn btn-sm" disabled={!loggedIn} onClick={() => setPicking(!picking)}><Plus size={12} /> Add</button>}>
      {picking && (
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <input placeholder="@channel or https://t.me/... or t.me/+invite" value={link} onChange={(e) => setLink(e.target.value)} style={{ flex: 1 }} />
            <button className="btn btn-primary btn-sm" disabled={!link || add.isPending} onClick={() => add.mutate({ link, join: true })}>Add link</button>
          </div>
          <input placeholder="search your chats" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: '100%', marginBottom: 6 }} />
          {dialogs.isLoading && <p style={{ fontSize: 12, color: C.dim }}>Loading your chats…</p>}
          {dialogs.error && <p style={{ fontSize: 12, color: C.red }}>{apiError(dialogs.error)}</p>}
          <div style={{ maxHeight: 220, overflowY: 'auto' }}>
            {shown.slice(0, 80).map((d) => (
              <div key={d.chat_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: `1px solid ${C.line}`, fontSize: 12 }}>
                <span style={{ flex: 1 }}>{d.title} {d.username && <span style={{ color: C.faint }}>@{d.username}</span>}</span>
                <span style={{ color: C.faint }}>{d.is_channel ? 'channel' : 'group'}</span>
                <button className="btn btn-sm" onClick={() => add.mutate({ chat_id: d.chat_id, title: d.title, username: d.username })}>Watch</button>
              </div>
            ))}
          </div>
        </div>
      )}
      {err && <p style={{ fontSize: 12, color: C.red }}>{err}</p>}
      {!channels.length && <p style={{ fontSize: 12, color: C.dim }}>No channels yet.</p>}
      {channels.map((c) => <ChannelRow key={c.id} c={c} onPatch={(d) => patch.mutate({ id: c.id, d })}
        onDelete={() => window.confirm(`Stop watching ${c.title}?`) && del.mutate(c.id)} onHistory={() => onHistory(c)} />)}
    </Card>
  );
}

function ChannelRow({ c, onPatch, onDelete, onHistory }) {
  const [open, setOpen] = useState(false);
  const [r, setR] = useState({ risk_pct: c.rules?.risk_pct ?? '', split_weights: (c.rules?.split_weights || []).join(','),
    symbol_map: Object.entries(c.rules?.symbol_map || {}).map(([k, v]) => `${k}=${v}`).join('\n') });
  const save = () => onPatch({ rules: {
    ...c.rules,
    risk_pct: r.risk_pct === '' ? undefined : Number(r.risk_pct),
    split_weights: r.split_weights ? r.split_weights.split(',').map(Number).filter((x) => !Number.isNaN(x)) : undefined,
    symbol_map: parseMap(r.symbol_map),
  } });
  return (
    <div style={{ borderBottom: `1px solid ${C.line}`, padding: '6px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
        <input type="checkbox" checked={c.enabled} onChange={(e) => onPatch({ enabled: e.target.checked })} title="watch this channel" />
        <b style={{ flex: 1 }}>{c.title || c.chat_id} {c.username && <span style={{ color: C.faint, fontWeight: 400 }}>@{c.username}</span>}</b>
        {c.rules?.risk_pct != null && <Badge text={`${c.rules.risk_pct}% risk`} colour={C.blue} />}
        <button className="btn btn-sm" onClick={onHistory} title="read the channel's recent messages"><History size={12} /></button>
        <button className="btn btn-sm" onClick={() => setOpen(!open)} title="overrides"><Settings2 size={12} /></button>
        <button className="btn btn-sm" onClick={onDelete} title="stop watching"><Trash2 size={12} /></button>
      </div>
      {open && (
        <div style={{ display: 'grid', gridTemplateColumns: '120px 200px 1fr auto', gap: 8, alignItems: 'end', marginTop: 8 }}>
          <Field label="Risk % (blank = default)"><input value={r.risk_pct} onChange={(e) => setR({ ...r, risk_pct: e.target.value })} /></Field>
          <Field label="TP weights e.g. 40,30,20,10"><input value={r.split_weights} onChange={(e) => setR({ ...r, split_weights: e.target.value })} /></Field>
          <Field label="Symbol map, one per line: GOLD=XAUUSD"><textarea rows={2} value={r.symbol_map} onChange={(e) => setR({ ...r, symbol_map: e.target.value })} /></Field>
          <button className="btn btn-primary btn-sm" onClick={save}>Save</button>
        </div>
      )}
    </div>
  );
}

function parseMap(text) {
  const out = {};
  (text || '').split('\n').forEach((line) => {
    const [k, ...v] = line.split('=');
    if (k?.trim() && v.join('=').trim()) out[k.trim().toUpperCase()] = v.join('=').trim();
  });
  return out;
}

// ── Rules ───────────────────────────────────────────────────────────────────
const RULE_FIELDS = [
  ['Sizing', [
    ['risk_mode', 'Sizing', 'select', ['percent', 'fixed_lots'], 'percent: risk % of balance to the stop; fixed_lots: a fixed total volume'],
    ['risk_pct', 'Total risk per signal %', 'number', null, 'split across all TP legs'],
    ['fixed_lots', 'Fixed total lots', 'number'],
    ['split_weights', 'TP weights (comma)', 'list', null, 'e.g. 40,30,20,10 - leg 1 gets 40% of the volume. Empty = equal'],
    ['max_legs', 'Max TP legs', 'number', null, 'use at most this many TPs (the nearest)'],
    ['allow_min_lot', 'Trade the min lot if risk is below it', 'bool'],
    ['min_lot_max_risk_pct', '…if that risk ≤ % of balance', 'number'],
  ]],
  ['Entries', [
    ['market_tolerance_pct', 'Market entry tolerance %', 'number', null, 'take a market signal while price is within this % past the written entry/zone'],
    ['chase_rule', 'Price ran past the entry', 'select', ['limit', 'skip', 'market'], 'limit = wait at the entry with a limit order'],
    ['skip_if_tp1_passed', 'Skip if price is beyond TP1', 'bool'],
    ['pending_expiry_hours', 'Cancel unfilled limit/stop after (h, 0 = never)', 'number'],
    ['missing_sl', 'Signal without SL', 'select', ['skip', 'default']],
    ['default_sl_pct', 'Default SL distance %', 'number'],
    ['missing_tp_rr', 'Signal without TP: TP at R (0 = none)', 'number'],
  ]],
  ['Follow-ups', [
    ['follow_breakeven', 'Follow breakeven', 'bool'],
    ['be_offset_pct', 'Breakeven offset %', 'number', null, 'BE = entry ± this, to cover spread/commission'],
    ['follow_move_sl', 'Follow move SL', 'bool'],
    ['allow_widen_sl', 'Allow widening the SL', 'bool'],
    ['follow_move_tp', 'Follow move TP', 'bool'],
    ['follow_close', 'Follow close all', 'bool'],
    ['follow_partial_close', 'Follow partial close', 'bool'],
    ['follow_cancel', 'Follow cancel pending', 'bool'],
    ['be_after_tp', 'Auto-BE after TP n (0 = off)', 'number', null, 'move the remaining legs to breakeven once TPn fills'],
    ['edit_updates', 'Edited signal updates SL/TP', 'bool'],
  ]],
  ['Filters & broker', [
    ['max_open_signals', 'Max open signals per channel', 'number'],
    ['min_confidence', 'Min parse confidence', 'number'],
    ['allowed_symbols', 'Allowed symbols (comma, empty = all)', 'list-str'],
    ['symbol_suffix', 'Broker symbol suffix', 'text', null, 'e.g. .m'],
    ['symbol_map', 'Symbol map (NAME=Broker name per line)', 'map'],
    ['magic', 'Magic number', 'number', null, 'kept outside the strategies\' range so their trade manager never touches copied trades'],
  ]],
];

function Rules({ rules, onSave }) {
  const [r, setR] = useState(rules);
  const [saved, setSaved] = useState(false);
  const set = (k, v) => { setR({ ...r, [k]: v }); setSaved(false); };
  const input = ([k, , type, opts]) => {
    const v = r[k];
    if (type === 'bool') return <input type="checkbox" checked={!!v} onChange={(e) => set(k, e.target.checked)} />;
    if (type === 'select') return <select value={v} onChange={(e) => set(k, e.target.value)}>{opts.map((o) => <option key={o}>{o}</option>)}</select>;
    if (type === 'list') return <input value={(v || []).join(',')} onChange={(e) => set(k, e.target.value.split(',').map((x) => x.trim()).filter(Boolean).map(Number))} />;
    if (type === 'list-str') return <input value={(v || []).join(',')} onChange={(e) => set(k, e.target.value.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean))} />;
    if (type === 'map') return <textarea rows={3} defaultValue={Object.entries(v || {}).map(([a, b]) => `${a}=${b}`).join('\n')} onBlur={(e) => set(k, parseMap(e.target.value))} />;
    if (type === 'text') return <input value={v ?? ''} onChange={(e) => set(k, e.target.value)} />;
    return <input type="number" step="any" value={v ?? ''} onChange={(e) => set(k, e.target.value === '' ? '' : Number(e.target.value))} />;
  };
  return (
    <Card icon={<Settings2 size={15} />} title="Rules"
      why="How every signal is sized and placed, and which follow-ups are obeyed. A channel's own overrides take precedence."
      right={<button className="btn btn-primary btn-sm" onClick={() => onSave(r).then(() => setSaved(true))}>{saved ? 'Saved' : 'Save rules'}</button>}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
        {RULE_FIELDS.map(([group, fields]) => (
          <div key={group}>
            <div style={{ fontSize: 12, color: C.blue, marginBottom: 6 }}>{group}</div>
            {fields.map((f) => (
              <div key={f[0]} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, fontSize: 12 }}>
                <span style={{ flex: 1, color: C.dim }} title={f[4]}>{f[1]}</span>
                <span style={{ width: f[2] === 'bool' ? 'auto' : 120 }}>{input(f)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}

// ── Parse tester ────────────────────────────────────────────────────────────
function ParseTester({ text, setText, channels }) {
  const [channelId, setChannelId] = useState('');
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const run = async () => {
    setErr(null);
    try { setRes((await copytrade.parse(text, channelId || null)).data); } catch (e) { setErr(apiError(e)); }
  };
  const p = res?.parsed;
  return (
    <Card icon={<Wand2 size={15} />} title="Test a message"
      why="Paste a signal or follow-up to see how it is read and - for a signal - exactly what would be placed now at live prices. Places nothing.">
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} style={{ width: '100%' }}
        placeholder={'XAUUSD BUY NOW @ 2350-2347\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365'} />
      <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
        <select value={channelId} onChange={(e) => setChannelId(e.target.value)}>
          <option value="">default rules</option>
          {channels.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
        </select>
        <button className="btn btn-primary btn-sm" disabled={!text.trim()} onClick={run}><Play size={12} /> Parse</button>
      </div>
      {err && <p style={{ fontSize: 12, color: C.red }}>{err}</p>}
      {p && (
        <div style={{ fontSize: 12, marginTop: 10 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <Badge text={p.kind} colour={KIND_COLOUR[p.kind] || C.dim} />
            {p.symbol && <b>{p.symbol}</b>} {p.side && <span>{p.side} {p.order_type}</span>}
            {p.entry != null && <span>entry {p.entry_low !== p.entry_high ? `${p.entry_low}–${p.entry_high}` : p.entry}</span>}
            {p.sl != null && <span>SL {p.sl}</span>}
            {p.tps?.length > 0 && <span>TPs {p.tps.join(' / ')}</span>}
            {p.new_price != null && <span>→ {p.new_price}</span>}
            {p.close_fraction != null && <span>close {Math.round(p.close_fraction * 100)}%</span>}
            {p.tp_index != null && <span>TP{p.tp_index}</span>}
            <span style={{ color: C.faint }}>confidence {p.confidence}</span>
          </div>
          {p.notes?.length > 0 && <div style={{ color: C.amber, marginTop: 4 }}>{p.notes.join('; ')}</div>}
          {res.plan_reason && <div style={{ color: C.amber, marginTop: 6 }}>Would skip: {res.plan_reason}</div>}
          {res.plan?.ok && (
            <div style={{ marginTop: 8 }}>
              <div>{res.plan.side} {res.plan.order_type} <b>{res.plan.symbol}</b> @ {res.plan.entry} · SL {res.plan.sl} ·
                risk <b>${res.plan.risk_dollars}</b></div>
              <table style={{ marginTop: 4 }}><thead><tr><th>Leg</th><th>Lots</th><th>TP</th></tr></thead>
                <tbody>{res.plan.legs.map((l) => <tr key={l.leg}><td>TP{l.leg}</td><td>{l.volume}</td><td>{l.tp ?? 'open'}</td></tr>)}</tbody></table>
              {res.plan.notes?.length > 0 && <div style={{ color: C.dim, marginTop: 4 }}>{res.plan.notes.join('; ')}</div>}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ── Channel history ─────────────────────────────────────────────────────────
function HistoryPanel({ channel, onClose, onTest }) {
  const { data = [], isLoading, error, refetch } = useQuery({
    queryKey: qk('ct-history', channel.id), queryFn: () => copytrade.history(channel.id, 150).then((r) => r.data) });
  const counts = useMemo(() => data.reduce((a, m) => { a[m.parsed.kind] = (a[m.parsed.kind] || 0) + 1; return a; }, {}), [data]);
  return (
    <Card icon={<History size={15} />} title={`History · ${channel.title}`}
      why="The channel's recent messages and how each one is read. Use it to check the parser understands this channel's style before going live."
      right={<><button className="btn btn-sm" onClick={() => refetch()}><RefreshCw size={12} /></button><button className="btn btn-sm" onClick={onClose}>Close</button></>}>
      {isLoading && <p style={{ fontSize: 12, color: C.dim }}>Reading the channel…</p>}
      {error && <p style={{ fontSize: 12, color: C.red }}>{apiError(error)}</p>}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {Object.entries(counts).map(([k, n]) => <Badge key={k} text={`${k} ${n}`} colour={KIND_COLOUR[k] || C.dim} />)}
      </div>
      <div style={{ maxHeight: 420, overflowY: 'auto' }}>
        {data.map((m) => (
          <div key={m.id} style={{ borderBottom: `1px solid ${C.line}`, padding: '6px 0', fontSize: 12 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: C.faint }}>
              <Badge text={m.parsed.kind} colour={KIND_COLOUR[m.parsed.kind] || C.dim} />
              <span>#{m.id}</span>{m.reply_to && <span>↩ #{m.reply_to}</span>}
              <span>{m.date?.replace('T', ' ').slice(0, 16)}</span>
              <button className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={() => onTest(m.text)}>Test</button>
            </div>
            <pre style={{ whiteSpace: 'pre-wrap', margin: '4px 0 0', fontSize: 12 }}>{m.text}</pre>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ── Signal log ──────────────────────────────────────────────────────────────
function SignalLog({ channels }) {
  const [open, setOpen] = useState(null);
  const names = Object.fromEntries(channels.map((c) => [c.id, c.title]));
  const { data = [], refetch } = useQuery({ queryKey: qk('ct-signals'), queryFn: () => copytrade.signals({ limit: 200 }).then((r) => r.data),
    refetchInterval: 10000 });
  const orders = useQuery({ queryKey: qk('ct-orders', open), queryFn: () => copytrade.orders(open).then((r) => r.data), enabled: !!open });
  const rows = data.filter((s) => s.kind !== 'NONE' || s.status !== 'IGNORED');
  return (
    <Card icon={<Send size={15} />} title="Signal log"
      why="Every signal and follow-up seen on a watched channel and what was done about it (chatter is hidden). Click a row for the MT5 actions and orders."
      right={<button className="btn btn-sm" onClick={() => refetch()}><RefreshCw size={12} /></button>}>
      {!rows.length && <p style={{ fontSize: 12, color: C.dim }}>Nothing yet.</p>}
      {rows.length > 0 && (
        <div className="table-scroll">
          <table style={{ width: '100%', fontSize: 12 }}>
            <thead><tr><th>Time</th><th>Channel</th><th>Kind</th><th>Message</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <Fragment key={s.id}>
                  <tr onClick={() => setOpen(open === s.id ? null : s.id)} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap' }}>{s.created_at?.replace('T', ' ').slice(5, 16)}</td>
                    <td>{names[s.channel_id] || '-'}</td>
                    <td><Badge text={s.kind} colour={KIND_COLOUR[s.kind] || C.dim} />{s.edited && <span style={{ color: C.faint }}> edit</span>}</td>
                    <td style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.text}</td>
                    <td><Badge text={s.status} colour={STATUS_COLOUR[s.status] || C.dim} /></td>
                    <td style={{ color: C.dim, maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.detail}</td>
                  </tr>
                  {open === s.id && (
                    <tr><td colSpan={6} style={{ background: 'rgba(110,118,129,0.08)' }}>
                      <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{s.text}</pre>
                      {s.actions?.length > 0 && <ul style={{ margin: '6px 0' }}>{s.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>}
                      {orders.data?.length > 0 && (
                        <table style={{ marginTop: 4 }}>
                          <thead><tr><th>Leg</th><th>Ticket</th><th>Type</th><th>Lots</th><th>Entry</th><th>SL</th><th>TP</th><th>Status</th></tr></thead>
                          <tbody>{orders.data.map((o) => (
                            <tr key={o.id}><td>TP{o.leg}</td><td>{o.ticket ?? '-'}</td><td>{o.side} {o.kind}</td><td>{o.volume}</td>
                              <td>{o.entry}</td><td>{o.sl}</td><td>{o.tp ?? 'open'}</td><td title={o.detail}>{o.status}</td></tr>
                          ))}</tbody>
                        </table>
                      )}
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ── page ────────────────────────────────────────────────────────────────────
export default function CopyTrading() {
  const qc = useQueryClient();
  const { data: st, refetch, error } = useQuery({ queryKey: qk('ct-status'), queryFn: () => copytrade.status().then((r) => r.data),
    refetchInterval: 15000 });
  const { data: channels = [] } = useQuery({ queryKey: qk('ct-channels'), queryFn: () => copytrade.channels().then((r) => r.data) });
  const [history, setHistory] = useState(null);
  const [testText, setTestText] = useState('');
  const save = (d) => copytrade.saveSettings(d).then(() => qc.invalidateQueries({ queryKey: qk('ct-status') }));
  const tg = st?.telegram;

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0 }}>Copy Trading</h1>
        {tg && <Badge text={tg.logged_in ? `Telegram: ${tg.user}` : 'Telegram: not logged in'} colour={tg.logged_in ? C.green : C.amber} />}
        {tg?.logged_in && <Badge text={tg.watching ? `watching ${tg.watched_chats} chat(s)` : 'not watching'} colour={tg.watching ? C.green : C.red} />}
        <button className="btn btn-sm" onClick={() => refetch()}><RefreshCw size={12} /></button>
        {st && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 14, alignItems: 'center', fontSize: 13 }}>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={st.enabled} onChange={(e) => save({ enabled: e.target.checked })} /> Copying on
            </label>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={!st.dry_run}
                onChange={(e) => (!e.target.checked || window.confirm('Send real orders to the MT5 account this server is connected to?')) && save({ dry_run: !e.target.checked })} />
              <span style={{ color: st.dry_run ? C.blue : C.red }}>{st.dry_run ? 'Dry run (no orders)' : 'LIVE orders'}</span>
            </label>
          </div>
        )}
      </div>
      <p style={{ color: C.dim, fontSize: 12, maxWidth: 900, lineHeight: 1.6 }}>
        Copies signals from Telegram channels into the MT5 account this server trades. A signal opens one position per TP
        (TP1…TP10), sized from the total risk and split by the weights; replies such as <i>"SL to entry"</i>,
        <i> "move SL to 2352"</i>, <i>"close half"</i>, <i>"close all"</i> or <i>"cancel the limit"</i> are applied to the signal
        they answer (or the latest one). Start in dry run: it reads, plans against live prices and logs, and sends nothing.
      </p>
      {tg && !tg.telethon && <p style={{ color: C.red, fontSize: 12 }}>telethon is not installed on this server - redeploy to install it.</p>}
      {tg?.last_error && <p style={{ color: C.amber, fontSize: 12 }}>Last error: {tg.last_error}</p>}
      {error && <p style={{ color: C.red, fontSize: 12 }}>{apiError(error)}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12, alignItems: 'start' }}>
        <div style={{ minWidth: 0 }}>
          <Card icon={<Send size={15} />} title="Telegram account"
            right={tg?.logged_in && <button className="btn btn-sm" onClick={() => window.confirm('Log out of Telegram on this server?') && copytrade.logout().then(() => refetch())}><LogOut size={12} /> Log out</button>}>
            {tg?.logged_in
              ? <p style={{ fontSize: 13, margin: 0 }}>Logged in as <b>{tg.user}</b>. {tg.connected ? 'Connected.' : 'Reconnecting…'}</p>
              : st && <TelegramLogin tg={tg} onDone={() => { refetch(); qc.invalidateQueries({ queryKey: qk('ct-dialogs') }); }} />}
          </Card>
          <Channels loggedIn={!!tg?.logged_in} onHistory={setHistory} />
          {history && <HistoryPanel channel={history} onClose={() => setHistory(null)} onTest={setTestText} />}
        </div>
        <div style={{ minWidth: 0 }}>
          <ParseTester text={testText} setText={setTestText} channels={channels} />
        </div>
      </div>
      {st && <Rules key={JSON.stringify(st.rules)} rules={st.rules} onSave={(rules) => save({ rules })} />}
      <SignalLog channels={channels} />
    </div>
  );
}
