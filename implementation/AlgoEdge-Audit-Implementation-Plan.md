# AlgoEdge Platform — Audit & Implementation Plan

**Purpose**: This document is a prioritized engineering brief for auditing and extending the AlgoEdge trading platform. It was written from a verbal walkthrough of the current system, not from direct repo access — every diagnosis below is a *hypothesis to verify*, not a confirmed root cause. The first step for whoever implements this (human or agent) is always: read the actual code before changing it.

**Known repo context** (from prior sessions): FastAPI backend, `aiosqlite`/SQLite persistence, Windows Server + Python virtualenv, backtest logic split across `backtest.py`, `engine.py`, `portfolio_engine.py`, `trade_grouper.py`, with `Backtester.jsx` as the frontend. `backtest_trades` table has a `strategy_id` column. Strategies live in a dedicated strategy folder and are called from risk/portfolio/position-sizing, the live engine, and the backtester.

**Rules for whoever executes this plan**:
1. Read every file you're about to touch, in full, before editing it. Don't patch based on this document's guesses alone.
2. Fix P0 items before touching P1/P2. A backtest with wrong PnL math invalidates every strategy decision built on top of it.
3. Every bug fix below should ship with a regression test (a "golden test case") so it can't silently regress.
4. Do not change strategy *signal logic* while fixing infrastructure bugs — keep these changes orthogonal so a strategy's edge isn't accidentally altered while chasing a plumbing bug.

---

## 1. Priority Ordering

| Priority | Track | Why |
|---|---|---|
| P0 | Correctness bugs (§2) | Wrong PnL math invalidates all backtest-driven decisions |
| P0 | Backtest/live fidelity (§3) | You can't trust prop-firm pass rates without this |
| P1 | Risk & portfolio framework (§4) | Needed before scaling to more strategies/accounts |
| P1 | Strategy authoring system (§5) | Removes hardcoding bottleneck |
| P2 | Multi-prop-firm infra (§6) | Operational scaling, not correctness-critical |
| Parked | Fundamental analysis (§7) | Needs a legality boundary drawn first — see below |

---

## 2. P0 — Correctness Bugs

### 2.1 TP1/TP2/TP3 Position-Sizing Overshoot

**Symptom**: With a $10,000 account risking 1% ($100) per trade, split 20%/40%/40% across TP1 (1:1R), TP2 (1:3R), TP3 (1:5R), backtest PnL wildly overshoots expected values on TP2/TP3 (e.g., $528 reported where $120 or $200 was expected).

**Expected math (use this as the golden test case)**:

```
Account: $10,000 | Risk: 1% = $100 total
TP1: 20% of risk = $20 risked  × 1R  = $20 expected profit
TP2: 40% of risk = $40 risked  × 3R  = $120 expected profit
TP3: 40% of risk = $40 risked  × 5R  = $200 expected profit
Max total profit if all 3 legs hit: $340
```

Add this exact scenario as a unit test against both the live and backtest position-sizing modules. If it fails in isolation (no market data needed), you've reproduced the bug cheaply.

**Leading hypotheses to check, in order**:
1. **User's own diagnosis is probably right**: backtest position sizing is falling back to a "dollar risk" shortcut instead of the real lot-size/pip-value calculation path that live trading uses. Confirm whether backtest and live share the *same* position-sizing module, or whether there are two parallel implementations that have drifted. If two — that's the root architectural problem, independent of this specific bug.
2. Confirm each TP leg's PnL is computed from *that leg's own* risk allocation ($20/$40/$40), not from the full $100 order risk.
3. Confirm pip/point value is computed using the leg's actual (possibly fractional/reduced) lot size after partial close, not the original full-order lot size.
4. Confirm the R-multiple isn't being applied twice (e.g., once during lot sizing, again during PnL calc).
5. Check broker lot-step rounding (e.g., 0.01 minimum lot) — on very small split sizes, rounding up can disproportionately inflate PnL.

**Action**: unify position sizing into one shared module (`position_sizing.py` or similar) called identically by `engine.py` (live) and `backtest.py`/`portfolio_engine.py` (backtest). Two implementations of the same math is how this class of bug happens and keeps happening.

### 2.2 Trailing Stop-Loss Modify Failures — CONFIRMED LIVE, not just backtest

**Status**: diagnosed from production Telegram logs (funded challenge trial account, Aug 17 2026). This is happening on a **live funded account right now**, which raises its priority above a pure backtest-fidelity issue — a position's protective stop can silently fail to trail while real capital is at risk.

**Observed pattern**:
```
1:55–1:56 AM — position_manager:_modify_sl:713 → "Failed to modify SL for 5747049601 to 113.767"
1:56 AM       — mt5.order_manager:modify_sl:203 → "Modify SL failed: Invalid stops"
1:56 AM       — position_manager:_modify_sl:713 → "Failed to modify SL for 5747049600 to 113.767"
1:56 AM       — position_manager:_modify_sl:713 → "Failed to modify SL for 5747049601 to 113.767"
...
2:07 AM       — position_manager:_modify_sl:713 → "Failed to modify SL for 5747049600 to 113.759"
2:07 AM       — mt5.order_manager:modify_sl:203 → "Modify SL failed: Invalid stops"
```

**Diagnosis**: "Invalid stops" is MT5 retcode 10016 (`TRADE_RETCODE_INVALID_STOPS`). Two tickets (5747049600/601) fail identically, repeatedly, against the *same* target price (113.767) before the trail logic moves on to a new target (113.759) roughly 12 minutes later — that pattern (same value, same rejection, multiple retries) is the classic signature of a trailing SL trying to move **inside the broker's minimum stop distance from current price** (`SYMBOL_TRADE_STOPS_LEVEL` and/or `SYMBOL_TRADE_FREEZE_LEVEL`, both broker/symbol-specific, expressed in points), not a random or intermittent failure. Lower-probability alternatives to rule out: price normalization/digit rounding mismatch, or a direction-sign bug placing the new SL on the wrong side of price (see the related APA_v1 bug in §2.4 — same category of "wrong side of price" defect, different module).

**Fix**:
1. Before sending any `TRADE_ACTION_SLTP` modify request, query `SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL)` and `SYMBOL_TRADE_FREEZE_LEVEL` for that symbol, convert to a price distance via the symbol's `Point`, and either clamp the candidate SL to respect that minimum distance from current bid/ask, or skip the modify entirely for that cycle if the trail hasn't moved far enough yet to clear the buffer.
2. Normalize the candidate SL to the symbol's `Digits` before sending — confirm 113.767/113.759 are valid tick increments for this specific symbol.
3. Stop retrying an identical failing value every cycle — log it once, back off, and raise an alert if a position's SL hasn't successfully updated in N consecutive attempts. Right now this fails silently-ish (log spam, no escalation) while the position sits with a stale, un-trailed stop — that's a live risk-exposure problem, not just a log-noise problem.
4. **Feed this back into §3.1/§3.2**: the backtester almost certainly doesn't simulate broker-side stops/freeze-level rejection at all, meaning a strategy can look fully protected in backtest while its live trailing stop chronically fails to move — a gap in backtest/live fidelity that's independent of spread/slippage. Worth adding a "simulated broker stops-level rejection" mode to the backtester so reported drawdown isn't overstating the protection a trailing stop actually provides live.

**Still needed to finalize the exact fix**: confirm the symbol these two tickets are trading, its `Digits`/`Point`, and the broker's stops/freeze level for that symbol.

### 2.3 News Filter — Wrong Endpoint

Confirm the current calendar/news API integration and its documented endpoint against what's actually being called. Add an integration test using a handful of known past high-impact events (a specific past CPI/NFP/FOMC timestamp) and assert the filter correctly blocks trading in the configured window around each. Extend this same test harness to backtesting (see §3.3) so the news blackout is enforced identically in both modes.

### 2.4 APA_v1 (HS_INVERSION) SELL Signals — Inverted/Duplicate SL & TP

**Status**: confirmed live, caught and correctly blocked by the pre-trade validation gate — this is a strategy logic bug, not an execution bug, and the safety net worked as designed.

**Observed** (production log, BTCUSD):
```
Signal REJECTED
Symbol: BTCUSD | Strategy: APA_v1 (M5) | Type: HS_INVERSION | Direction: SELL
Entry: 63106.453
SL:    63102.79211071429
TP:    63102.79211071429
Score: 90/100
Reason: SELL Stop Loss must be above entry
```

**Diagnosis — two separate defects in one signal**:
1. SL and TP are the *literal same value* to 11 decimal places. Not "close" — identical. That's the same expression being assigned to both fields somewhere in the SELL branch of the APA_v1 HS_INVERSION signal generator.
2. For a SELL, SL must sit above entry (price rising = loss for a short) and TP below (price falling = profit). Here both computed values sit *below* entry — backwards for this direction, correct-looking for a BUY. Strongly suggests the SELL branch is either missing its sign flip relative to the BUY branch, or was copy-pasted from it without mirroring the offset direction.

**Good news**: the validation gate rejected this before it reached the broker — confirms that safety layer is functioning for at least this error class. Worth checking it's applied uniformly across every strategy/direction combination, not just this path.

**Fix**:
1. Audit the SELL-direction SL/TP calculation specifically inside APA_v1's HS_INVERSION handler — look for a shared variable or expression being assigned to both `stop_loss` and `take_profit`, and confirm the offset sign is properly inverted between BUY and SELL branches.
2. Add the BUY/SELL SL-TP sanity check (SL/TP must be on the correct, opposite sides of entry for the given direction, and must not be equal) as a permanent unit test in the strategy-authoring validation pipeline from §5, so this class of bug is caught at development time for every strategy, not just this one at runtime.
3. Worth quantifying: pull how many APA_v1 SELL signals have been rejected for this reason in production. A Score-90/100 pattern being silently dropped every time is lost opportunity if the underlying pattern read is sound and only the stop math is broken — fixing this could recover real signal flow, not just clean up logs.

---

## 3. P0 — Backtest/Live Fidelity

This is the highest-value section. Everything else is easier to trust once this is solid, and prop-firm pass rates depend directly on it.

### 3.1 Spread & Slippage Modeling

Currently spread/slippage aren't modeled, which is why backtest results won't reproduce live — especially on prop-firm feeds where spreads can widen sharply around news/session opens.

**Recommended approach** (same technique the Concretum SPY paper used for its own slippage estimate — run real small-size test trades and measure actual execution vs. quoted price):
1. Log **actual** live spread and slippage per trade, per symbol, per time-of-day, continuously in production.
2. Build an empirical spread/slippage distribution from that log (bootstrap-resample from it, don't assume a Gaussian).
3. Apply that empirical distribution to backtest fills instead of a flat assumption — widen effective entry/exit price by a sampled spread + slippage draw, conditioned on symbol and session (Asian/London/NY, since liquidity — and therefore spread — varies structurally across sessions).
4. Re-run existing strategies through this cost model and compare degraded vs. non-degraded Sharpe/drawdown before trusting any prop-firm pass-rate estimate.

### 3.2 Same-Candle Entry/SL/TP Ambiguity

This is the mechanism behind "backtest says win, live says loss" on indices like NAS100/SPX500. On any bar where entry, SL, and TP could all occur within the same candle, OHLC data alone can't tell you the true intrabar path — the backtester is currently guessing (probably optimistically).

**Fix, in order of rigor**:
1. **Minimum viable fix (do this first)**: when a bar's high and low both breach SL and TP levels, adopt a conservative worst-case rule — assume SL is hit first. This alone will remove the optimistic bias, even before deeper data is available.
2. **Target architecture**: for any bar flagged as ambiguous, replay it using the lowest-timeframe data you have access to (M1, or tick data if MT5/your data vendor exposes it) and walk the actual chronological path to determine which level was truly touched first.
3. If full-history M1/tick data isn't available for older backtests, fall back to rule (1) and **tag** those trades in a separate "ambiguous-bar" report so you know how much of your historical win rate is resting on an assumption rather than a measurement.

### 3.3 Signals Live But Not in Backtest (and vice versa)

Likely causes, in rough order of probability:
- **Bar-closure semantics mismatch**: live evaluates conditions on the currently-forming (unclosed) candle in some code path, while backtest only ever evaluates on fully closed candles (or vice versa). This is the single most common cause of this exact symptom and should be the first thing audited.
- **Data feed divergence**: live pulls from MT5's real-time feed, backtest pulls from a separate historical vendor — different brokers can have meaningfully different OHLC prints, and timestamp/timezone/DST alignment differences shift candle boundaries.
- **Indicator warm-up gaps**: missing bars around weekends/holidays in historical data changing rolling-window indicator values (EMA, VWAP, ATR) versus their live equivalents.

**Best debugging technique — build this once, reuse forever**: run the exact live signal-generation code path against historical data replayed *as if live* (bar-by-bar, not the batch backtester), and diff its signal output against the batch backtester's output for the same window. Any divergence points precisely at the discrepancy. This "shadow replay" harness is worth building as permanent infrastructure — it will catch this entire class of bug going forward, not just this instance.

### 3.4 Drawdown Type Configuration

Implement all of these as explicit, independently selectable types (prop firms differ on which they enforce, and some enforce more than one simultaneously):

| Type | Definition |
|---|---|
| **Static (absolute)** | Measured from initial starting balance — e.g., a hard floor at balance − 10%, never moves regardless of gains |
| **Trailing (relative)** | Measured from the highest equity peak ever reached — ratchets up as the account grows, the classic FTMO-style rule |
| **Daily** | Resets each day at a configured cutoff (broker midnight or 5pm EST for FX) — peak-to-trough *within that single day only* |
| **Max drawdown (peak-to-trough)** | Reporting metric: largest cumulative decline from any historical equity peak to the subsequent trough — this is likely the "talk-to-talk" term from the transcript |
| **Consecutive-loss drawdown** | Largest cumulative loss across an unbroken losing streak, with no intervening new high — distinct from max DD if you want to track streak risk specifically |

Critical distinction to get right: **balance-based vs. equity-based**. Prop firms differ on whether drawdown counts only realized P&L (balance) or includes floating/unrealized P&L (equity). Store both series separately so any rule can be evaluated against the correct one — this is a common, expensive source of accidental rule violations.

---

## 4. P1 — Risk & Portfolio Management Framework

**Per-strategy config** (sketch):
```yaml
strategy: HTF_FVG_Flip
max_trades_per_day: 3
risk_per_trade_pct: 1.0
max_concurrent_positions: 1
allowed_sessions: [london, newyork]
allowed_symbols: [NQ, MNQ]
```

**Portfolio-level (universal) gates layered on top**:
- Max concurrent open trades across *all* strategies combined
- Max daily/trailing/static drawdown (using the types from §3.4)
- **Correlation exposure cap** — worth adding explicitly: if HTF FVG Flip, Bias/Key-Level/IFVG, and NY Open Break-Retest can all fire on the same NQ move simultaneously, you can end up with 3x the intended risk on one directional bet without any single strategy's config catching it. A portfolio-level correlation/exposure check across simultaneously-open positions closes this gap.

**Architecture recommendation**: route every order — live or backtest — through a single risk-engine gatekeeper module. Never let strategy code submit orders directly. Centralizing this is what prevents bugs like §2.1 from being duplicated across multiple code paths in the first place.

**Sizing-policy sandbox** (for choosing between fixed-fractional, Kelly-fraction, or volatility-targeted sizing — the same technique the Concretum SPY paper uses in its dynamic-sizing section): given a real historical trade-level R-multiple distribution from your *actual* bar-by-bar backtest, Monte Carlo-simulate many random reorderings of that same trade sequence under different sizing rules, and compare resulting Sharpe/max-DD/CAGR distributions. Important guardrail: this sandbox is for choosing a sizing *policy* — it is not itself evidence a strategy works. The evidence has to come from the real backtest on real price data first (this is exactly where the gold-strategy paper you read earlier went wrong — it treated an assumed-probability simulation as if it were a real backtest).

---

## 5. P1 — Strategy Authoring System

The core tension in the transcript: wanting to avoid hardcoding every strategy, while being (correctly) wary of putting a non-deterministic LLM into the live decision loop.

**Recommended split — don't put an LLM in the execution path at all**:

- **Authoring time (offline, LLM-assisted is fine here)**: write an MD spec describing entry/exit logic, risk parameters, applicable sessions and symbols — exactly like the three specs already written for HTF FVG Flip, Bias/Key-Level/IFVG, and NY Open Break-Retest. A coding agent (Claude Code) translates the spec into a deterministic Python module conforming to a standard `Strategy` interface, the same interface every other strategy in the folder already implements.
- **Validation gate before the module is allowed into the strategy folder**: run it against a battery of synthetic OHLC fixtures with known expected signals (unit tests), plus the "shadow replay" harness from §3.3 to confirm live/backtest parity from day one.
- **Execution time (live and backtest)**: pure deterministic code, zero LLM calls per candle. This directly removes the hallucination/non-determinism risk — the LLM's involvement ends before the strategy ever touches capital.
- **Graphical preview tool** (addresses the "see it on a chart before committing" want): once a new strategy module is generated, run it against a chosen historical window and plot entries/exits/key levels as chart overlays (a Plotly/Lightweight-Charts widget in the existing frontend) so you can visually sanity-check "does this fire where I'd expect" before running a full backtest. This is a scoped, achievable near-term build — it doesn't require solving general LLM-driven trading, just a rendering layer on top of a strategy that's already been code-generated and validated.

This keeps the existing architecture (strategy folder → called by risk/portfolio/engine/backtester) intact — it just adds a code-generation-and-validation layer in front of it.

---

## 6. P2 — Multi-Prop-Firm Infrastructure

**Pattern**: one control-plane frontend + parent backend, talking to N lightweight "trading node" backends — one per prop-firm account/broker connection.

- Prefer **ECS Fargate tasks** over N full EC2 VPS instances for the trading nodes — cheaper, elastically scalable, and "spin up a new node" becomes launching a task from a template rather than provisioning a whole server. Elastic Beanstalk can work too if you want its operational simplicity, but it's built more for single web-app-per-environment deployment than many small always-on isolated processes — worth weighing the trade-off before committing.
- **Static outbound IP**: neither EB nor Fargate gives a stable public IP by default. Standard fix: put trading nodes in a private subnet routed through a NAT Gateway with an Elastic IP — all nodes behind that NAT share one stable outbound IP. If prop firms specifically require *distinct* IPs per account (some flag shared IPs across accounts as a rule violation), you'd need one NAT Gateway + Elastic IP per IP-isolation group, which scales cost with number of distinct IPs needed rather than number of nodes — still cheaper than N full VPS instances.
- **Central event bus**: Redis pub/sub (or a lightweight MQTT/WebSocket hub) so the dashboard subscribes to live events — fills, errors, drawdown alerts — from every node without polling.
- **Node registry** (simple table in existing SQLite/Postgres):
```json
{
  "node_id": "propfirm-alpha-01",
  "url": "https://node-alpha.internal:8000",
  "prop_firm": "FTMO",
  "broker": "MT5-Alpha",
  "status": "healthy",
  "last_heartbeat": "2026-08-17T14:32:00Z"
}
```
Dashboard health-checks against this registry and lights up per-node status in one UI, matching the "one front end managing all prop firms" goal directly.

---

## 7. Parked — Fundamental Analysis / "Edge" Discussion

Two different ideas got bundled together in the transcript, and it's worth separating them clearly:

**Legitimate and worth building** — reading institutional/dealer positioning through *public* options data:
- Dealer gamma exposure (GEX) derived from open interest and volume at each strike — publicly derivable from CBOE data, or purchasable from providers like SpotGamma/SqueezeMetrics. This is exactly the mechanism the Concretum SPY paper documented in its gamma-imbalance section, where 5-day RSI was used as a cheap proxy for dealer positioning and showed real, statistically significant predictive power (β=-3.25, p=0.001).
- Trading known scheduled releases (CPI/NFP/FOMC) via implied-volatility term structure and skew changes around the event — fully legal, doesn't require knowing the number in advance, just modeling how vol gets priced and crushed around a known calendar date.
- Put/call ratios and VIX term structure as regime inputs — public data, legal, and would slot naturally into the regime-classification concept from the VWAP paper you read.

**Not something I'll help build**: anything premised on obtaining CPI/economic data, or knowledge of institutional order flow, before it's public. That's insider trading / misappropriation of embargoed data regardless of what a prop firm's own rulebook says about it, and it's a different category of risk than a coding bug — it's a legal one. If the underlying want is "see what smart money is doing before the crowd," the gamma/options-positioning route above gets you there legally and is genuinely well-published, testable territory.

---

## 8. Inputs Still Needed Before Execution Starts

- ~~The actual trailing-SL error trace/screenshot (§2.2)~~ — **received and diagnosed**. Still need: exact symbol/Digits/Point/stops-level for tickets 5747049600/601 to finalize the fix.
- Confirmation of which prop firm(s)/brokers are in scope, and whether they expose a spread/tick-data API (needed for §3.1 and §3.2).
- Confirmation of whether MT5 access here includes tick data or only down to M1 — determines how far §3.2's intrabar simulation can go.
- Read access to the actual repo for whoever executes this — everything above is a hypothesis set, not a diagnosis.
