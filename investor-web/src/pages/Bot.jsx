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

const STATE = { NEW: ['Setting up', 'neutral'], ACTIVE: ['Trading', 'good'], PAUSED: ['Paused', 'warn'] };

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
        {a.state === 'NEW' && <p className="muted">We're setting up your terminal. You'll get a message when the bot starts trading.</p>}
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
      <Card title="Backtests">
        {d.features?.backtest
          ? <p className="muted">Backtests for your plan are run by the admin on request for now - message them through the Telegram bot.</p>
          : <p className="muted">Backtests come with Pro and Elite.</p>}
      </Card>
    </>
  );
}
