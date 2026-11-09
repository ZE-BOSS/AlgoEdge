/**
 * Copy Trading: copy signals from Telegram channels into MT5.
 *
 * The server logs in to Telegram as the USER (a bot cannot read someone else's
 * channel), watches the channels added here, and for every message:
 *   a signal    -> places it (an entry zone becomes limit orders through the
 *                  zone), one position per TP, sized from the total risk
 *   a follow-up -> breakeven, move SL/TP, close all/part, cancel pending -
 *                  applied to the signal it replies to, or the latest one
 * Dry run (the default) reads, plans against live prices and logs, but sends
 * nothing to MT5.
 */
import { Fragment, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Copy, Eye, EyeOff, History, Inbox, Info, Link2,
  LogOut, MessageSquare, Play, Plus, RefreshCw, Search, Send, Settings2, Trash2, Wand2, X,
} from 'lucide-react';

import { copytrade } from '../services/api';
import { qk } from '../services/servers';
import './CopyTrading.css';

const KIND = {
  OPEN: ['Signal', 'green'], BREAKEVEN: ['Breakeven', 'blue'], MOVE_SL: ['Move SL', 'blue'], MOVE_TP: ['Move TP', 'blue'],
  CLOSE: ['Close', 'red'], CANCEL: ['Cancel', 'yellow'], INFO: ['Info', 'muted'], NONE: ['Chatter', 'muted'],
};
const STATUS = {
  EXECUTED: 'green', PARTIAL: 'yellow', DRY_RUN: 'blue', SKIPPED: 'yellow', ERROR: 'red',
  IGNORED: 'muted', CLOSED: 'muted', SEEN: 'muted', SUPERSEDED: 'muted',
};

function apiError(e) {
  const said = e?.response?.data?.detail;
  if (said) return typeof said === 'string' ? said : JSON.stringify(said);
  if (e?.response) return `The server returned ${e.response.status}.`;
  return 'The request never reached the server.';
}

const Pill = ({ tone = 'muted', plain, children, title }) => (
  <span className={`ct-pill ct-pill--${tone}${plain ? ' ct-pill--plain' : ''}`} title={title}>{children}</span>
);
const KindPill = ({ kind }) => { const [t, c] = KIND[kind] || [kind, 'muted']; return <Pill tone={c} plain>{t}</Pill>; };

function Switch({ checked, onChange, label, sub, danger, disabled }) {
  return (
    <label className={`ct-switch${danger ? ' ct-switch--danger' : ''}`}>
      <input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="ct-switch__track" />
      {(label || sub) && <span className="ct-switch__text">{label}{sub && <span className="ct-switch__sub">{sub}</span>}</span>}
    </label>
  );
}

function Card({ icon, title, sub, actions, children, foot }) {
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
      {foot && <footer className="ct-card__foot">{foot}</footer>}
    </section>
  );
}

const Field = ({ label, hint, children, htmlFor }) => (
  <div className="ct-field">
    {label && <label htmlFor={htmlFor}>{label}</label>}
    {children}
    {hint && <p className="ct-hint">{hint}</p>}
  </div>
);

const Note = ({ tone, children }) => {
  const Icon = tone === 'ok' ? CheckCircle2 : tone === 'err' || tone === 'warn' ? AlertTriangle : Info;
  return <div className={`ct-note${tone ? ` ct-note--${tone}` : ''}`}><Icon size={14} /><div>{children}</div></div>;
};

const initials = (s) => (s || '?').replace(/[^\p{L}\p{N} ]/gu, '').trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '#';

// ── Telegram login ──────────────────────────────────────────────────────────
function TelegramLogin({ tg, onDone }) {
  const [f, setF] = useState({ api_id: tg?.api_id || '', api_hash: '', phone: tg?.phone || '' });
  const [showHash, setShowHash] = useState(false);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState(tg?.awaiting_code ? 2 : 1);
  const [needPw, setNeedPw] = useState(false);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const credsOk = /^\d{4,12}$/.test(String(f.api_id).trim()) && f.api_hash.trim().length >= 16 && /^\+?\d{7,15}$/.test(f.phone.replace(/[\s-]/g, ''));

  const send = async (e) => {
    e?.preventDefault();
    setBusy(true); setMsg(null);
    try {
      const { data } = await copytrade.loginStart({ api_id: Number(f.api_id), api_hash: f.api_hash.trim(), phone: f.phone.replace(/[\s-]/g, '') });
      setStep(2);
      setMsg({ tone: 'ok', text: `Code sent${data.via ? ` via ${data.via}` : ''}. Check the Telegram app on your phone.` });
    } catch (err) { setMsg({ tone: 'err', text: apiError(err) }); }
    setBusy(false);
  };
  const login = async (e) => {
    e?.preventDefault();
    setBusy(true); setMsg(null);
    try {
      const { data } = await copytrade.loginCode({ code: needPw ? null : code.trim(), password: password || null });
      if (data.need_password) { setNeedPw(true); setMsg({ tone: 'warn', text: 'Two-step verification is on. Enter your Telegram cloud password.' }); }
      else if (data.ok) { setMsg({ tone: 'ok', text: `Logged in as ${data.user}.` }); onDone(); }
      else setMsg({ tone: 'err', text: data.error || 'Login failed.' });
    } catch (err) { setMsg({ tone: 'err', text: apiError(err) }); }
    setBusy(false);
  };

  return (
    <div>
      <div className="ct-steps">
        <div className={`ct-step ${step === 1 ? 'is-on' : 'is-done'}`}><span className="ct-step__n">{step > 1 ? '✓' : 1}</span>App keys & phone</div>
        <div className="ct-step__bar" style={{ alignSelf: 'center' }} />
        <div className={`ct-step ${step === 2 ? 'is-on' : ''}`}><span className="ct-step__n">2</span>Confirm code</div>
      </div>

      {step === 1 ? (
        <form className="ct-form" onSubmit={send}>
          <div className="ct-row" style={{ gridTemplateColumns: 'minmax(120px, 0.7fr) minmax(170px, 1fr)' }}>
            <Field label="API ID" htmlFor="ct-apiid">
              <input id="ct-apiid" className="ct-input ct-mono" inputMode="numeric" autoComplete="off" placeholder="12345678"
                value={f.api_id} onChange={(e) => setF({ ...f, api_id: e.target.value.replace(/\D/g, '') })} />
            </Field>
            <Field label="Phone number" htmlFor="ct-phone">
              <input id="ct-phone" className="ct-input ct-mono" type="tel" autoComplete="tel" placeholder="+234 801 234 5678"
                value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
            </Field>
          </div>
          <Field label="API hash" htmlFor="ct-apihash" hint="32 characters, from the same page as the API ID.">
            <div className="ct-affix">
              <input id="ct-apihash" className="ct-input ct-mono" type={showHash ? 'text' : 'password'} autoComplete="off" spellCheck={false}
                placeholder="0123456789abcdef0123456789abcdef" value={f.api_hash} onChange={(e) => setF({ ...f, api_hash: e.target.value })} />
              <button type="button" className="ct-affix__btn" onClick={() => setShowHash(!showHash)} title={showHash ? 'Hide' : 'Show'}>
                {showHash ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </Field>
          <Note>
            Get the API ID and hash at <a href="https://my.telegram.org/apps" target="_blank" rel="noreferrer">my.telegram.org/apps</a> -
            log in with your phone, open <b>API development tools</b> and create an app (any name). They and the session are stored
            encrypted on this server and never shown again.
          </Note>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="submit" className="btn btn-primary" disabled={busy || !credsOk}><Send size={14} /> {busy ? 'Sending…' : 'Send code'}</button>
          </div>
        </form>
      ) : (
        <form className="ct-form" onSubmit={login}>
          {!needPw ? (
            <Field label={`Code sent to ${f.phone || 'your Telegram'}`} htmlFor="ct-code" hint="Telegram sends it to your app (not SMS) - look for a message from Telegram.">
              <input id="ct-code" className="ct-input ct-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus
                placeholder="·····" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
            </Field>
          ) : (
            <Field label="Telegram cloud password" htmlFor="ct-pw" hint="The two-step verification password you set in Telegram → Privacy and Security.">
              <input id="ct-pw" className="ct-input" type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <button type="button" className="btn btn-secondary" onClick={() => { setStep(1); setNeedPw(false); setCode(''); setMsg(null); }}>Back</button>
            <button type="submit" className="btn btn-primary" disabled={busy || (needPw ? !password : code.length < 5)}>{busy ? 'Checking…' : 'Log in'}</button>
          </div>
        </form>
      )}
      {msg && <div style={{ marginTop: 12 }}><Note tone={msg.tone}>{msg.text}</Note></div>}
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────
function parseMap(text) {
  const out = {};
  (text || '').split('\n').forEach((line) => {
    const [k, ...v] = line.split('=');
    if (k?.trim() && v.join('=').trim()) out[k.trim().toUpperCase()] = v.join('=').trim();
  });
  return out;
}
const mapText = (m) => Object.entries(m || {}).map(([k, v]) => `${k}=${v}`).join('\n');

function Channels({ loggedIn, onHistory, historyId }) {
  const qc = useQueryClient();
  const { data: channels = [] } = useQuery({ queryKey: qk('ct-channels'), queryFn: () => copytrade.channels().then((r) => r.data) });
  const [adding, setAdding] = useState(false);
  const [tab, setTab] = useState('chats');
  const [search, setSearch] = useState('');
  const [link, setLink] = useState('');
  const [err, setErr] = useState(null);
  const dialogs = useQuery({ queryKey: qk('ct-dialogs'), queryFn: () => copytrade.dialogs().then((r) => r.data),
    enabled: adding && loggedIn && tab === 'chats', staleTime: 60000 });
  const refresh = () => qc.invalidateQueries({ queryKey: qk('ct-channels') });
  const add = useMutation({ mutationFn: (d) => copytrade.addChannel(d), onSuccess: () => { setErr(null); setLink(''); refresh(); },
    onError: (e) => setErr(apiError(e)) });
  const patch = useMutation({ mutationFn: ({ id, d }) => copytrade.patchChannel(id, d), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id) => copytrade.deleteChannel(id), onSuccess: refresh });
  const watched = new Set(channels.map((c) => String(c.chat_id)));
  const shown = (dialogs.data || []).filter((d) => !watched.has(String(d.chat_id))
    && (!search || `${d.title} ${d.username || ''}`.toLowerCase().includes(search.toLowerCase())));

  return (
    <Card icon={<MessageSquare size={15} />} title="Channels" sub="Signals from these channels and groups are copied."
      actions={<button className="btn btn-sm btn-secondary" disabled={!loggedIn} onClick={() => setAdding(!adding)}
        title={loggedIn ? '' : 'Log in to Telegram first'}>{adding ? <><X size={13} /> Close</> : <><Plus size={13} /> Add channel</>}</button>}>
      {adding && (
        <div className="ct-adder">
          <div className="ct-tabs">
            <button className={tab === 'chats' ? 'is-on' : ''} onClick={() => setTab('chats')}>From my chats</button>
            <button className={tab === 'link' ? 'is-on' : ''} onClick={() => setTab('link')}>By link or @username</button>
          </div>
          {tab === 'link' ? (
            <form className="ct-form" onSubmit={(e) => { e.preventDefault(); if (link.trim()) add.mutate({ link: link.trim(), join: true }); }}>
              <Field hint="Public: @name or t.me/name. Private: the t.me/+invite link. Joins the channel on your account if you aren't in it yet.">
                <div className="ct-affix">
                  <input className="ct-input" placeholder="https://t.me/goldtradersunny" value={link} onChange={(e) => setLink(e.target.value)} />
                  <button type="submit" className="ct-affix__btn" disabled={!link.trim() || add.isPending}><Link2 size={14} />&nbsp;Add</button>
                </div>
              </Field>
            </form>
          ) : (
            <>
              <div className="ct-affix">
                <input className="ct-input" placeholder="Search your channels and groups" value={search} onChange={(e) => setSearch(e.target.value)} />
                <span className="ct-affix__unit"><Search size={14} /></span>
              </div>
              <div className="ct-picker">
                {dialogs.isLoading && <p className="ct-hint" style={{ padding: 8 }}>Loading your chats…</p>}
                {dialogs.error && <Note tone="err">{apiError(dialogs.error)}</Note>}
                {!dialogs.isLoading && !dialogs.error && !shown.length && <p className="ct-hint" style={{ padding: 8 }}>No matching chats.</p>}
                {shown.slice(0, 100).map((d) => (
                  <div key={d.chat_id} className="ct-picker__item">
                    <span className="ct-chan__avatar">{initials(d.title)}</span>
                    <div className="ct-chan__main">
                      <div className="ct-chan__name">{d.title}</div>
                      <div className="ct-chan__meta">{d.is_channel ? 'Channel' : 'Group'}{d.username ? ` · @${d.username}` : ''}</div>
                    </div>
                    <button className="btn btn-sm btn-primary" disabled={add.isPending}
                      onClick={() => add.mutate({ chat_id: d.chat_id, title: d.title, username: d.username })}>Watch</button>
                  </div>
                ))}
              </div>
            </>
          )}
          {err && <div style={{ marginTop: 10 }}><Note tone="err">{err}</Note></div>}
        </div>
      )}
      {!channels.length ? (
        <div className="ct-empty"><Inbox size={26} /><div>{loggedIn ? 'No channels yet - add one to start copying.' : 'Log in to Telegram, then add the channels to copy.'}</div></div>
      ) : (
        <div className="ct-list">
          {channels.map((c) => <ChannelRow key={c.id} c={c} active={historyId === c.id} onPatch={(d) => patch.mutate({ id: c.id, d })}
            onDelete={() => window.confirm(`Stop watching ${c.title}?`) && del.mutate(c.id)} onHistory={() => onHistory(c)} />)}
        </div>
      )}
    </Card>
  );
}

function ChannelRow({ c, active, onPatch, onDelete, onHistory }) {
  const [open, setOpen] = useState(false);
  const [r, setR] = useState({
    risk_pct: c.rules?.risk_pct ?? '', split_weights: (c.rules?.split_weights || []).join(', '), symbol_map: mapText(c.rules?.symbol_map),
    default_symbol: c.rules?.default_symbol ?? '', allowed_senders: (c.rules?.allowed_senders || []).join(', '),
  });
  const save = () => {
    const rules = { ...c.rules };
    if (r.risk_pct === '') delete rules.risk_pct; else rules.risk_pct = Number(r.risk_pct);
    const w = r.split_weights.split(',').map((x) => Number(x.trim())).filter((x) => x > 0);
    if (w.length) rules.split_weights = w; else delete rules.split_weights;
    const m = parseMap(r.symbol_map);
    if (Object.keys(m).length) rules.symbol_map = m; else delete rules.symbol_map;
    if (r.default_symbol.trim()) rules.default_symbol = r.default_symbol.trim(); else delete rules.default_symbol;
    const snd = r.allowed_senders.split(',').map((x) => x.trim()).filter(Boolean);
    if (snd.length) rules.allowed_senders = snd; else delete rules.allowed_senders;
    onPatch({ rules });
    setOpen(false);
  };
  const overrides = ['risk_pct', 'split_weights', 'symbol_map', 'default_symbol', 'allowed_senders'].filter((k) => c.rules?.[k] != null).length;
  return (
    <div className="ct-chan">
      <div className="ct-chan__row">
        <span className="ct-chan__avatar">{initials(c.title)}</span>
        <div className="ct-chan__main">
          <div className="ct-chan__name">{c.title || c.chat_id}</div>
          <div className="ct-chan__meta">
            {c.username ? `@${c.username}` : 'private'}
            {c.rules?.risk_pct != null && ` · ${c.rules.risk_pct}% risk`}
            {overrides > 0 && ` · ${overrides} override${overrides > 1 ? 's' : ''}`}
          </div>
        </div>
        <Switch checked={c.enabled} onChange={(v) => onPatch({ enabled: v })} />
        <button className={`ct-iconbtn${active ? ' is-on' : ''}`} onClick={onHistory} title="Read recent messages"><History size={15} /></button>
        <button className={`ct-iconbtn${open ? ' is-on' : ''}`} onClick={() => setOpen(!open)} title="Channel overrides"><Settings2 size={15} /></button>
        <button className="ct-iconbtn ct-iconbtn--danger" onClick={onDelete} title="Stop watching"><Trash2 size={15} /></button>
      </div>
      {open && (
        <div className="ct-chan__body">
          <div className="ct-form">
            <div className="ct-row">
              <Field label="Risk per signal" hint="Blank = the default in Rules.">
                <div className="ct-affix">
                  <input className="ct-input" type="number" step="0.1" min="0" placeholder="default" value={r.risk_pct}
                    onChange={(e) => setR({ ...r, risk_pct: e.target.value })} />
                  <span className="ct-affix__unit">%</span>
                </div>
              </Field>
              <Field label="TP weights" hint="Volume split across TP1, TP2… e.g. 40, 30, 20, 10.">
                <input className="ct-input ct-mono" placeholder="equal" value={r.split_weights} onChange={(e) => setR({ ...r, split_weights: e.target.value })} />
              </Field>
            </div>
            <div className="ct-row">
              <Field label="Symbol when the signal names none" hint={'e.g. XAUUSD for "Take Entry SELL 4430 …"'}>
                <input className="ct-input ct-mono" placeholder="none" value={r.default_symbol}
                  onChange={(e) => setR({ ...r, default_symbol: e.target.value })} />
              </Field>
              <Field label="Only these senders" hint="Groups: @username or user id. Blank = the group's admins.">
                <input className="ct-input ct-mono" placeholder="admins" value={r.allowed_senders}
                  onChange={(e) => setR({ ...r, allowed_senders: e.target.value })} />
              </Field>
            </div>
            <Field label="Symbol map" hint="One per line: name in the channel = name at your broker.">
              <textarea className="ct-textarea ct-mono" rows={2} placeholder={'GOLD=XAUUSD\nNAS100=US Tech 100'} value={r.symbol_map}
                onChange={(e) => setR({ ...r, symbol_map: e.target.value })} />
            </Field>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn btn-sm btn-secondary" onClick={() => setOpen(false)}>Cancel</button>
              <button className="btn btn-sm btn-primary" onClick={save}>Save overrides</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Rules ───────────────────────────────────────────────────────────────────
// [key, name, type, help, extra]  type: bool | num(unit) | select(options) | list | list-str | map | text
const RULES = [
  ['Sizing', [
    ['risk_mode', 'Sizing method', 'select', 'Risk % sizes from the distance to the stop; fixed uses a set total volume.', [['percent', 'Risk % of balance'], ['fixed_lots', 'Fixed lots']]],
    ['risk_pct', 'Total risk per signal', 'num', 'Split across all TP legs.', '%'],
    ['fixed_lots', 'Fixed total volume', 'num', 'Used when sizing is fixed.', 'lots'],
    ['split_weights', 'TP weights', 'list', 'Volume share per leg, e.g. 40, 30, 20, 10. Empty = equal.'],
    ['max_legs', 'Max TP legs', 'num', 'Use at most this many targets (the nearest).', 'legs'],
    ['allow_min_lot', 'Trade the min lot if the budget is smaller', 'bool'],
    ['min_lot_max_risk_pct', '…only if that risk is at most', 'num', null, '%'],
  ]],
  ['Entries', [
    ['zone_entry', 'Entry zones', 'select', 'Layer = limit orders spread through the zone; first fill takes TP1, deepest takes the last TP.', [['layer', 'Layer limit orders'], ['market', 'One entry now']]],
    ['market_tolerance_pct', 'Market entry tolerance', 'num', 'How far past the written entry a market signal is still taken.', '%'],
    ['chase_rule', 'If price ran past the entry', 'select', null, [['limit', 'Wait with a limit'], ['skip', 'Skip'], ['market', 'Enter anyway']]],
    ['skip_if_tp1_passed', 'Skip if price is beyond TP1', 'bool'],
    ['on_first_fill', 'When the first zone order fills', 'select', 'A quick tap of the zone often fills only the first order. Market: cancel the deeper orders and take their volume now. Also at market: take it now and keep the limits too (more size if they fill later; they are cancelled at TP1).', [['keep', 'Keep the rest waiting'], ['market', 'Take the rest at market (cancel the limits)'], ['market_add', 'Also take the rest at market (limits stay until TP1)']]],
    ['pending_expiry_hours', 'Cancel unfilled orders after', 'num', '0 = never.', 'h'],
    ['missing_sl', 'Signal without a stop', 'select', null, [['skip', 'Skip it'], ['default', 'Use default stop']]],
    ['default_sl_pct', 'Default stop distance', 'num', null, '%'],
    ['missing_tp_rr', 'Signal without a target', 'num', 'TP at this R multiple. 0 = no TP.', 'R'],
  ]],
  ['Follow-ups', [
    ['follow_breakeven', 'Follow breakeven / "risk free"', 'bool'],
    ['cancel_pending_on_be', '"Risk free" also cancels unfilled orders', 'bool', 'No risk left means no orders left either.'],
    ['be_offset_pct', 'Breakeven offset', 'num', 'Entry ± this % of price, for spread and commission. Gold: 0.01 ≈ $0.40. Max 0.1. If it would sit past the price, plain entry is used.', '%'],
    ['follow_move_sl', 'Follow move SL', 'bool'],
    ['allow_widen_sl', 'Allow widening the stop', 'bool'],
    ['follow_move_tp', 'Follow move TP', 'bool'],
    ['follow_close', 'Follow close all', 'bool'],
    ['follow_partial_close', 'Follow partial close', 'bool'],
    ['follow_cancel', 'Follow cancel pending', 'bool'],
    ['edit_updates', 'Edited signal updates SL / TP', 'bool'],
    ['cancel_pending_after_tp', 'Cancel unfilled orders once TP n fills', 'num', '0 = keep them.', 'TP'],
    ['be_after_tp', 'Auto breakeven once TP n fills', 'num', '0 = off.', 'TP'],
    ['trail_ladder', 'Trail the stop up the TP ladder', 'bool', 'TP1 → breakeven, TP2 → TP1, TP3 → TP2 … as price reaches each target. Never loosens.'],
    ['runner_leg', 'Last leg runs without a target', 'bool', 'The ladder trail (or his close) ends it - catches the moves that run past the posted TPs.'],
    ['stack_at_tp1', 'Stack at TP1', 'num', 'When TP1 is reached, add this many times the open volume at market, stop at the entry (risks only the TP1 distance). 0 = off.', '×'],
  ]],
  ['Filters & broker', [
    ['max_open_signals', 'Max open signals per channel', 'num', null, 'signals'],
    ['min_confidence', 'Min parse confidence', 'num', '0 - 1. Lower reads looser formats.'],
    ['only_admins', 'In groups, follow only the admins', 'bool', 'Members chat in groups too ("closed all", "move SL…"). Channels are unaffected.'],
    ['allowed_symbols', 'Only these symbols', 'list-str', 'Comma separated. Empty = all.'],
    ['symbol_suffix', 'Broker symbol suffix', 'text', 'e.g. .m or .pro'],
    ['symbol_map', 'Symbol map', 'map', 'One per line: channel name = broker name.'],
    ['magic', 'Magic number', 'num', 'Kept outside the strategies\' range so their trade manager never touches copied trades.'],
  ]],
];

function RuleControl({ def, value, set }) {
  const [k, , type, , extra] = def;
  if (type === 'bool') return <Switch checked={!!value} onChange={(v) => set(k, v)} />;
  if (type === 'select') return (
    <select className="ct-select" value={value ?? ''} onChange={(e) => set(k, e.target.value)}>
      {extra.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
    </select>
  );
  if (type === 'list') return <input className="ct-input ct-mono" placeholder="equal" value={(value || []).join(', ')}
    onChange={(e) => set(k, e.target.value.split(',').map((x) => x.trim()).filter(Boolean).map(Number).filter((x) => !Number.isNaN(x)))} />;
  if (type === 'list-str') return <input className="ct-input ct-mono" placeholder="all" value={(value || []).join(', ')}
    onChange={(e) => set(k, e.target.value.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean))} />;
  if (type === 'map') return <textarea className="ct-textarea ct-mono" rows={3} placeholder={'GOLD=XAUUSD'} defaultValue={mapText(value)}
    onBlur={(e) => set(k, parseMap(e.target.value))} />;
  if (type === 'text') return <input className="ct-input ct-mono" value={value ?? ''} onChange={(e) => set(k, e.target.value)} />;
  const input = <input className="ct-input ct-mono" type="number" step="any" value={value ?? ''}
    onChange={(e) => set(k, e.target.value === '' ? '' : Number(e.target.value))} />;
  return extra ? <div className="ct-affix" style={{ width: '100%' }}>{input}<span className="ct-affix__unit">{extra}</span></div> : input;
}

function Rules({ rules, defaults, onSave }) {
  // draft is null while nothing is edited, so background refreshes show through;
  // once edited, the draft is kept until saved or discarded
  const [draft, setDraft] = useState(null);
  const [state, setState] = useState('clean');
  const r = draft ?? rules;
  const set = (k, v) => { setDraft((d) => ({ ...(d ?? rules), [k]: v })); setState('dirty'); };
  const save = async () => {
    setState('saving');
    try { await onSave(r); setDraft(null); setState('saved'); } catch { setState('dirty'); }
  };
  return (
    <Card icon={<Settings2 size={15} />} title="Rules" sub="How every signal is sized and placed, and which follow-ups are obeyed. Channel overrides take precedence."
      foot={<>
        {state === 'dirty' && <span className="ct-dirty"><AlertTriangle size={13} /> Unsaved changes</span>}
        {state === 'saved' && <span className="ct-dirty" style={{ color: 'var(--green)' }}><CheckCircle2 size={13} /> Saved</span>}
        <button className="btn btn-sm btn-secondary" disabled={state !== 'dirty'} onClick={() => { setDraft(null); setState('clean'); }}>Discard</button>
        <button className="btn btn-sm btn-secondary" onClick={() => { setDraft({ ...defaults }); setState('dirty'); }}>Reset to defaults</button>
        <button className="btn btn-sm btn-primary" disabled={state !== 'dirty'} onClick={save}>{state === 'saving' ? 'Saving…' : 'Save rules'}</button>
      </>}>
      <div className="ct-rules">
        {RULES.map(([group, defs]) => (
          <div key={group} className="ct-group">
            <div className="ct-group__title">{group}</div>
            {defs.map((d) => {
              const stack = d[2] === 'map';
              return (
                <div key={d[0]} className={`ct-rule${stack ? ' ct-rule--stack' : ''}`}>
                  <div className="ct-rule__text">
                    <div className="ct-rule__name">{d[1]}</div>
                    {d[3] && <div className="ct-rule__help">{d[3]}</div>}
                  </div>
                  <div className={`ct-rule__ctl${d[2] === 'select' ? ' ct-rule__ctl--wide' : ''}`}><RuleControl def={d} value={r[d[0]]} set={set} /></div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </Card>
  );
}

// ── Message tester ──────────────────────────────────────────────────────────
const SAMPLE = 'Buy Gold 4487 - 4495\nStop Loss 4483\nTP1 4497\nTP2 4499\nTP3 4501\nTP4 Open (4504/4507/4512/4518)';

function ParseTester({ text, setText, channels }) {
  const [channelId, setChannelId] = useState('');
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setErr(null); setBusy(true);
    try { setRes((await copytrade.parse(text, channelId || null)).data); } catch (e) { setErr(apiError(e)); }
    setBusy(false);
  };
  const p = res?.parsed;
  const plan = res?.plan;
  return (
    <Card icon={<Wand2 size={15} />} title="Test a message" sub="See how a message is read and exactly what would be placed now at live prices. Places nothing.">
      <div className="ct-form">
        <textarea className="ct-textarea ct-mono" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={SAMPLE} />
        <div style={{ display: 'flex', gap: 8 }}>
          <select className="ct-select" value={channelId} onChange={(e) => setChannelId(e.target.value)} style={{ flex: 1 }}>
            <option value="">Default rules</option>
            {channels.map((c) => <option key={c.id} value={c.id}>{c.title} rules</option>)}
          </select>
          {!text && <button className="btn btn-secondary" onClick={() => setText(SAMPLE)}>Use example</button>}
          <button className="btn btn-primary" disabled={!text.trim() || busy} onClick={run}><Play size={14} /> {busy ? 'Reading…' : 'Test'}</button>
        </div>
      </div>
      {err && <div style={{ marginTop: 12 }}><Note tone="err">{err}</Note></div>}
      {p && (
        <div className="ct-result">
          <div className="ct-result__head">
            <KindPill kind={p.kind} />
            {p.symbol && <b style={{ fontSize: '0.85rem' }}>{p.symbol}</b>}
            {p.side && <Pill tone={p.side === 'BUY' ? 'green' : 'red'} plain>{p.side} {p.order_type !== 'MARKET' ? p.order_type : ''}</Pill>}
            <span className="ct-hint" style={{ marginLeft: 'auto' }}>confidence {Math.round((p.confidence || 0) * 100)}%</span>
          </div>
          {p.kind === 'OPEN' ? (
            <dl className="ct-kv" style={{ margin: 0 }}>
              <div><dt>Entry</dt><dd>{p.entry_low != null && p.entry_low !== p.entry_high ? `${p.entry_low} – ${p.entry_high}` : (p.entry ?? 'market')}</dd></div>
              <div><dt>Stop</dt><dd>{p.sl ?? '—'}</dd></div>
              <div><dt>Targets</dt><dd>{p.tps?.length ? `${p.tps.length}: ${p.tps[0]} … ${p.tps[p.tps.length - 1]}` : '—'}</dd></div>
            </dl>
          ) : (
            <div className="ct-result__section">
              {p.kind === 'BREAKEVEN' && 'Moves the stop of the signal it replies to (or the latest one) to its entry.'}
              {p.kind === 'MOVE_SL' && `Moves the stop to ${p.new_price}.`}
              {p.kind === 'MOVE_TP' && `Moves ${p.tp_index ? `TP${p.tp_index}` : 'the targets'} to ${p.new_price}.`}
              {p.kind === 'CLOSE' && `Closes ${p.close_fraction < 1 ? `${Math.round(p.close_fraction * 100)}% of` : 'all of'} ${p.applies_to === 'all' ? 'every open signal on the channel' : 'the signal it replies to'}.`}
              {p.kind === 'CANCEL' && 'Cancels the unfilled orders of the signal it replies to. Filled positions stay open.'}
              {p.kind === 'INFO' && 'Information only - nothing is done.'}
              {p.kind === 'NONE' && 'Not a signal - ignored.'}
            </div>
          )}
          {p.notes?.length > 0 && <div className="ct-result__section" style={{ color: 'var(--yellow)' }}>{p.notes.join('; ')}</div>}
          {res.plan_reason && <div className="ct-result__section"><Note tone="warn">Would be skipped: {res.plan_reason}</Note></div>}
          {plan?.ok && (
            <div className="ct-result__section">
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
                <b style={{ color: 'var(--text-primary)' }}>{plan.symbol}</b>
                <Pill tone="blue" plain>{plan.order_type === 'LAYER' ? 'Layered limits' : plan.order_type}</Pill>
                <span>stop <span className="ct-mono">{plan.sl}</span></span>
                <span style={{ marginLeft: 'auto' }}>risk <b className="ct-mono" style={{ color: 'var(--text-primary)' }}>${plan.risk_dollars}</b></span>
              </div>
              <table className="ct-table">
                <thead><tr><th>Leg</th><th>Order</th><th>Entry</th><th>Lots</th><th>Target</th></tr></thead>
                <tbody>{plan.legs.map((l) => (
                  <tr key={l.leg}><td>TP{l.leg}</td><td>{(l.kind || plan.order_type).toLowerCase()}</td><td className="num">{l.entry ?? plan.entry}</td>
                    <td className="num">{l.volume}</td><td className="num">{l.tp ?? 'open'}</td></tr>
                ))}</tbody>
              </table>
              {plan.notes?.length > 0 && <p className="ct-hint" style={{ marginTop: 8 }}>{plan.notes.join('; ')}</p>}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ── Channel history ─────────────────────────────────────────────────────────
function HistoryPanel({ channel, onClose, onTest }) {
  const [filter, setFilter] = useState('signals');
  const { data = [], isLoading, error, refetch, isFetching } = useQuery({
    queryKey: qk('ct-history', channel.id), queryFn: () => copytrade.history(channel.id, 300).then((r) => r.data) });
  const counts = useMemo(() => data.reduce((a, m) => { a[m.parsed.kind] = (a[m.parsed.kind] || 0) + 1; return a; }, {}), [data]);
  const shown = data.filter((m) => filter === 'all' || (filter === 'signals' ? m.parsed.kind === 'OPEN'
    : !['OPEN', 'NONE', 'INFO'].includes(m.parsed.kind)));
  return (
    <Card icon={<History size={15} />} title={`History · ${channel.title}`} sub="Recent messages and how each one is read."
      actions={<>
        <button className="ct-iconbtn" onClick={() => refetch()} title="Reload"><RefreshCw size={14} className={isFetching ? 'spin' : ''} /></button>
        <button className="ct-iconbtn" onClick={onClose} title="Close"><X size={15} /></button>
      </>}>
      {isLoading && <p className="ct-hint">Reading the channel…</p>}
      {error && <Note tone="err">{apiError(error)}</Note>}
      {!!data.length && (
        <div className="ct-chips" style={{ marginBottom: 6 }}>
          {[['signals', `Signals ${counts.OPEN || 0}`], ['follow', `Follow-ups ${data.filter((m) => !['OPEN', 'NONE', 'INFO'].includes(m.parsed.kind)).length}`], ['all', `All ${data.length}`]].map(([k, t]) => (
            <button key={k} className={`ct-chip${filter === k ? ' is-on' : ''}`} onClick={() => setFilter(k)}>{t}</button>
          ))}
        </div>
      )}
      <div className="ct-scroll">
        {shown.map((m) => (
          <div key={m.id} className="ct-msg">
            <div className="ct-msg__meta">
              <KindPill kind={m.parsed.kind} />
              <span>#{m.id}</span>{m.reply_to && <span>↩ #{m.reply_to}</span>}
              <span>{m.date?.replace('T', ' ').slice(0, 16)}</span>
              <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={() => onTest(m.text)}><Wand2 size={12} /> Test</button>
            </div>
            <pre>{m.text}</pre>
          </div>
        ))}
        {!isLoading && !error && !shown.length && <div className="ct-empty">Nothing here.</div>}
      </div>
    </Card>
  );
}

// ── Signal log ──────────────────────────────────────────────────────────────
function SignalLog({ channels }) {
  const [open, setOpen] = useState(null);
  const [filter, setFilter] = useState('all');
  const names = Object.fromEntries(channels.map((c) => [c.id, c.title]));
  const { data = [], refetch, isFetching } = useQuery({ queryKey: qk('ct-signals'), queryFn: () => copytrade.signals({ limit: 300 }).then((r) => r.data),
    refetchInterval: 10000 });
  const orders = useQuery({ queryKey: qk('ct-orders', open), queryFn: () => copytrade.orders(open).then((r) => r.data), enabled: !!open });
  const base = data.filter((s) => s.kind !== 'NONE');
  const rows = base.filter((s) => filter === 'all' || (filter === 'signals' ? s.kind === 'OPEN'
    : filter === 'problems' ? ['ERROR', 'SKIPPED', 'PARTIAL'].includes(s.status) : s.kind !== 'OPEN'));
  return (
    <Card icon={<Send size={15} />} title="Signal log" sub="Every signal and follow-up seen and what was done. Chatter is hidden. Click a row for the orders."
      actions={<button className="ct-iconbtn" onClick={() => refetch()} title="Reload"><RefreshCw size={14} className={isFetching ? 'spin' : ''} /></button>}>
      <div className="ct-chips" style={{ marginBottom: 10 }}>
        {[['all', 'All'], ['signals', 'Signals'], ['follow', 'Follow-ups'], ['problems', 'Skipped & errors']].map(([k, t]) => (
          <button key={k} className={`ct-chip${filter === k ? ' is-on' : ''}`} onClick={() => setFilter(k)}>{t}</button>
        ))}
      </div>
      {!rows.length ? <div className="ct-empty"><Inbox size={26} /><div>Nothing yet.</div></div> : (
        <div style={{ overflowX: 'auto' }}>
          <table className="ct-table">
            <thead><tr><th>Time</th><th>Channel</th><th>Type</th><th>Message</th><th>Result</th><th>Detail</th><th /></tr></thead>
            <tbody>
              {rows.map((s) => (
                <Fragment key={s.id}>
                  <tr className={`is-click${open === s.id ? ' is-open' : ''}`} onClick={() => setOpen(open === s.id ? null : s.id)}>
                    <td className="num" style={{ whiteSpace: 'nowrap' }}>{s.created_at?.replace('T', ' ').slice(5, 16)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{names[s.channel_id] || '—'}</td>
                    <td><KindPill kind={s.kind} />{s.edited && <span className="ct-hint"> edited</span>}</td>
                    <td><div className="ct-trunc">{s.text}</div></td>
                    <td><Pill tone={STATUS[s.status] || 'muted'}>{s.status === 'DRY_RUN' ? 'Dry run' : s.status.toLowerCase()}</Pill></td>
                    <td><div className="ct-trunc" style={{ maxWidth: 280 }}>{s.detail}</div></td>
                    <td>{open === s.id ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</td>
                  </tr>
                  {open === s.id && (
                    <tr className="is-open"><td colSpan={7}>
                      <div className="ct-detail">
                        <pre>{s.text}</pre>
                        {s.actions?.length > 0 && <ul>{s.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>}
                        {orders.data?.length > 0 && (
                          <table className="ct-table">
                            <thead><tr><th>Leg</th><th>Ticket</th><th>Order</th><th>Lots</th><th>Entry</th><th>Stop</th><th>Target</th><th>Status</th></tr></thead>
                            <tbody>{orders.data.map((o) => (
                              <tr key={o.id}><td>TP{o.leg}</td><td className="num">{o.ticket ?? '—'}</td><td>{o.side} {o.kind.toLowerCase()}</td>
                                <td className="num">{o.volume}</td><td className="num">{o.entry}</td><td className="num">{o.sl}</td>
                                <td className="num">{o.tp ?? 'open'}</td><td title={o.detail}>{o.status.toLowerCase()}</td></tr>
                            ))}</tbody>
                          </table>
                        )}
                      </div>
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
  const { data: st, refetch, error, isFetching } = useQuery({ queryKey: qk('ct-status'), queryFn: () => copytrade.status().then((r) => r.data),
    refetchInterval: 15000 });
  const { data: channels = [] } = useQuery({ queryKey: qk('ct-channels'), queryFn: () => copytrade.channels().then((r) => r.data) });
  const [history, setHistory] = useState(null);
  const [testText, setTestText] = useState('');
  const save = (d) => copytrade.saveSettings(d).then(() => qc.invalidateQueries({ queryKey: qk('ct-status') }));
  const tg = st?.telegram;
  const setLive = (live) => {
    if (live && !window.confirm('Send REAL orders to the MT5 account this server is connected to?')) return;
    save({ dry_run: !live });
  };

  return (
    <div className="ct-page">
      <div className="ct-header">
        <div>
          <h2><Copy size={22} /> Copy Trading</h2>
          <p>
            Copies signals from Telegram channels into the MT5 account this server trades. A signal opens one position per target,
            sized from your total risk; entry zones become limit orders through the zone. Replies like <i>"SL to entry"</i>,
            <i> "move SL to 2352"</i>, <i>"close half"</i> or <i>"cancelling limit orders"</i> are applied to the signal they answer.
          </p>
          <div className="ct-status">
            {tg && <Pill tone={tg.logged_in ? 'green' : 'yellow'}>{tg.logged_in ? `Telegram · ${tg.user}` : 'Telegram not connected'}</Pill>}
            {tg?.logged_in && <Pill tone={tg.watching ? 'green' : 'red'}>{tg.watching ? `Watching ${tg.watched_chats} chat${tg.watched_chats === 1 ? '' : 's'}` : 'Not watching'}</Pill>}
            {st && <Pill tone={!st.enabled ? 'muted' : st.dry_run ? 'blue' : 'red'}>{!st.enabled ? 'Copying off' : st.dry_run ? 'Dry run' : 'Live orders'}</Pill>}
            <button className="ct-iconbtn" onClick={() => refetch()} title="Refresh"><RefreshCw size={13} className={isFetching ? 'spin' : ''} /></button>
          </div>
        </div>
        {st && (
          <div className="ct-controls">
            <Switch checked={st.enabled} onChange={(v) => save({ enabled: v })} label="Copying" sub={st.enabled ? 'Acting on new messages' : 'Paused'} />
            <span className="ct-controls__sep" />
            <div className="ct-field">
              <span className="ct-label">Mode</span>
              <div className="ct-seg">
                <button className={st.dry_run ? 'is-on is-dry' : ''} onClick={() => setLive(false)}>Dry run</button>
                <button className={!st.dry_run ? 'is-on is-live' : ''} onClick={() => !st.dry_run || setLive(true)}>Live</button>
              </div>
            </div>
          </div>
        )}
      </div>

      {tg && !tg.telethon && <Note tone="err">Telethon is not installed on this server - redeploy to install it.</Note>}
      {tg?.last_error && <Note tone="warn">Last error: {tg.last_error}</Note>}
      {error && <Note tone="err">{apiError(error)}</Note>}

      <div className="ct-grid">
        <div className="ct-col">
          <Card icon={<Send size={15} />} title="Telegram account"
            sub={tg?.logged_in ? 'The server reads your channels as you - shown in Telegram → Settings → Devices.' : 'Log in once; the server then watches your channels even when your phone is off.'}
            actions={tg?.logged_in && <button className="btn btn-sm btn-secondary" onClick={() => window.confirm('Log out of Telegram on this server?') && copytrade.logout().then(() => refetch())}><LogOut size={13} /> Log out</button>}>
            {tg?.logged_in ? (
              <div className="ct-account">
                <span className="ct-avatar">{initials(tg.user)}</span>
                <div>
                  <div style={{ fontWeight: 600 }}>{tg.user}</div>
                  <div className="ct-hint">{tg.phone} · {tg.connected ? 'connected' : 'reconnecting…'}</div>
                </div>
              </div>
            ) : st && <TelegramLogin tg={tg} onDone={() => { refetch(); qc.invalidateQueries({ queryKey: qk('ct-dialogs') }); }} />}
          </Card>
          <Channels loggedIn={!!tg?.logged_in} onHistory={(c) => setHistory(history?.id === c.id ? null : c)} historyId={history?.id} />
          {history && <HistoryPanel channel={history} onClose={() => setHistory(null)} onTest={setTestText} />}
        </div>
        <div className="ct-col">
          <ParseTester text={testText} setText={setTestText} channels={channels} />
        </div>
      </div>

      {st && <Rules rules={st.rules} defaults={st.defaults} onSave={(rules) => save({ rules })} />}
      <SignalLog channels={channels} />
    </div>
  );
}
