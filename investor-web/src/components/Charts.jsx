import { useEffect, useMemo, useRef, useState } from 'react';
import { compact, day, money, monthName } from '../format';
import { usePrefs } from '../usePrefs';

/*
 * Charts for one investor's own money. Colours were run through the palette
 * validator against the card surface (#111822, dark): the balance line and
 * the money-put-in line are a validated pair. Gain and loss use the reserved
 * good/bad colours and always carry a sign and a label as well, so they never
 * rely on colour alone.
 */
const SERIES = { balance: '#b08a1a', invested: '#4a86d8' };

function useWidth(min = 280) {
  const ref = useRef(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  return [ref, w];
}

const RANGES = [['1M', 31], ['3M', 92], ['6M', 183], ['1Y', 366], ['ALL', null]];

export function RangeTabs({ value, onChange }) {
  return (
    <div className="seg" role="tablist" aria-label="Time range">
      {RANGES.map(([k]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? 'on' : ''}
                onClick={() => onChange(k)}>{k === 'ALL' ? 'All' : k}</button>
      ))}
    </div>
  );
}

function inRange(points, range) {
  const days = (RANGES.find(([k]) => k === range) || [])[1];
  if (!days || !points.length) return points;
  const last = new Date(`${points.at(-1).date}T12:00:00Z`).getTime();
  return points.filter((p) => last - new Date(`${p.date}T12:00:00Z`).getTime() <= days * 864e5);
}

/**
 * Your balance against the money you have put in (net of withdrawals). Where
 * the balance is above the line, that gap is profit; below it, the gap is
 * capital being lost. Crosshair + tooltip; "Show as table" for the numbers.
 */
export function BalanceChart({ points: all, range, onRange }) {
  const { hidden } = usePrefs();
  const [box, w] = useWidth();
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);
  const mine = useMemo(() => (all || []).filter((p) => p.value !== null && p.value !== undefined), [all]);
  const points = useMemo(() => inRange(mine, range), [mine, range]);

  if (mine.length < 2) {
    return <p className="muted">Your balance chart starts once the fund has been valued on two days since your money arrived.</p>;
  }
  const H = 240, P = { t: 14, r: 12, b: 26, l: 60 };
  const val = points.map((p) => Number(p.value));
  const inv = points.map((p) => Number(p.net_invested));
  let lo = Math.min(...val, ...inv), hi = Math.max(...val, ...inv);
  if (hi - lo < 1e-6) { lo -= Math.max(1, hi * 0.02); hi += Math.max(1, hi * 0.02); }
  const pad = (hi - lo) * 0.1; lo -= pad; hi += pad;
  const iw = w - P.l - P.r, ih = H - P.t - P.b;
  const n = Math.max(1, points.length - 1);
  const x = (i) => P.l + (i / n) * iw;
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * ih;
  const line = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');

  // the gap between the two lines, green where the balance is above, red below;
  // a segment that crosses is split at the crossing so the colours meet there
  const gaps = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const d0 = val[i] - inv[i], d1 = val[i + 1] - inv[i + 1];
    const seg = (a, b, sign) => gaps.push({ sign, d: `M${x(a.i)},${y(a.v)}L${x(b.i)},${y(b.v)}L${x(b.i)},${y(b.u)}L${x(a.i)},${y(a.u)}Z` });
    const A = { i, v: val[i], u: inv[i] }, B = { i: i + 1, v: val[i + 1], u: inv[i + 1] };
    if ((d0 >= 0) === (d1 >= 0)) seg(A, B, d0 + d1 >= 0 ? 1 : -1);
    else {
      const t = d0 / (d0 - d1);
      const C = { i: i + t, v: val[i] + t * (val[i + 1] - val[i]), u: inv[i] + t * (inv[i + 1] - inv[i]) };
      seg(A, C, d0 >= 0 ? 1 : -1); seg(C, B, d1 >= 0 ? 1 : -1);
    }
  }
  const ticks = [0, 0.5, 1].map((f) => lo + pad + f * (hi - lo - 2 * pad));
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(Math.min(points.length - 1, Math.max(0, Math.round(((e.clientX - r.left - P.l) / iw) * n))));
  };
  const hp = hover != null ? points[hover] : null;
  const hDiff = hp ? Number(hp.value) - Number(hp.net_invested) : 0;

  return (
    <div>
      <div className="chart-top">
        <div className="legend">
          <span><i style={{ background: SERIES.balance }} />Your balance</span>
          <span><i style={{ background: SERIES.invested }} />Money you put in</span>
          <span><i className="swatch-good" />Profit</span>
          <span><i className="swatch-bad" />Capital lost</span>
        </div>
        <RangeTabs value={range} onChange={onRange} />
      </div>
      <div className="chart" ref={box}>
        {table ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th className="num">Balance</th><th className="num">Put in</th><th className="num">Difference</th></tr></thead>
              <tbody>
                {[...points].reverse().map((p) => {
                  const d = Number(p.value) - Number(p.net_invested);
                  return (
                    <tr key={p.date}><td>{day(p.date)}</td>
                      <td className="num">{hidden ? '••••' : money(p.value)}</td>
                      <td className="num">{hidden ? '••••' : money(p.net_invested)}</td>
                      <td className={`num ${d < 0 ? 'bad' : 'good'}`}>{hidden ? '••••' : `${d < 0 ? '−' : '+'}${money(Math.abs(d).toFixed(2))}`}</td></tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <svg width={w} height={H} role="img" onPointerMove={onMove} onPointerLeave={() => setHover(null)}
               aria-label={`Your balance against the money you put in, ${day(points[0].date)} to ${day(points.at(-1).date)}`}>
            {ticks.map((t) => (
              <g key={t}>
                <line className="grid" x1={P.l} x2={w - P.r} y1={y(t)} y2={y(t)} />
                <text className="axis" x={P.l - 8} y={y(t)} dy="0.32em" textAnchor="end">{hidden ? '•••' : compact(t)}</text>
              </g>
            ))}
            <text className="axis" x={P.l} y={H - 6}>{day(points[0].date)}</text>
            <text className="axis" x={w - P.r} y={H - 6} textAnchor="end">{day(points.at(-1).date)}</text>
            {gaps.map((g, i) => <path key={i} d={g.d} className={g.sign > 0 ? 'gap-good' : 'gap-bad'} />)}
            <path d={line(inv)} fill="none" stroke={SERIES.invested} strokeWidth="2" strokeLinejoin="round" />
            <path d={line(val)} fill="none" stroke={SERIES.balance} strokeWidth="2" strokeLinejoin="round" />
            <circle cx={x(points.length - 1)} cy={y(val.at(-1))} r="4.5" fill={SERIES.balance} className="ring" />
            {hp && (
              <g>
                <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={P.t} y2={P.t + ih} />
                <circle cx={x(hover)} cy={y(inv[hover])} r="4" fill={SERIES.invested} className="ring" />
                <circle cx={x(hover)} cy={y(val[hover])} r="5" fill={SERIES.balance} className="ring" />
              </g>
            )}
          </svg>
        )}
        {!table && hp && (
          <div className="tip wide" style={{ left: Math.min(Math.max(x(hover), 110), w - 110) }}>
            <span className="muted">{day(hp.date)}</span>
            <span><i style={{ background: SERIES.balance }} />Balance <b>{hidden ? '••••' : money(hp.value)}</b></span>
            <span><i style={{ background: SERIES.invested }} />Put in <b>{hidden ? '••••' : money(hp.net_invested)}</b></span>
            <span className={hDiff < 0 ? 'bad' : 'good'}>
              {hDiff < 0 ? 'Capital lost' : 'Profit'} <b>{hidden ? '••••' : `${hDiff < 0 ? '−' : '+'}${money(Math.abs(hDiff).toFixed(2))}`}</b></span>
          </div>
        )}
      </div>
      <button className="link small" onClick={() => setTable((t) => !t)}>{table ? 'Show as chart' : 'Show as table'}</button>
    </div>
  );
}

/** Your profit or loss in dollars, month by month. Hover a bar for the sum. */
export function MonthBars({ months }) {
  const { hidden } = usePrefs();
  const [box, w] = useWidth();
  const [hover, setHover] = useState(null);
  const list = (months || []).slice(-12);
  if (!list.length) return <p className="muted">Your first month appears here once your money has been in for a month end.</p>;
  const H = 200, P = { t: 18, r: 8, b: 26, l: 8 };
  const vals = list.map((m) => Number(m.profit));
  const max = Math.max(1, ...vals.map(Math.abs));
  const mid = P.t + (H - P.t - P.b) / 2;
  const half = (H - P.t - P.b) / 2 - 14;
  const bw = (w - P.l - P.r) / list.length;
  const hm = hover != null ? list[hover] : null;
  return (
    <div className="chart" ref={box}>
      <svg width={w} height={H} role="img" aria-label="Your profit or loss each month"
           onPointerLeave={() => setHover(null)}>
        <line className="zero" x1={P.l} x2={w - P.r} y1={mid} y2={mid} />
        {list.map((m, i) => {
          const v = vals[i], h = Math.max(2, (Math.abs(v) / max) * half);
          const cx = P.l + i * bw + bw / 2, bwid = Math.min(30, bw * 0.62);
          const top = v < 0 ? mid : mid - h;
          const r = Math.min(4, bwid / 2, h);
          // rounded at the data end only, square on the baseline
          const d = v < 0
            ? `M${cx - bwid / 2},${mid} h${bwid} v${h - r} q0,${r} -${r},${r} h-${bwid - 2 * r} q-${r},0 -${r},-${r} Z`
            : `M${cx - bwid / 2},${mid} v-${h - r} q0,-${r} ${r},-${r} h${bwid - 2 * r} q${r},0 ${r},${r} v${h - r} Z`;
          return (
            <g key={m.month} onPointerEnter={() => setHover(i)}>
              <rect x={cx - bw / 2} y={P.t} width={bw} height={H - P.t - P.b} fill="transparent" />
              <path d={d} className={v < 0 ? 'bar-bad' : 'bar-good'} opacity={hover == null || hover === i ? 1 : 0.45} />
              {bw > 38 && !hidden && (
                <text className="axis" x={cx} textAnchor="middle" y={v < 0 ? top + h + 12 : top - 5}>{compact(v)}</text>
              )}
              <text className="axis" x={cx} y={H - 6} textAnchor="middle">{monthName(m.month, { short: true })}</text>
            </g>
          );
        })}
      </svg>
      {hm && (
        <div className="tip wide" style={{ left: Math.min(Math.max(P.l + hover * bw + bw / 2, 110), w - 110) }}>
          <span className="muted">{monthName(hm.month)}</span>
          <span>Start <b>{hidden ? '••••' : money(hm.start_value)}</b></span>
          <span>Money in or out <b>{hidden ? '••••' : money(hm.money_in_out, { sign: true })}</b></span>
          <span>End <b>{hidden ? '••••' : money(hm.end_value)}</b></span>
          <span className={Number(hm.profit) < 0 ? 'bad' : 'good'}>Result <b>{hidden ? '••••' : money(hm.profit, { sign: true })}</b>
            {hm.fund_return_pct && ` (fund ${Number(hm.fund_return_pct) > 0 ? '+' : ''}${hm.fund_return_pct}%)`}</span>
        </div>
      )}
    </div>
  );
}

/**
 * What your balance is made of: the capital still there, and either profit on
 * top of it or the part of your capital that has been lost. Labelled, so the
 * colours are never the only cue.
 */
export function CapitalBar({ c }) {
  const { hidden } = usePrefs();
  const capital = Number(c.capital_intact), profit = Number(c.profit_on_top), lost = Number(c.capital_eroded);
  const whole = capital + profit + lost || 1;
  const seg = (v) => `${Math.max(0, (v / whole) * 100)}%`;
  const h = (v) => (hidden ? '••••' : money(String(v)));
  return (
    <div className="capbar">
      <div className="capbar-track" role="img"
           aria-label={`Capital ${h(c.capital_intact)}, ${profit > 0 ? `profit ${h(c.profit_on_top)}` : `capital lost ${h(c.capital_eroded)}`}`}>
        {capital > 0 && <span className="seg-capital" style={{ width: seg(capital) }} />}
        {profit > 0 && <span className="seg-profit" style={{ width: seg(profit) }} />}
        {lost > 0 && <span className="seg-lost" style={{ width: seg(lost) }} />}
      </div>
      <div className="capbar-keys">
        <span><i className="k-capital" />Your capital <b className="num">{h(c.capital_intact)}</b></span>
        {profit > 0 && <span><i className="k-profit" />Profit on top <b className="num good">+{h(c.profit_on_top)}</b></span>}
        {lost > 0 && <span><i className="k-lost" />Capital lost <b className="num bad">−{h(c.capital_eroded)}</b></span>}
      </div>
    </div>
  );
}

/** Your result by market: diverging bars from zero, every value labelled. */
export function SymbolBars({ rows }) {
  const { hidden } = usePrefs();
  if (!rows?.length) return null;
  const max = Math.max(1, ...rows.map((r) => Math.abs(Number(r.amount))));
  return (
    <div className="symbars">
      {rows.slice(0, 10).map((r) => {
        const v = Number(r.amount), wpct = (Math.abs(v) / max) * 50;
        return (
          <div key={r.symbol} className="symrow">
            <span className="sym">{r.symbol}</span>
            <span className="symtrack">
              <span className={v < 0 ? 'neg' : 'pos'} style={{ width: `${wpct}%` }} />
            </span>
            <span className={`num ${v < 0 ? 'bad' : 'good'}`}>{hidden ? '••••' : money(r.amount, { sign: true })}</span>
          </div>
        );
      })}
    </div>
  );
}
