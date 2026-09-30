import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useLoad } from '../hooks';
import { usePrefs } from '../usePrefs';
import { Amount, Card, Loaded, Stat } from '../components/ui';
import { BalanceChart, CapitalBar, MonthBars } from '../components/Charts';
import Icon from '../components/Icons';
import { day, isNeg, monthName, pct, sinceLaunch } from '../format';

function CapitalMessage({ c }) {
  if (c.state === 'not_invested') {
    return (
      <div className="banner">
        <Icon name="info" /><div><strong>Your account opens when your first transfer arrives.</strong>
          <span>From then on this page shows your balance, the money you put in, and whether it is
            earning profit or losing capital. <Link to="/money">Add money</Link></span></div>
      </div>
    );
  }
  if (c.state === 'profit') {
    return (
      <div className="banner banner-good">
        <Icon name="up" /><div><strong>Your capital is intact, with <Amount v={c.profit} /> of profit on top.</strong>
          <span>That is {pct(c.profit_pct_of_capital)} on the <Amount v={c.net_invested} /> you have
            put in, after fees.</span></div>
      </div>
    );
  }
  return (
    <div className="banner banner-bad">
      <Icon name="down" /><div><strong>Losses are eating into your capital: <Amount v={c.capital_eroded} /> so far.</strong>
        <span>Your balance is {pct(c.profit_pct_of_capital)} below the <Amount v={c.net_invested} /> you
          have put in. No performance fee is charged until this is earned back.</span></div>
    </div>
  );
}

function RecentTrades() {
  const q = useLoad(api.trades);
  return (
    <Card title="Your share of recent trades" aside={<Link to="/trades" className="small">All trades</Link>}>
      <Loaded q={q}>
        {({ trades }) => {
          const mine = trades.filter((t) => t.your_amount !== null).slice(0, 5);
          if (!mine.length) return <p className="muted">Trades closed while your money is in the fund appear here, with your share of each result.</p>;
          return (
            <ul className="trade-list">
              {mine.map((t) => (
                <li key={t.id}>
                  <span className={`dir ${t.direction === 'BUY' ? 'long' : 'short'}`}>{t.direction === 'BUY' ? 'Buy' : 'Sell'}</span>
                  <div><strong>{t.symbol}</strong><span className="muted small">{day(t.closed_on)}</span></div>
                  <div className="right">
                    <Amount v={t.your_amount} sign className={isNeg(t.your_amount) ? 'bad' : 'good'} />
                    {t.result_pct && <span className="muted small">{pct(t.result_pct)} on the fund</span>}
                  </div>
                </li>
              ))}
            </ul>
          );
        }}
      </Loaded>
    </Card>
  );
}

function Breakdown({ s, c }) {
  return (
    <Card title="Where your numbers come from" aside={<Link to="/how" className="small">Full explanation</Link>}>
      <div className="calc">
        <div className="calc-row"><span>Money you put in</span><Amount v={c.paid_in} /></div>
        <div className="calc-row"><span>Money paid out to you</span><span className="op">−</span><Amount v={c.paid_out} /></div>
        <div className="calc-row total"><span>Net invested</span><Amount v={c.net_invested} /></div>
        <div className="calc-row"><span>Your balance today</span><Amount v={c.value} /></div>
        <div className="calc-row"><span>Net invested</span><span className="op">−</span><Amount v={c.net_invested} /></div>
        <div className={`calc-row total ${isNeg(c.profit) ? 'bad' : 'good'}`}>
          <span>{isNeg(c.profit) ? 'Capital lost' : 'Profit'}</span><Amount v={c.profit} sign /></div>
        <p className="muted small">Fees are already taken off the balance: <Amount v={c.fees_paid} /> in total
          so far. Your balance moves by the same percentage as the fund: {sinceLaunch(s.nav_per_unit)} since
          launch. You hold {s.share_of_pool_pct}% of it.</p>
      </div>
    </Card>
  );
}

export default function Home() {
  const me = useLoad(api.me);
  const nav = useLoad(api.nav);
  const perf = useLoad(api.performance);
  const { prefs } = usePrefs();
  // the saved default until the investor picks another range on this page
  const [picked, setRange] = useState(null);
  const range = picked || prefs.chart_range || 'ALL';

  return (
    <Loaded q={me}>
      {({ investor, statement: s, capital: c, lockup_until: lockup }) => {
        const st = perf.data?.stats;
        return (
          <div className="stack">
            <section className="hero-card">
              <div className="hero-main">
                <span className="eyebrow">Your balance · {day(s.as_of)}</span>
                <div className="hero-value"><Amount v={s.current_value} forceFull /></div>
                <div className={`hero-change ${isNeg(s.profit) ? 'bad' : 'good'}`}>
                  <Icon name={isNeg(s.profit) ? 'down' : 'up'} size={18} />
                  <Amount v={s.profit} sign />
                  {c.profit_pct_of_capital && <span>({pct(c.profit_pct_of_capital)})</span>}
                  <span className="muted">since you joined</span>
                </div>
              </div>
              <div className="hero-side">
                <div><span>Available to withdraw</span><strong><Amount v={s.withdrawable_now} /></strong></div>
                <div><span>Fund since launch</span><strong>{sinceLaunch(s.nav_per_unit)}</strong></div>
                <div className="hero-actions">
                  <Link className="btn primary small" to="/money">Add money</Link>
                  <Link className="btn small" to="/money/withdraw">Withdraw</Link>
                </div>
              </div>
            </section>

            {investor.status === 'closing' && (
              <div className="notice">You have asked to close your account. We will pay out your balance and
                confirm by email; nothing else is needed from you.</div>
            )}
            <CapitalMessage c={c} />

            {c.state !== 'not_invested' && (
              <Card title="What your balance is made of"><CapitalBar c={c} /></Card>
            )}

            <Card title="Your balance against the money you put in">
              <Loaded q={nav}>{(points) => <BalanceChart points={points} range={range} onRange={setRange} />}</Loaded>
            </Card>

            <div className="stat-grid">
              <Stat label="Total return" icon="chart" tone={isNeg(s.profit) ? 'bad' : 'good'}
                    note={<>on <Amount v={c.net_invested} /> net invested</>}>
                {c.profit_pct_of_capital ? pct(c.profit_pct_of_capital) : '—'}</Stat>
              <Stat label="Paid in" icon="arrowIn"><Amount v={c.paid_in} /></Stat>
              <Stat label="Paid out to you" icon="arrowOut"><Amount v={c.paid_out} /></Stat>
              <Stat label="Fees paid" icon="fee" note="management and performance"><Amount v={c.fees_paid} /></Stat>
              <Stat label="Share of the fund" icon="user">{s.share_of_pool_pct}%</Stat>
              <Stat label="Days invested" icon="activity"
                    note={st ? `since ${day(st.first_deposit)}` : null}>{st ? st.days_invested : '—'}</Stat>
              <Stat label="Best month" icon="up" tone="good"
                    note={st ? monthName(st.best_month.month) : null}>{st ? <Amount v={st.best_month.profit} sign /> : '—'}</Stat>
              <Stat label="Worst month" icon="down" tone={st && isNeg(st.worst_month.profit) ? 'bad' : undefined}
                    note={st ? monthName(st.worst_month.month) : null}>{st ? <Amount v={st.worst_month.profit} sign /> : '—'}</Stat>
              <Stat label="Largest fall" icon="down"
                    note="from a previous high, since you joined">{st ? pct(st.largest_fall_pct) : '—'}</Stat>
              <Stat label="Months up / down" icon="chart">{st ? `${st.months_up} / ${st.months_down}` : '—'}</Stat>
            </div>

            <div className="two-col">
              <Card title="Your result each month">
                <Loaded q={perf}>{(p) => <MonthBars months={p.months} />}</Loaded>
              </Card>
              <RecentTrades />
            </div>

            <Breakdown s={s} c={c} />

            <Card title="Available to withdraw" aside={<Link className="btn small" to="/money/withdraw">Withdraw</Link>}>
              <div className="big"><Amount v={s.withdrawable_now} /></div>
              <p className="muted">
                {s.withdrawable_explanation.charAt(0).toUpperCase() + s.withdrawable_explanation.slice(1)}.
                {lockup && new Date(`${lockup}T00:00:00Z`) > new Date() &&
                  ` Your money is in its lock-up period until ${day(lockup)}.`}
              </p>
            </Card>
          </div>
        );
      }}
    </Loaded>
  );
}
