import { streakProfile, streaksBy } from '../src/utils/summaryEngine.js';
const t = (d, p, s='A', sym='X') => ({ exit_time: `2026-01-${String(d).padStart(2,'0')}T10:00:00Z`, pnl: p, strategy_id: s, symbol: sym });
const ts = [t(1,5),t(2,3),t(3,-1),t(4,-2),t(5,-1),t(6,0),t(7,-4),t(8,2),t(9,1,'B'),t(10,1,'B')];
const p = streakProfile([...ts].reverse());
const ok = p.maxWin === 3 && p.maxLoss === 3 && p.current.type === 'W' && p.current.length === 3 && p.runs.length === 4 && p.dist.L[3] === 1 && p.dist.L[1] === 1;
const g = streaksBy(ts);
console.log(ok && g.length === 2 && g.find(x => x.key.startsWith('B')).maxWin === 2 ? 'streaks OK' : 'streaks FAIL ' + JSON.stringify(p));
