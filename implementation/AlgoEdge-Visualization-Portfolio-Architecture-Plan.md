# AlgoEdge — Visualization, Fundamentals UI & Portfolio Architecture Plan

**Date:** August 19, 2026
**Status:** Draft v1 — extends `AlgoEdge-OrderFlow-Fundamental-Edge-Plan.md` and the backtest system audit
**Target standard:** Institutional/hedge-fund-desk grade — deterministic backtest-live parity, auditable trade history, no silent config drift between stages, production-grade error handling

## 1. Purpose & Scope

This covers five things:

1. A real-time, TradingView-replay-style visualization system for backtesting and optimization
2. A fundamentals UI suite — order flow, gamma exposure, binary contract expiry, and one open panel
3. Configuration continuity across Optimize → Backtest → Live
4. A two-tier configuration model — global vs. per-symbol-per-strategy
5. A real portfolio construction engine, replacing the current placeholder

I don't have the earlier planning files as actual documents in this session — file storage doesn't carry across conversations. Everything below is built from the AlgoEdge context already established plus what was described in this session. Where I'm extrapolating rather than restating something confirmed, it's marked **[OPEN]**, and all of those are collected in Section 9. If you upload the earlier plan docs, I'll reconcile this against them directly.

## 2. Design Principles

Apply across every section below.

- **Config portability.** A (symbol, strategy) configuration produced in Optimization moves into Backtest and then Live unmodified. A stage-specific override is an explicit fork with its own record — never a silent edit to the same object.
- **Independent legs.** The same symbol can appear more than once in a portfolio if attached to different strategies. Each (symbol, strategy) pair is its own configuration unit, its own risk envelope, its own equity contribution — never merged or aliased.
- **Visualization is read-only.** Markers, zones, and overlays are metadata the engine generates and attaches to trades/bars. The chart renders what happened; it never recomputes or infers it. Otherwise the visualization layer becomes a second source of truth that can drift from the actual trading logic — the same category of bug as the current trailing-stop and signal-inversion issues, one layer up.
- **No feature ships without a parity test.** Anything touching the optimize/backtest/live boundary gets a same-config-same-data parity check before it's considered done (Section 8).

## 3. Pipeline: Optimize → Backtest → Live

### 3.1 Optimization
- Single symbol only — no portfolio optimization.
- Inputs: one technical strategy plus a fundamentals overlay (order flow, gamma, news/event data). Fundamentals refine the technical strategy's parameters; they aren't a strategy on their own.
- Output: a versioned config object tied to (symbol, strategy), sent forward via a "Send to Backtest" action rather than re-entered.

### 3.2 Backtest
- Accepts the optimized config directly. Supports **both** single-symbol and portfolio backtesting.
- A portfolio backtest is a set of (symbol, strategy) legs, each carrying its own config — some legs may come from optimization runs, others configured manually. The portfolio itself isn't optimized as a unit; only its legs are.
- Fundamentals simulate identically to how they ran in optimization — same data, same evaluation logic — so a strategy's fundamentals-aware behavior doesn't change just because it moved stages.

### 3.3 Live
- Accepts the config from Backtest via "Promote to Live" — single-symbol or portfolio, matching whatever was backtested.
- The target: live behavior matches backtest behavior for an identical config. Same guarantee the trailing-stop and signal-inversion fixes are chasing, extended to fundamentals-aware strategies.

### 3.4 Config object — illustrative shape **[OPEN]**

```json
{
  "config_id": "uuid",
  "symbol": "BTCUSD",
  "strategy_id": "vwap_reversion_v2",
  "source_stage": "optimization",
  "created_at": "2026-08-19T00:00:00Z",
  "technical_params": { "...": "strategy-specific" },
  "fundamental_params": {
    "order_flow": { "enabled": true, "lookback": "...", "threshold": "..." },
    "gamma": { "enabled": true, "regime_filter": "..." },
    "news": { "enabled": true, "blackout_window_minutes": 15 }
  },
  "risk_params": { "...": "see Section 6" }
}
```
The idea is one object that travels unmodified between stages — final field list is for the coding agent to settle against the existing strategy spec schema.

## 4. Replay Visualization Engine

Applies identically to backtesting and optimization — one engine, not two.

### 4.1 What's new vs. what exists
The current loader (fetching/loading spinner) stays for the "waiting on data" state. This adds a layer on top: once a run starts processing, the chart animates through the historical window as it goes — TradingView's bar-replay tool, not a jump straight to a finished equity curve.

### 4.2 Data flow
Reuses the existing WebSocket channel. Suggested event types, one per processed step:

- `bar_update` — OHLCV for the newly processed bar
- `trade_opened` — entry price, SL, TP, confluence tags, zone geometry
- `trade_modified` — SL/TP change; makes trailing-stop behavior visible frame-by-frame, directly useful for the retcode 10016 issue already on file
- `trade_closed` — close price and close reason: `tp_hit`, `sl_hit`, `trailing_stop`, `manual`, `strategy_exit`
- `indicator_update` — values for whatever indicators the active strategy uses

### 4.3 Chart markers required per trade
- Entry marker
- SL line, including movement over time if trailing
- TP line(s)
- Close marker, visually distinct per close reason
- Confluence tags at the signal bar
- Zone geometry (FVG boxes, order blocks, liquidity zones) as shaded regions, not points
- The specific parameter values behind the signal (which Fib level, which ATR multiple) — on hover/click rather than always-on, so the chart doesn't get cluttered

### 4.4 Replay scrubbing
Store markers/zones as trade-linked metadata rather than deriving them at render time — most likely a new table in `algoedge.db` keyed by trade_id, storing marker/zone geometry as JSON. That's what lets a finished run be scrubbed back through exactly as it looked live, not just watched once. Scrub control: a timeline/seek bar under the chart, same interaction as TradingView's replay tool.

### 4.5 Indicator overlays
Indicators render based on the strategy's declared indicator list (RSI bands, VWAP, whatever else a given strategy uses) rather than being hardcoded per chart — a new strategy that adds an indicator later shouldn't require a frontend change.

### 4.6 Performance approach **[OPEN]**
Most likely place this lags if built naively:
- Canvas-based rendering rather than DOM/SVG markers — DOM nodes per marker lag well before canvas does. `lightweight-charts` (TradingView's own open-source embeddable library) is a reasonable starting point to evaluate rather than building a renderer from scratch.
- Render only the visible viewport; keep off-screen data in memory, out of the render loop.
- Batch WebSocket messages into animation-frame-aligned render ticks — a fast backtest can emit more events per second than the screen can usefully show.
- Downsample zone/marker density at low zoom levels.
- Move heavy computation (zone geometry, any indicator math not precomputed backend-side) off the main thread via a Web Worker.

## 5. Fundamentals & Order Flow UI Suite

Four panels. Two specified in detail, one from what was described in this session, one open.

### 5.1 Order Flow
Institutional order placement on the price chart — circles/shapes at price levels, sized or labeled by order count/volume, so activity density reads at a glance. Visual front end for the order-flow microstructure research already synthesized (decomposed OFI, broker-level autocorrelation); the panel visualizes that data, it isn't a new data source.

### 5.2 Gamma Exposure
Dealer gamma positioning and the delta-hedging flow it implies, per the dealer gamma-hedging research already done.
**[OPEN]** Chart type isn't settled. Gamma exposure is usually shown as a profile against strike (GEX-by-strike bar chart) — strike-indexed, not price/time-indexed like the order flow panel, so they don't share an x-axis cleanly. Worth deciding whether this is a price-chart overlay (with reprojection) or its own sub-chart before building it.

### 5.3 Binary Contract Expiry
Expired binary/synthetic-index contracts (Deriv) — most likely an expiry outcome distribution over time.
**[OPEN]** Exact metrics weren't specified here — win rate over time, payout distribution, and strike-vs-expiry-price scatter are the obvious candidates, worth confirming against the source plan.

### 5.4 Fourth panel **[OPEN]**
Referenced as already scoped in a prior session, but the specifics weren't in this dictation and I don't have that file here. Candidates worth considering until confirmed:
- Dark pool / large block print feed
- Open interest heatmap
- Net delta exposure (distinct from gamma)
- COT (Commitment of Traders) positioning — relevant given the NQ/MNQ futures exposure

## 6. Configuration Architecture

| Setting | Scope |
|---|---|
| Max daily drawdown | Global |
| Max weekly drawdown | Global |
| Risk hard cap | Global |
| TP level structure | Global |
| Volume split | Global |
| Risk % per trade | Per (symbol, strategy) |
| Max trade days | Per (symbol, strategy) — **[OPEN]** exact meaning, see 6.1 |
| Max number of losses | Per (symbol, strategy) — independent circuit breaker |

### 6.1 Two points on the table worth locking down
- **Max trade days** — does this cap how long a single position can stay open, or cap the number of active trading days allotted to that leg over a period? Different implementations.
- **Risk hard cap mechanics** — does it cap each individual leg's risk % (no leg exceeds X), or the sum of all active legs' risk exposure (total portfolio risk doesn't exceed X)? Both are legitimate, but they're different mechanisms.

### 6.2 Max losses vs. max drawdown
These run in parallel, not nested. A (symbol, strategy) leg can hit its own loss-count limit and get benched even while the account is nowhere near its drawdown ceiling — and regardless of the loss-count rule, no leg's activity can ever push the account past the global max drawdown. Both constraints stay active; whichever triggers first is what stops that leg (or the account).

### 6.3 Portfolio composition
A portfolio is a list of (symbol, strategy) legs, not a list of symbols. The same symbol can appear multiple times if paired with a different strategy each time — e.g. BTCUSD with one strategy and BTCUSD with a different strategy, as two independent legs, each with its own risk config. Identical rule for portfolio backtesting and portfolio live trading.

**[OPEN]** TP levels and volume split are specified as strictly global. Worth a second look before locking that in — a scalping leg and a swing leg in the same portfolio may reasonably want different TP structures. Flagging rather than overriding what was specified.

## 7. Portfolio Management Engine

The current portfolio construction approach was flagged as not solid enough. I don't have visibility into its current implementation in this session, so what follows is a from-scratch proposal to review against, not a diagnosis of the existing one.

### 7.1 Correlation/offset-based construction
Use single-leg backtest equity curves to find (symbol, strategy) pairs whose drawdown periods don't overlap — legs that are negatively or weakly correlated, so one leg's losing streak tends to land near another leg's winning streak, smoothing the combined equity curve. Mechanically: build a correlation matrix across legs' daily or weekly returns from backtest data, then bias allocation toward low/negative-correlation combinations.

### 7.2 Other allocation methods worth building in **[OPEN]**
- **Risk parity / equal risk contribution** — size each leg so it contributes equally to total portfolio risk, rather than equal dollar allocation.
- **Volatility targeting** — scale leg size inversely to its recent volatility.
- **Walk-forward validation on the correlation weights** — correlations estimated on one backtest window aren't guaranteed to hold going forward. Worth building in from the start rather than retrofitting once the correlation-based version is already trusted.

### 7.3 UI
A dedicated UI rather than reusing the optimization screen, matching your stated preference — the units of analysis are genuinely different (single-leg parameters in optimization vs. cross-leg correlation and allocation here), so sharing a screen would be forcing two different jobs into one.

## 8. Testing & Production-Readiness

"Bug-free" isn't a claim any real system gets to make outright, but this is the testing discipline that gets you close to institutional-grade reliability here:

- **Unit tests on marker/zone geometry generation**, independent of the chart renderer, so a rendering bug and a geometry bug fail differently and get caught separately.
- **Replay-based regression tests** — record a known run's event stream once, replay it, assert the chart state matches. Catches silent breakage in the visualization layer specifically, separate from the trading logic itself.
- **Backtest-vs-live parity tests on the config object** — same config, same historical window, assert trade-for-trade match. Direct test for the divergence issue already on record, now extended to fundamentals-aware configs.
- **Load testing** the WebSocket/render pipeline against the fastest historical backtest run on file, not just typical ones — that's where lag shows up first.

## 9. Open Items

1. Config object field list / schema (3.4)
2. Rendering/performance approach — needs validation against real data volumes (4.6)
3. Identity of the 4th fundamentals panel (5.4)
4. Gamma panel chart type (5.2)
5. Binary expiry panel metrics (5.3)
6. "Max trade days" definition (6.1)
7. Risk hard cap mechanics — per-leg vs. aggregate (6.1)
8. Whether TP levels/volume split should ever be per-strategy overridable (6.3)
9. Which portfolio allocation methods beyond correlation-offset to build now vs. later (7.2)

## 10. Suggested Build Order

1. **Config object + pipeline plumbing** (Section 3) — everything else depends on this existing first.
2. **Replay visualization engine** (Section 4) — highest immediate debugging value given the trailing-stop and signal-inversion issues already open.
3. **Order flow + gamma panels** (5.1–5.2) — the underlying research is already synthesized.
4. **Per-leg config + portfolio composition rules** (Section 6).
5. **Portfolio management engine** (Section 7) — needs real per-leg backtest data from steps 1–2 to correlate against.
6. **Binary expiry + 4th panel** (5.3–5.4) — lowest urgency; the news/event calendar fix is still the higher near-term priority.
