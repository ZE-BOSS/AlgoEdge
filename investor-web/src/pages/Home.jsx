import { Link } from 'react-router-dom';
import { api } from '../api';
import { useLoad } from '../hooks';
import { Card, Loaded } from '../components/ui';
import NavChart from '../components/NavChart';
import { day, isNeg, money, sinceLaunch } from '../format';

export default function Home() {
  const me = useLoad(api.me);
  const nav = useLoad(api.nav);
  return (
    <Loaded q={me}>
      {({ investor, statement: s, lockup_until: lockup }) => (
        <div className="stack">
          <section className="hero">
            <span className="eyebrow">Your holding · {day(s.as_of)}</span>
            <div className="hero-value">{money(s.current_value)}</div>
            <div className={isNeg(s.profit) ? 'bad' : 'good'}>
              {money(s.profit, { sign: true })} <span className="muted">since you joined</span>
            </div>
            <Link className="link small" to="/how">How this is calculated</Link>
          </section>

          {investor.status === 'closing' && (
            <div className="notice">You have asked to close your account. We will pay out your holding
              and confirm by email; nothing else is needed from you.</div>
          )}
          {investor.status === 'pending' && (
            <div className="notice">Your account opens when your first transfer is confirmed.{' '}
              <Link to="/money">Add money</Link></div>
          )}

          <div className="tiles">
            <div className="tile"><span>Paid in</span><strong>{money(s.capital_in)}</strong></div>
            <div className="tile"><span>Paid out to you</span><strong>{money(s.withdrawn)}</strong></div>
            <div className="tile"><span>Your share of the fund</span><strong>{s.share_of_pool_pct}%</strong></div>
            <div className="tile"><span>Fund since launch</span><strong>{sinceLaunch(s.nav_per_unit)}</strong></div>
          </div>

          <Loaded q={nav}>{(points) => {
            const mine = points.some((p) => p.value !== null);
            return (
              <Card title={mine ? 'Your holding value' : 'What $100 invested at launch is worth'}>
                <NavChart points={points} field={mine ? 'value' : 'nav_per_unit'} />
              </Card>
            );
          }}</Loaded>

          <Card title="Available to withdraw" aside={<Link className="btn small" to="/money/withdraw">Withdraw</Link>}>
            <div className="big">{money(s.withdrawable_now)}</div>
            <p className="muted">
              {s.withdrawable_explanation.charAt(0).toUpperCase() + s.withdrawable_explanation.slice(1)}.
              {lockup && new Date(`${lockup}T00:00:00Z`) > new Date() &&
                ` Your money is in its lock-up period until ${day(lockup)}.`}
            </p>
          </Card>
        </div>
      )}
    </Loaded>
  );
}
