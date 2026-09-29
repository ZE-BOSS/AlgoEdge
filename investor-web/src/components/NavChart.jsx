import { useEffect, useRef, useState } from 'react';
import { day, money } from '../format';

/**
 * One dollar series over time: the investor's holding value, or before they
 * hold anything, what $100 invested at launch is worth. One series, so no
 * legend: the card title names it. `field` picks the series.
 *
 * Geometry uses Number() — plotting needs coordinates — but every figure the
 * reader sees (axis, tooltip, table) is the API's own string, so the chart can
 * never display a rounded-by-float price.
 */
const H = 200, PAD = { t: 12, r: 12, b: 26, l: 64 };

// axis labels only: short dollar figures ($1.2k, $950)
function axisMoney(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}m`;
  if (a >= 1e4) return `$${(v / 1e3).toFixed(0)}k`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(a < 10 ? 2 : 0)}`;
}

export default function NavChart({ points: all, field = 'value' }) {
  const points = (all || []).filter((p) => p[field] !== null && p[field] !== undefined);
  const box = useRef(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!points || points.length < 2) {
    return <p className="muted">The chart appears once the fund has been valued on two separate days.</p>;
  }

  const ys = points.map((p) => Number(p[field]));
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1e-9) { lo -= Math.max(1, hi * 0.01); hi += Math.max(1, hi * 0.01); }
  const padY = (hi - lo) * 0.08;
  lo -= padY; hi += padY;
  const iw = w - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const x = (i) => PAD.l + (i / (points.length - 1)) * iw;
  const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * ih;
  const d = ys.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const ticks = [0, 0.5, 1].map((f) => lo + padY + f * (hi - lo - 2 * padY));

  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - PAD.l) / iw) * (points.length - 1));
    setHover(Math.min(points.length - 1, Math.max(0, i)));
  };
  const hp = hover != null ? points[hover] : null;

  return (
    <div>
      <div className="chart" ref={box}>
        {!table && (
          <svg width={w} height={H} role="img"
               aria-label={`Value in US dollars from ${day(points[0].date)} to ${day(points.at(-1).date)}`}
               onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
            {ticks.map((t) => (
              <g key={t}>
                <line className="grid" x1={PAD.l} x2={w - PAD.r} y1={y(t)} y2={y(t)} />
                <text className="axis" x={PAD.l - 8} y={y(t)} dy="0.32em" textAnchor="end">{axisMoney(t)}</text>
              </g>
            ))}
            <text className="axis" x={PAD.l} y={H - 6}>{day(points[0].date)}</text>
            <text className="axis" x={w - PAD.r} y={H - 6} textAnchor="end">{day(points.at(-1).date)}</text>
            <path d={d} className="line" />
            {hp && (
              <g>
                <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={PAD.t + ih} />
                <circle className="dot" cx={x(hover)} cy={y(ys[hover])} r="4.5" />
              </g>
            )}
            {/* the last point is always marked: it is today's figure */}
            <circle className="dot" cx={x(points.length - 1)} cy={y(ys.at(-1))} r="4" />
          </svg>
        )}
        {!table && hp && (
          <div className="tip" style={{ left: Math.min(Math.max(x(hover), 70), w - 70), top: 0 }}>
            <strong>{money(hp[field])}</strong><span>{day(hp.date)}</span>
          </div>
        )}
        {table && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th className="num">Value</th></tr></thead>
              <tbody>
                {[...points].reverse().map((p) => (
                  <tr key={p.date}><td>{day(p.date)}</td><td className="num">{money(p[field])}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <button className="link small" onClick={() => setTable((t) => !t)}>
        {table ? 'Show as chart' : 'Show as table'}
      </button>
    </div>
  );
}
