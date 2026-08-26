# CRT (Candle Range Theory) Strategy — Implementation Spec for AlgoEdge

Source: a discretionary NY-session CRT scalp strategy (demonstrated on MNQ futures), formalized here into implementable rules. Sections flagged **[OPEN]** mark places where the source was discretionary or informal and a default had to be chosen for this spec — review before implementing as-is.

> **UPDATED (2026-08, Phase 6 — [Doc-3]).** Three changes since this spec was written: (1) `bias_neutral_mode`
> (`BLOCK`/`REDUCED_SIZE`/`ALLOW`, default `REDUCED_SIZE`) replaces the hard BLOCK on a NEUTRAL HTF bias —
> measured on real logs this was the single largest rejection category (254/900 evaluations); a valid C2
> sweep with no confirmed trend now trades at reduced size instead of being discarded. (2)
> `trigger_grace_bars` (default 2) lets a live `c2_trigger` survive that many additional HTF closes before
> invalidating, instead of expiring on the very next one. (3) **Target-mode exemption**: this spec's own
> "Architectural discrepancy" note below (SL derived backward from a structural TP that the RiskParams
> TP-grid then silently overrides) is still open — CRT still declares `structural_tp`/`structural_tp_rr`/
> `tp_is_structural` in its signal metadata (unchanged from before), and VWAP v2 (Phase 8) now uses the
> identical pattern for its own σ-band targets. Neither strategy is actually exempted from the grid yet;
> that product decision (exempt CRT/VWAP from the grid, or place SL structurally and let the grid own
> targets) remains unmade — see `risk/multi_tp.py`'s TODO. See `implementation/TASKS.md` Phase 6 (6.8–6.10)
> for the full change record.

## Flow Summary

```
1. Scan HTF candles for a range candle (C1) followed by a sweep-and-reclaim candle (C2)
2. Check HTF trend/bias — discard the setup if C2's direction conflicts with it
3. Drop to LTF, wait for a break of C2's trigger level → enter
4. TP = C1's opposite extreme; SL derived from target R-multiple
5. Manage the trade to TP/SL
6. Mark the session done — no further setups until the next session
```

## 1. Definitions

| Term | Meaning |
|---|---|
| HTF | Higher timeframe used to identify the pattern — 1H, 30M, or 15M (configurable) |
| LTF | Lower timeframe used only for entry timing — 5M (configurable) |
| Candle 1 (C1) / Range Candle | Defines the range; use wick high/low, never the candle body |
| Candle 2 (C2) / Sweep Candle | The next HTF candle; must wick beyond one side of C1 and close back inside it |
| Trigger Level | C2's high (bullish setups) or low (bearish setups) — the LTF break that fires entry |

## 2. Configuration

```
htf_timeframe: enum[1H, 30M, 15M]      # default 1H — highest-quality per source; 15M = more frequent
ltf_timeframe: enum[1M, 5M, 15M]            # default 5M
session_start / session_cutoff: time   # instrument-local — see Section 8
target_r_multiple: float               # default 1.5, valid range 1.5-2.0
max_trades_per_session: int = 1
instruments: [...]                     # see Section 8 before populating
```

## 3. Setup Detection (evaluate on every HTF candle close)

1. If there's no pending setup, treat the just-closed candle as candidate C1.
2. When the next HTF candle closes, evaluate it as C2 against C1:
   - **Bullish:** `C2.low < C1.low` AND `C1.low < C2.close < C1.high`
   - **Bearish:** `C2.high > C1.high` AND `C1.low < C2.close < C1.high`
3. No match → C2 becomes the new candidate C1; keep scanning.
4. Match → valid setup. Record `direction, C1.high, C1.low, C2.high, C2.low` and proceed to Section 4.

**[OPEN]** if C2 wicks beyond *both* sides of C1 before closing (sweeps high and low intrabar), direction is ambiguous — the source doesn't address this. Recommend treating as invalid/skip by default.

## 4. Trend / Bias Filter

Only take a setup whose direction matches the prevailing HTF bias. The source determines this visually ("is price making higher highs, or has it started printing lower highs") — this needs a formal rule for AlgoEdge:

1. **Preferred:** reuse `market_structure.py`'s bias logic once its current bugs are resolved — HH/HL sequence = bullish, LH/LL = bearish, evaluated on the same HTF as the CRT scan.
2. **Fallback:** compare the last 3 HTF swing highs/lows directly using the same HH/HL vs. LH/LL logic.

**[OPEN]** the source never addresses a flat/ranging market. This spec defaults to "no clear bias → no trade" rather than guessing a side.

If a setup's direction conflicts with bias, discard it and resume scanning from Section 3, step 1 — don't hold it and wait for bias to flip.

## 5. Entry Trigger

On the LTF, once a bias-aligned setup exists:

- Bullish → enter long when price trades **above `C2.high`**
- Bearish → enter short when price trades **below `C2.low`**

**[OPEN]** how long to wait before invalidating an untriggered setup isn't specified in the source. Suggested default: invalidate if the trigger hasn't fired by the time the next HTF candle closes.

## 6. Trade Management

- **TP** = C1's opposite extreme (bullish → `C1.high`; bearish → `C1.low`)
- **SL** is derived backward from TP to hit `target_r_multiple` — it is *not* placed at a structural level like beyond the sweep wick:

```
tp_distance = abs(take_profit - entry_price)
sl_distance = tp_distance / target_r_multiple     # default 1.5
stop_loss   = entry_price - sl_distance   (long)
stop_loss   = entry_price + sl_distance   (short)
```

**Worked example** (bullish, illustrative numbers — not from the source): C1 high 1.2050 / low 1.2000. C2 wicks to 1.1985, closes at 1.2015 (valid — inside 1.2000-1.2050) with a high of 1.2020. LTF breaks 1.2020 → entry there.
`tp_distance` = 1.2050 - 1.2020 = 30 pips → `sl_distance` = 30 / 1.5 = 20 pips → SL = 1.2000.

Position sizing / account-risk-% should stay in AlgoEdge's existing risk layer — this module should emit `{entry, stop_loss, take_profit}`, not a lot size.

## 7. Session / Time Filter

- Start scanning at session open — the source uses **9:30 AM ET** (NY session), called out as producing the highest-quality setups.
- Stop opening *new* searches if nothing valid has appeared by **~12:00-1:00 PM ET**; a setup already in progress can still run to TP/SL.
- This window is instrument-specific — see Section 8.

## 8. Instrument Notes — Review Before Deploying Across the Book

The source is demonstrated on MNQ futures — real order flow, conventional session structure. AlgoEdge's three instrument classes interact with this differently:

- **Forex / Metals:** translates cleanly — real liquidity-sweep dynamics and a conventional NY session both apply as intended.
- **Deriv synthetics (Crash/Boom, Volatility indices):** worth flagging directly. CRT's entire premise is real institutional stop-hunting and liquidity delivery — arguably even more explicitly than general SMC concepts. That's the same concern already raised about running the SMC strategy on synthetics, since those instruments are algorithmically generated rather than order-flow driven. CRT is more exposed to that critique, not less, given how directly the source ties its logic to liquidity. Recommend backtesting CRT on synthetics in isolation rather than assuming the forex/metals edge transfers by default. They also trade continuously, so the NY-session filter has no natural anchor there and would need reworking or dropping for that instrument class.

## 9. Open Questions Summary

1. Trigger timeout after a setup forms but doesn't fire (Section 5)
2. Which HTF to use, and how to arbitrate if multiple HTFs produce aligned setups at once (Sections 2-3)
3. Formal trend-filter logic — the market-structure reuse is a judgment call made for this spec, not something the source specifies (Section 4)
4. The source's claimed 70-80% hit rate is an unverified, anecdotal number from a single content creator — treat as a hypothesis for AlgoEdge's backtester, not a validated input to risk sizing
5. Flat/ranging market handling (Section 4)
6. C2 sweeping both sides intrabar (Section 3)

## 10. Suggested Integration

Implement as its own strategy module using the same interface as AlgoEdge's existing SMC strategy, so the backtesting/execution pipeline treats it uniformly. Output per session should be `no_setup`, or a single `{direction, entry, stop_loss, take_profit, htf, trigger_level}` object — never a stream of concurrent CRT signals, to preserve the one-trade-per-session design intent.
