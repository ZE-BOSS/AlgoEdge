import { Link } from 'react-router-dom';
import { api } from '../api';
import { useLoad } from '../hooks';
import { Card, Loaded } from '../components/ui';
import { money, units } from '../format';

/** "Why is my number X?" answered from the numbers themselves. */
export default function How() {
  const me = useLoad(api.me);
  return (
    <Loaded q={me}>
      {({ statement: s, terms }) => (
        <div className="stack">
          <Card title="How your value is calculated">
            <p>The fund is divided into <strong>units</strong>. When you add money you buy units at that
              day&rsquo;s price; when you withdraw, units are sold at that day&rsquo;s price. The price moves
              with the fund&rsquo;s trading results, so everyone&rsquo;s holding moves by the same percentage.
              The fund started at $100 a unit: a unit price of {money(s.nav_per_unit)} means every $100
              invested at launch is now worth {money(s.nav_per_unit)}.</p>
            <div className="sum">
              <div><span className="muted">Units you hold</span><strong className="num">{units(s.units)}</strong></div>
              <div className="op">×</div>
              <div><span className="muted">Unit price</span><strong className="num">{money(s.nav_per_unit)}</strong></div>
              <div className="op">=</div>
              <div><span className="muted">Your holding</span><strong className="num">{money(s.current_value)}</strong></div>
            </div>
            <p className="muted small">Why units and not a percentage: someone joining later buys at the
              later price, so they can never share in profit made before their money arrived — and nor
              can you in theirs.</p>
          </Card>

          <Card title="How your profit is calculated">
            <div className="sum">
              <div><span className="muted">Holding</span><strong className="num">{money(s.current_value)}</strong></div>
              <div className="op">+</div>
              <div><span className="muted">Paid out to you</span><strong className="num">{money(s.withdrawn)}</strong></div>
              <div className="op">−</div>
              <div><span className="muted">Paid in</span><strong className="num">{money(s.capital_in)}</strong></div>
              <div className="op">=</div>
              <div><span className="muted">Profit</span><strong className="num">{money(s.profit, { sign: true })}</strong></div>
            </div>
          </Card>

          <Card title="Your terms">
            <dl className="pairs">
              <dt>Performance fee</dt><dd>{terms.performance_fee_pct}% of new profit, after the management fee</dd>
              <dt>Minimum first deposit</dt><dd>{money(terms.min_investment)}</dd>
              <dt>Management fee</dt><dd>{terms.management_fee_pct}% of each two-month period&rsquo;s profit (nothing in a losing period)</dd>
              <dt>Standard withdrawal limit</dt><dd>{terms.withdrawal_cap_pct}% of each month&rsquo;s profit</dd>
              <dt>Lock-up</dt><dd>{terms.lockup_days} days from your first deposit</dd>
              <dt>Notice</dt><dd>{terms.notice_days} days to pay a standard withdrawal</dd>
            </dl>
            <p className="muted small">These are the terms in force when your money went in (version
              {' '}{terms.version}). If the fund&rsquo;s terms change later, money already committed keeps
              these. The performance fee is charged only on profit above your previous high point, so
              a loss is earned back before any fee is charged again.</p>
          </Card>
          <Link to="/" className="link">Back to overview</Link>
        </div>
      )}
    </Loaded>
  );
}
