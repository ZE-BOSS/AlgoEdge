import * as S from './summaryEngine.js';
import fs from 'fs';
const d = JSON.parse(fs.readFileSync('../../../debug/apa/eurusd.json', 'utf8'));
const legs = d.trades.map(t => ({ ...t, _source_symbol: 'EURUSD', _source_strategy: 'APA_v1', _initial_balance: 25000 }));

console.log('=== B1 epoch-seconds parsed as ms ===');
console.log('  raw entry_time        :', legs[0].entry_time);
console.log('  new Date(entry_time)  :', new Date(legs[0].entry_time).toISOString());
console.log('  truth (entry_time_iso):', legs[0].entry_time_iso);

console.log('\n=== B2 bucketByPeriod ===');
console.log('  month keys:', [...S.bucketByPeriod(legs, 'month').keys()].slice(0, 5));

const st = S.computePeriodStats(legs, 25000);
console.log('\n=== B3 duration ===');
console.log('  avgDurationMin:', st.avgDurationMin.toFixed(5));

console.log('\n=== B4 R / winrate basis ===');
console.log('  expectancyR       :', st.expectancyR.toFixed(3));
console.log('  backend expectancy:', d.report.expectancy_r);
console.log('  winRate legs      :', (st.winRate * 100).toFixed(1) + '%');
console.log('  winRate groups    :', (d.report.win_rate * 100).toFixed(1) + '%');

console.log('\n=== B5 sentinels / annualisation ===');
console.log('  sortino:', st.sortino, '| calmar:', st.calmar.toFixed(2), '| PF:', st.profitFactor.toFixed(2));
console.log('  sharpe :', st.sharpe.toFixed(2), '(sqrt(252) on PER-TRADE returns)');
