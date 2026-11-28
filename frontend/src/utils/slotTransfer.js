/**
 * Moving slots (one symbol + one strategy, with its own parameters and risk)
 * between places: the Backtester's rows -> Settings, and one server's Settings
 * -> another server's (demo -> live).
 *
 * The same rules everywhere, because the danger is the same everywhere: a copy
 * must never start trading on its own.
 *   - A pairing the target already has takes the copied parameters and risk;
 *     its on/off switch and its id stay the target's.
 *   - A pairing the target does not have is ADDED SWITCHED OFF.
 *   - Target slots that are not in the copy are left alone.
 */

const COPIED = ['strategy_params', 'risk', 'use_measured_params', 'tp1_rr', 'tp_count',
  'risk_per_trade_pct', 'max_positions_per_symbol', 'timeframe'];

const sameSymbol = (a, b) => String(a || '').trim().toUpperCase() === String(b || '').trim().toUpperCase();

export function newSlotId() {
  return (globalThis.crypto?.randomUUID?.() || Math.random().toString(16).slice(2).padEnd(12, '0'))
    .replace(/-/g, '').slice(0, 12);
}

/** Only the settings a slot carries, never its switch or id. */
export function slotSettings(slot) {
  const out = {};
  COPIED.forEach((k) => {
    if (slot && slot[k] !== undefined) out[k] = slot[k];
  });
  return out;
}

/** Merge `incoming` slots into `target` slots by the rules above. */
export function mergeSlots(target = [], incoming = []) {
  const next = (target || []).map(s => ({ ...s }));
  let updated = 0;
  let added = 0;
  (incoming || []).forEach((src) => {
    if (!src || !src.symbol || !src.strategy_id) return;
    const k = next.findIndex(t => sameSymbol(t.symbol, src.symbol) && t.strategy_id === src.strategy_id);
    if (k >= 0) {
      next[k] = { ...next[k], ...slotSettings(src) };
      updated += 1;
    } else {
      next.push({ slot_id: newSlotId(), symbol: src.symbol, strategy_id: src.strategy_id, enabled: false, ...slotSettings(src) });
      added += 1;
    }
  });
  return { next, updated, added };
}

/** What /config must be sent with the slots so `symbols` (read by /bot/start)
 *  and the legacy `instrument_settings` projection stay in step with them. */
export function slotsPayload(slots) {
  const enabled = (slots || []).filter(s => s.enabled !== false);
  const symbols = [...new Set(enabled.map(s => s.symbol))];
  const legacy = symbols.map(sym => ({
    symbol: sym, strategy_id: enabled.find(s => s.symbol === sym).strategy_id, enabled: true,
  }));
  return { instrument_slots: slots, instrument_settings: legacy, symbols };
}

/** A file anyone can move between servers by hand. */
export function slotsFile(slots, fromLabel) {
  return {
    kind: 'algoedge-slots',
    version: 1,
    exported_from: fromLabel || '',
    exported_at: new Date().toISOString(),
    slots: (slots || []).map(s => ({ symbol: s.symbol, strategy_id: s.strategy_id, enabled: s.enabled !== false, ...slotSettings(s) })),
  };
}

export function readSlotsFile(obj) {
  if (!obj || obj.kind !== 'algoedge-slots' || !Array.isArray(obj.slots)) {
    throw new Error('Not an AlgoEdge slots file');
  }
  return obj.slots.filter(s => s && s.symbol && s.strategy_id);
}
