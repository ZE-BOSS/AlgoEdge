import { streakProfile, streaksBy, drawdownSpan, exitMs } from '../src/utils/summaryEngine.js';
const t = (d, p, s='A', sym='X') => ({ exit_time: `2026-01-${String(d).padStart(2,'0')}T10:00:00Z`, pnl: p, pnl_r: p / 10, strategy_id: s, symbol: sym });
const ts = [t(1,5),t(2,3),t(3,-1),t(4,-2),t(5,-1),t(6,0),t(7,-4),t(8,2),t(9,1,'B'),t(10,1,'B')];
const p = streakProfile([...ts].reverse());
const fails = [];
const ck = (c, m) => { if (!c) fails.push(m); };
ck(p.maxWin === 3 && p.maxLoss === 3, 'lengths');
ck(p.current.type === 'W' && p.current.length === 3, 'current');
ck(p.runs.length === 4 && p.dist.L[3] === 1 && p.dist.L[1] === 1, 'runs/dist');
ck(Math.abs(p.worstLoss.r + 0.4) < 1e-9 && p.worstLoss.items.length === 3, 'worstLoss R');
const g = streaksBy(ts);
ck(g.length === 2 && g.find(x => x.key.startsWith('B')).maxWin === 2, 'streaksBy');
// balances: start 100, balance_after chained -> % of account of the streak
let b = 100; const tb = ts.map(x => ({ ...x, balance_after: (b += x.pnl) }));
const pb = streakProfile(tb);
ck(Math.abs(pb.worstLoss.startBal - 108) < 1e-9 && Math.abs(pb.worstLoss.pct - (-4 / 108 * 100)) < 1e-9, 'streak pct ' + JSON.stringify([pb.worstLoss.startBal, pb.worstLoss.pct]));
// drawdown: peak 108 -> trough 100 (after -4), wins in between none, losses 4
const dd = drawdownSpan(tb);
ck(Math.abs(dd.pct - (8 / 108 * 100)) < 1e-9 && dd.losses === 4 && dd.items.length === 5, 'drawdown ' + JSON.stringify([dd.pct, dd.losses, dd.items.length]));
ck(exitMs({ exit_time: 1767261600 }) === 1767261600000 && exitMs({ exit_time: 1767261600000 }) === 1767261600000, 'epoch');
console.log(fails.length ? 'streaks FAIL ' + fails.join('; ') : 'streaks OK');
