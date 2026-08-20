# AlgoEdge — Implementation Plan (Phase 4–7)

## Background

Phases 1–3 are complete. This plan covers:
- **Phase 4** — §3.2 gap: per-strategy risk fields not connected in live trading or portfolio backtest UI
- **Phase 5** — Cache efficiency: signal cache persists too long, blocking valid trades
- **Phase 6** — Remaining open bugs from previous sessions
- **Phase 7** — Full per-symbol-per-strategy autonomy (new feature from phase7.txt)

---

## Phase 4: Wire Per-Strategy Risk Fields End-to-End

> [!IMPORTANT]
> **Current gap**: `VWAPParams` has `max_trades_per_day`, `max_losses_per_day`, `drawdown_kill_pct`. `APAParams`, `NYOpenRetestParams`, `HTFFVGFlipParams`, `BiasIFVGParams`, `CRTParams` do **not**. The `CircuitBreaker` reads `max_daily_trades` from a flat dict — it does not read per-strategy params. Live trading ignores per-strategy guardrails. Portfolio backtesting has no per-strategy limits exposed in the UI.

### 4.1 Add missing fields to all strategy `Params` dataclasses

All strategy params files need the same guardrail fields VWAP already has:

| Field | Default | Meaning |
|---|---|---|
| `max_trades_per_day` | strategy-specific | Max signals per day for this strategy |
| `max_losses_per_day` | 2 | Kill strategy for the session after N losses |
| `drawdown_kill_pct` | 10.0 | Deactivate if realized drawdown > N% of capital |
| `max_open_positions` | 1 | Max concurrent open positions for this (symbol, strategy) slot |

**Files to modify:**
- [APAParams](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/strategies/strategy_apa/params.py)
- [NYOpenRetestParams](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/strategies/strategy_six_ny_open_retest/params.py)
- [HTFFVGFlipParams](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/strategies/strategy_four_htf_fvg_flip/params.py)
- [BiasIFVGParams](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/strategies/strategy_five_bias_ifvg/params.py)
- [CRTParams](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/core/config_schema.py) (inline in config_schema)
- [DriftJumpAlphaParams](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/core/config_schema.py)

### 4.2 Wire per-strategy limits into `CircuitBreaker` for live trading

[circuit_breaker.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/risk/circuit_breaker.py) currently reads only flat `config` dict. Need to:
- Accept optional `per_strategy_params: dict[str, dict]` keyed by `strategy_id`
- `check_can_trade(symbol, strategy_id)` reads strategy-specific `max_trades_per_day`, `max_losses_per_day` before falling back to global

### 4.3 Wire per-strategy limits into backtester engine

[backtester/engine.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/backtester/engine.py) — per-strategy `max_trades_per_day` and `max_losses_per_day` must be enforced per symbol-strategy slot, not just as a global cap.

### 4.4 Expose per-strategy risk in Backtester UI (single + portfolio)

[Backtester.jsx](file:///c:/Users/ikchr/Documents/AlgoEdge/frontend/src/pages/Backtester.jsx) — strategy params panel already exists for single backtest. For **portfolio** backtest, each strategy row needs:
- `Max Trades / Day`
- `Max Losses / Day`
- `Max Open Positions` (per symbol-strategy slot)

### 4.5 Strategy settings page: surface per-strategy risk in live config

[Strategy.jsx](file:///c:/Users/ikchr/Documents/AlgoEdge/frontend/src/pages/Settings/Strategy.jsx) — the per-symbol strategy assignment UI needs to expose `max_trades_per_day`, `max_losses_per_day`, `max_open_positions` next to each instrument row.

---

## Phase 5: Fix Signal Cache / Circuit Breaker Staleness

> [!WARNING]
> **Current bug**: `_last_signal_time` and `open_positions_by_symbol` in CircuitBreaker can persist stale counts across sessions, blocking trades that should be valid.

### 5.1 CircuitBreaker daily reset audit

[circuit_breaker.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/risk/circuit_breaker.py) — Verify `reset_daily()` is called on every new UTC day in live trading loop. Currently `last_reset_day` is set at init but there is no guarantee it resets mid-session at midnight.

### 5.2 `_last_signal_time` TTL

[bot_service.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/services/bot_service.py) — `_last_signal_time` stores a fingerprint to prevent duplicate signals, but the TTL logic:
- No expiry: if a signal fires, its fingerprint blocks that exact pattern **forever** in the session
- Fix: expire entries older than `scan_interval × 3` (e.g. 3 minutes for a 60s scan)

### 5.3 `open_positions_by_symbol` reconciliation

Currently `open_positions_by_symbol` is incremented on signal execution but only decremented when bot_service detects a close event. If the bot restarts mid-session, the counter starts at 0 even though positions are open → allows over-trading. Fix: seed from `mt5.positions_get()` on bot start.

---

## Phase 6: Remaining Open Bugs

### 6.1 BTCUSD multiple SL hits at same price (image 3)

Three positions all SL'd at `64424.909` simultaneously with identical PnL — indicates the bot placed 3 positions for the same signal but their SLs were never grouped or cross-checked. The `max_positions_per_symbol` check exists in config but is not enforced in live trading's pre-execution check.

**Fix**: Add `max_positions_per_symbol` enforcement before order submission in `bot_service.py` execution loop, reading from `CircuitBreaker.open_positions_by_symbol[symbol]`.

### 6.2 "Backtest Cancelled" exception shows full traceback in Telegram

[backtest.py API route](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/api/routes/backtest.py#L519) — the `raise Exception("Backtest Cancelled")` propagates through the global error handler and fires a Telegram SYSTEM ERROR. Now rate-limited to 1hr (fixed §error_handler), but it should never show traceback. Fix: use a sentinel exception class `BacktestCancelledError` that the global handler recognizes and suppresses.

---

## Phase 7: Per-Symbol-Per-Strategy Full Autonomy

> [!IMPORTANT]
> This is the largest new feature. Key design decisions summarized below — please review before approving.

### Design: `InstrumentSlot` replaces `InstrumentSettings`

The current `InstrumentSettings` model is `symbol → one strategy`. Phase 7 requires `(symbol, strategy_id)` as a composite key — the **same symbol can appear multiple times** with different strategies.

**New data model:**

```python
@dataclass
class InstrumentSlot:
    """
    One trading slot: a specific strategy applied to a specific symbol.
    Multiple slots can share the same symbol (different strategies).
    All risk/guardrail parameters below are per-slot, overriding globals.
    """
    slot_id: str          # UUID, e.g. "usdchf-vwap-1"
    symbol: str           # e.g. "USDCHF"
    strategy_id: str      # e.g. "VWAP_v1", "APA_v1"
    enabled: bool = True
    label: str = ""       # User-visible name, e.g. "USDCHF Scalp"

    # ── Per-slot Risk ─────────────────────────────────────
    risk_per_trade_pct: float | None = None   # None = use global
    max_trades_per_day: int | None = None     # None = use strategy default
    max_losses_per_day: int | None = None     # None = use strategy default
    max_open_positions: int | None = None     # None = use global max_positions_per_symbol
    max_consecutive_losses: int | None = None # NEW: kill slot after N consecutive losses

    # ── Strategy-specific params override ──────────────────
    strategy_params_override: dict = field(default_factory=dict)
    # e.g. {"sl_points": 170, "sl_atr_multiplier": 0} for VWAP on USDCHF
```

### 7.1 Backend: `InstrumentSlot` model + config migration

**Files:**
- [config_schema.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/core/config_schema.py) — Add `InstrumentSlot` dataclass; `UserConfigV2.instrument_settings` → `instrument_slots: list[InstrumentSlot]`; keep `instrument_settings` for DB backward compat
- [bot_service.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/services/bot_service.py) — Replace `symbols` list with `slots: list[InstrumentSlot]`. Bot scans by slot, not by symbol. Same symbol with two strategies = two scan passes
- [circuit_breaker.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/risk/circuit_breaker.py) — State keyed by `slot_id` instead of `symbol` for per-slot counters; `open_positions_by_symbol` stays as global guard

**Validation rule** (mirrors hard-cap validation):
```
if sum(slot.max_open_positions for slot in enabled_slots) > global.max_concurrent_positions:
    → raise config validation error
```

### 7.2 Backtester: multi-slot portfolio support

[backtester/engine.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/backtester/engine.py):
- Portfolio backtester currently groups by `(symbol, strategy)` — this is already close to slot-based
- Need to allow identical `symbol` pairs with different strategies in the same run
- Per-slot `strategy_params_override` merged with strategy defaults before simulation

[Backtester.jsx](file:///c:/Users/ikchr/Documents/AlgoEdge/frontend/src/pages/Backtester.jsx):
- Portfolio row = one slot (symbol + strategy + per-slot risk overrides)
- Allow adding same symbol multiple times (currently de-duped by symbol)
- Each row expandable to show: risk %, max trades/day, max losses/day, max open positions, max consecutive losses, + strategy-specific params

### 7.3 Strategy settings: slot-based symbol picker

[Strategy.jsx](file:///c:/Users/ikchr/Documents/AlgoEdge/frontend/src/pages/Settings/Strategy.jsx):
- Replace current `instrument_settings` toggle grid with an **Add Slot** pattern
- Each slot card shows: Symbol | Strategy | Risk % | Max Trades | Max Losses | Max Consecutive Losses | Max Open Positions | [Expand params]
- Same symbol can be added multiple times — de-duplication removed
- Adding a slot with `max_open_positions > global.max_concurrent_positions` shows inline validation warning

### 7.4 Live bot: slot-aware execution

[bot_service.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/services/bot_service.py):
- `_scan_loop` iterates over `slots` instead of `symbols`
- Each slot gets its own strategy instance with merged params
- `_last_signal_time` keyed by `slot_id` (not symbol) — USDCHF/VWAP and USDCHF/APA have independent cooldowns
- `max_consecutive_losses` tracking per slot: if N consecutive losses → mark slot `paused_until = next_day` 

### 7.5 Risk: per-slot risk amount

[position_sizer.py](file:///c:/Users/ikchr/Documents/AlgoEdge/backend/risk/position_sizer.py):
- Read `slot.risk_per_trade_pct` if set, else fall back to `global.risk.risk_per_trade_pct`
- Hard cap still applies globally: no slot can exceed `max_risk_hard_cap_pct`

---

## Open Questions

> [!IMPORTANT]
> **Q1 — Slot ID persistence**: When a slot is deleted and re-added, should its loss counter reset? Current design: yes (new UUID = clean state). Is that correct?

> [!IMPORTANT]
> **Q2 — Strategy params override scope**: Should `strategy_params_override` in a slot override the strategy's global params (e.g. `vwap.sl_points`) for ALL slots using that strategy, or only for that specific slot? Current design: **per-slot only** (global strategy params untouched). Confirm?

> [!IMPORTANT]
> **Q3 — Bot restart state**: When the bot restarts, should per-slot loss counters (daily trades, consecutive losses) reset to zero or be persisted to DB? Current design: reset to zero on restart, but seed `open_positions` from MT5 live positions.

## Proposed Order of Execution

```
Phase 4 → Phase 5 → Phase 6 → Phase 7
```

Phases 4 and 5 are relatively self-contained. Phase 7 depends on Phase 4 (all strategies must have the fields before slots can override them).

## Verification Plan

| Phase | Test |
|---|---|
| 4.1–4.2 | Run live bot with VWAP USDCHF: confirm max_trades_per_day=2 halts after 2 trades |
| 4.3 | Portfolio backtest with NYOpenRetest (max_trades=1/day): confirm single trade per day |
| 5.2 | Force same signal twice in 60s: second should pass (fingerprint expired after 3× scan interval) |
| 5.3 | Restart bot mid-session with open positions: confirm open_positions_by_symbol = actual MT5 count |
| 6.1 | BTCUSD signal: confirm only 1 position opens when max_open_positions=1 |
| 7 | Portfolio backtest: USDCHF/VWAP + USDCHF/APA same date range — both produce independent signals |
