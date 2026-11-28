import { mergeSlots, slotsPayload, slotsFile, readSlotsFile } from '../src/utils/slotTransfer.js';
const assert = (c, m) => { if (!c) { console.error('FAIL', m); process.exit(1); } };
const live = [
  { slot_id: 'a1', symbol: 'XAUUSD', strategy_id: 'VWAP_v1', enabled: true, risk: { risk_per_trade_pct: 1 } },
  { slot_id: 'b2', symbol: 'EURUSD', strategy_id: 'ORB_v1', enabled: true },
];
const demo = [
  { slot_id: 'x', symbol: 'xauusd', strategy_id: 'VWAP_v1', enabled: false, risk: { risk_per_trade_pct: 0.5, be_mode: 'RR' }, strategy_params: { k: 2 } },
  { slot_id: 'y', symbol: 'US30', strategy_id: 'AV_DowBigTradePin_v1', enabled: true, risk: { risk_per_trade_pct: 0.75 } },
];
const { next, updated, added } = mergeSlots(live, demo);
assert(updated === 1 && added === 1, 'counts');
assert(next[0].slot_id === 'a1' && next[0].enabled === true, 'existing keeps id + switch');
assert(next[0].risk.risk_per_trade_pct === 0.5 && next[0].strategy_params.k === 2, 'existing takes settings');
assert(next[1].symbol === 'EURUSD' && !next[1].risk, 'untouched slot');
assert(next[2].symbol === 'US30' && next[2].enabled === false && next[2].slot_id !== 'y', 'new is OFF with a new id');
const p = slotsPayload(next);
assert(JSON.stringify(p.symbols) === JSON.stringify(['XAUUSD', 'EURUSD']), 'symbols only enabled');
const back = readSlotsFile(JSON.parse(JSON.stringify(slotsFile(demo, 'Demo'))));
assert(back.length === 2 && back[0].risk.be_mode === 'RR' && !('slot_id' in back[0]), 'file round trip');
let threw = false; try { readSlotsFile({ kind: 'x' }); } catch { threw = true; }
assert(threw, 'rejects other files');
console.log('slotTransfer OK');
