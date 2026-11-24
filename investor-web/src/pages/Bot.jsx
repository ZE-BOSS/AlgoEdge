import { useState } from 'react';
import { api } from '../api';
import { useLoad, useSubmit } from '../hooks';
import { Badge, Card, Field, Loaded, PasswordInput, Problem } from '../components/ui';
import { day } from '../format';

/**
 * VVIP: our bot trades the member's own MT5 account.
 *
 * The money never leaves their broker account. They connect the account after
 * accepting the terms, choose their risk (the server caps it at 2% a trade),
 * and can pause or disconnect at any time - disconnecting deletes the stored
 * password.
 */
export default function Bot() {
  const q = useLoad(api.vvipMe);
  return (
    <Loaded q={q}>
      {(d) => (
        <div className="stack">
          {!d.level ? <Plans d={d} /> : d.account && d.account.state !== 'STOPPED'
            ? <Connected d={d} reload={q.reload} />
            : <Connect d={d} reload={q.reload} />}
        </div>
      )}
    </Loaded>
  );
}

function Plans({ d }) {
  const p = d.prices || {};
  return (
    <Card title="Let our bot trade your account">
      <p className="muted">
        VVIP connects our trading bot to your own MT5 account. Your money stays with your broker, in your name;
        you can pause or disconnect at any time.
      </p>
      <ul className="plain-list">
        <li><strong>Starter, ${p.STARTER}</strong>: live trading on your account</li>
        <li><strong>Pro, ${p.PRO}</strong>: plus backtesting</li>
        <li><strong>Elite, ${p.ELITE}</strong>: plus order-flow data</li>
      </ul>
      <p className="muted small">Each plan runs for {d.period_days} days. To join, message the admin through our Telegram bot.</p>
    </Card>
  );
}

function Connect({ d, reload }) {
  const [f, setF] = useState({ broker: 'Exness', mt5_login: '', mt5_server: '', mt5_password: '', risk_pct: 1, accept_terms: false });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const { busy, error, submit } = useSubmit(async () => {
    await api.vvipConnect({ ...f, risk_pct: Number(f.risk_pct) });
    reload();
  });
  return (
    <Card title="Connect your MT5 account" aside={<Badge tone="good">{d.level}</Badge>}>
      <form className="form" onSubmit={submit}>
        <Field label="Broker"><input value={f.broker} onChange={set('broker')} required /></Field>
        <Field label="MT5 login" hint="Your account number"><input inputMode="numeric" value={f.mt5_login} onChange={set('mt5_login')} required /></Field>
        <Field label="Server" hint="As shown in MT5, e.g. Exness-MT5Real8"><input value={f.mt5_server} onChange={set('mt5_server')} required /></Field>
        <Field label="Password" hint="Your trading password - stored encrypted, deleted if you disconnect. Never your broker's website password if it differs.">
          <PasswordInput autoComplete="off" value={f.mt5_password} onChange={set('mt5_password')} required />
        </Field>
        <Field label="Risk per trade (%)" hint="Between 0.1 and 2. We recommend 1.">
          <input type="number" min="0.1" max="2" step="0.1" value={f.risk_pct} onChange={set('risk_pct')} />
        </Field>
        <label className="check"><input type="checkbox" checked={f.accept_terms} onChange={set('accept_terms')} /> <span className="small">{d.terms}</span></label>
        <Problem error={error} />
        <button className="btn" disabled={busy || !f.accept_terms}>{busy ? 'Connecting…' : 'Connect'}</button>
      </form>
    </Card>
  );
}

const STATE = { NEW: ['Setting up', 'neutral'], PROVISIONING: ['Setting up', 'neutral'], ACTIVE: ['Trading', 'good'], PAUSED: ['Paused', 'warn'] };
const num = (v, d = 2) => (v == null ? '-' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));

function Spark({ data }) {
  if (!data || data.length < 2) return null;
  const w = 240, h = 48, lo = Math.min(0, ...data), hi = Math.max(0, ...data), span = hi - lo || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - lo) / span) * h}`).join(' ');
  const zero = h - ((0 - lo) / span) * h;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Cumulative result in R">
      <line x1="0" x2={w} y1={zero} y2={zero} stroke="currentColor" strokeOpacity="0.2" />
      <polyline points={pts} fill="none" stroke="var(--gold, #d4a72c)" strokeWidth="1.8" />
    </svg>
  );
}

function Backtests() {
  const q = useLoad(api.vvipBacktests);
  const [f, setF] = useState({ market: 'Gold', months: 6, note: '' });
  const [sent, setSent] = useState(false);
  const ask = useSubmit(async () => { await api.vvipAskBacktest({ ...f, months: Number(f.months) }); setSent(true); q.reload(); });
  return (
    <Card title="Backtests">
      <Loaded q={q}>
        {(d) => (
          <div className="stack">
            {d.systems.length === 0 && <p className="muted">No backtests published yet.</p>}
            {d.systems.map((s) => (
              <div key={s.label} style={{ borderBottom: '1px solid var(--line, #2a2a2a)', paddingBottom: 10 }}>
                <p><strong>{s.label}</strong> <span className="muted small">{s.from} to {s.to}</span></p>
                <div className="row" style={{ gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
                  <Spark data={s.curve} />
                  <span className="small">{s.trades} trades · win rate {num(s.win_rate, 1)}% · profit factor {num(s.profit_factor)} ·
                    {' '}{num(s.total_r)}R total · worst drop {num(s.max_drawdown_pct, 1)}%</span>
                </div>
                <p className="muted small">By month (R): {Object.entries(s.months).slice(-12).map(([m, r]) => `${m.slice(2)} ${r > 0 ? '+' : ''}${r}`).join(' · ')}</p>
              </div>
            ))}
            <p className="muted small">Backtests show how the systems would have done in the past. They don't promise future results.</p>
            <form className="form" onSubmit={ask.submit}>
              <p><strong>Ask for a backtest</strong></p>
              <Field label="Market"><input value={f.market} onChange={(e) => setF({ ...f, market: e.target.value })} required /></Field>
              <Field label="Months"><input type="number" min="1" max="36" value={f.months} onChange={(e) => setF({ ...f, months: e.target.value })} /></Field>
              <Field label="Note (optional)"><input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
              <Problem error={ask.error} />
              {sent && <p className="good small">Sent. The results will appear here.</p>}
              <button className="btn" disabled={ask.busy}>Send request</button>
            </form>
            {d.requests.length > 0 && <p className="muted small">Your requests: {d.requests.map((r) => `${r.market} (${r.months} mo, ${r.status.toLowerCase()})`).join(' · ')}</p>}
          </div>
        )}
      </Loaded>
    </Card>
  );
}

function OrderFlow() {
  const q = useLoad(api.vvipOrderflow);
  return (
    <Card title="Order flow (exchange data)">
      <Loaded q={q}>
        {(d) => (
          <div className="stack">
            {!d.live && <p className="muted small">The exchange feed is not connected right now; levels may be old.</p>}
            {d.markets.length === 0 && <p className="muted">No order-flow data yet.</p>}
            {d.markets.map((m) => (
              <div key={m.cfd}>
                <p><strong>{m.name}</strong> <span className="muted small">in {m.cfd} prices · session {m.session}</span></p>
                <p className="small">
                  Most traded price (POC) <strong>{num(m.levels.poc)}</strong> · value area {num(m.levels.val)} – {num(m.levels.vah)} · VWAP {num(m.levels.vwap)}
                </p>
                <p className="small">
                  Buyers vs sellers today: <strong className={m.delta >= 0 ? 'good' : 'bad'}>{m.delta >= 0 ? '+' : ''}{num(m.delta, 0)}</strong> contracts
                  {' '}(last 30 min {m.delta_30m >= 0 ? '+' : ''}{num(m.delta_30m, 0)})
                </p>
                {m.levels.prev && <p className="muted small">Yesterday: POC {num(m.levels.prev.poc)} · value {num(m.levels.prev.val)} – {num(m.levels.prev.vah)} · high {num(m.levels.prev.high)} · low {num(m.levels.prev.low)}</p>}
                {(m.levels.big_prints || []).length > 0 && (
                  <p className="muted small">Large trades: {m.levels.big_prints.slice(-5).reverse().map((b) => `${b.side} ${b.size} @ ${num(b.price)}`).join(' · ')}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </Loaded>
    </Card>
  );
}

function Connected({ d, reload }) {
  const a = d.account;
  const [risk, setRisk] = useState(a.risk_pct);
  const save = useSubmit(async () => { await api.vvipSettings({ risk_pct: Number(risk) }); reload(); });
  const pause = useSubmit(async () => { await api.vvipSettings({ pause: a.state !== 'PAUSED' }); reload(); });
  const stop = useSubmit(async () => { await api.vvipSettings({ disconnect: true }); reload(); });
  const [label, tone] = STATE[a.state] || [a.state, 'neutral'];
  return (
    <>
      <Card title="Your bot" aside={<Badge tone={tone}>{label}</Badge>}>
        <p className="muted small">
          {a.broker} · login {a.mt5_login} · {a.mt5_server} · {d.level}
          {d.expires_at && <> · plan runs until {day(d.expires_at)}</>}
        </p>
        {['NEW', 'PROVISIONING'].includes(a.state) && <p className="muted">We're setting up your terminal. You'll get a message when the bot starts trading.</p>}
        {a.live?.equity != null && (
          <div className="stat-grid">
            <div><p className="muted small">Balance</p><strong>{num(a.live.balance)} {a.live.currency}</strong></div>
            <div><p className="muted small">Equity</p><strong>{num(a.live.equity)}</strong></div>
            <div><p className="muted small">Today</p><strong className={a.live.day_pnl >= 0 ? 'good' : 'bad'}>{a.live.day_pnl >= 0 ? '+' : ''}{num(a.live.day_pnl)}</strong></div>
            <div><p className="muted small">Below peak</p><strong>{num(a.live.drawdown_pct, 1)}%</strong></div>
            <div><p className="muted small">Open trades</p><strong>{a.live.open_positions}</strong></div>
          </div>
        )}
        {a.paused_reason && <p className="muted small">{a.paused_reason}</p>}
        <form className="form" onSubmit={save.submit}>
          <Field label="Risk per trade (%)" hint={`Daily loss stop ${a.daily_loss_pct}%, drawdown stop ${a.max_dd_pct}%.`}>
            <input type="number" min="0.1" max="2" step="0.1" value={risk} onChange={(e) => setRisk(e.target.value)} />
          </Field>
          <Problem error={save.error || pause.error || stop.error} />
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <button className="btn" disabled={save.busy}>Save</button>
            <button type="button" className="btn" onClick={pause.submit} disabled={pause.busy}>{a.state === 'PAUSED' ? 'Resume' : 'Pause'}</button>
            <button type="button" className="btn danger-outline" onClick={stop.submit} disabled={stop.busy}>Disconnect</button>
          </div>
        </form>
      </Card>
      {d.features?.backtest ? <Backtests /> : <Card title="Backtests"><p className="muted">Backtests come with Pro and Elite.</p></Card>}
      {d.features?.orderflow ? <OrderFlow /> : <Card title="Order flow"><p className="muted">Exchange order-flow levels come with Elite.</p></Card>}
    </>
  );
}
