# Databento signals, Deriv MT5 execution

Status: design, 26 Nov 2026. Goal: run chosen strategies on CME futures data (Databento),
place the orders on Deriv through MT5, and prove it in shadow mode before real money.

## Why
The futures research (Jan–Nov 2026, `/opt/algoedge/research_data/report.json`) found the
edge in futures bars plus order flow (gold VWAP + 6h CVD filter: +30R → better on unseen
Jul–Nov). Deriv's CFD prices follow the futures with a moving basis and their own spread;
the strategy should read the deeper market and only trade on Deriv.

## Result of the comparison (26 Nov, `research_data/compare.json`)

Same strategies, same engine, Jan–Nov 2026, run on Deriv's MT5 prices with Deriv costs, with the
Databento order-flow filter applied by time to the MT5 trades:

| | futures | Deriv MT5 | MT5 + Databento filter (unseen Jul–Nov) |
|---|---|---|---|
| gold VWAP_v1 (cvd6h < 0.0113) | 88 tr, +30.2R, PF 1.57 | 86 tr, +27.3R, PF 1.60, DD 7.0R | 11.6R → 16.3R, PF 2.2, DD 3.9R |
| nasdaq VWAP_v1 (no filter) | 44 tr, +14.6R, PF 1.63 | 46 tr, +26.4R, PF 2.21, DD 4.6R | — |
| nasdaq SigmaBreakout (big30 < 4.99) | 167 tr, +7.5R | 169 tr, +5.4R, PF 1.45, DD 1.7R | 2.4R → 2.5R, DD 1.4R |
| nasdaq M30ORB | +16.1R | **−3.0R** | — (futures-only edge: dropped) |
| gold NQPrecision | +14.4R | 18 tr since 26 Aug only (MT5 M1 depth) | inconclusive |

So the signals do not need futures candles: the edge is there on Deriv's own prices, and the
order-flow filter still helps when applied to MT5-generated signals. **Phase 1 is therefore an
order-flow gate on MT5-driven slots** (no candle feed, no basis translation). The futures candle
feed below stays as phase 2, for strategies whose edge only exists on futures bars.

## Phase 1: order-flow gate (build first)
- `InstrumentSettings.of_filter = {"feature": "cvd6h", "op": "<", "threshold": 0.0113}`.
- The live `OrderFlowFeed` keeps per-minute delta/volume/big-trade series for the mapped future
  (`ORDERFLOW_MAP`); features computed exactly as `scripts/of_optimise.py` (minutes strictly before).
- `BotService` checks the gate after the strategy signals, before risk/execution; a blocked
  signal is logged with the feature value. Feed stale > 90 s → gate fails closed (no trade).
- Shadow first: `of_filter.mode = "shadow"` logs what the gate would have blocked and trades
  anyway; `"enforce"` blocks.

## Phase 2 pieces (futures candles)

1. **Candle feed** (`backend/orderflow/candles.py`, new)
   - Warm-up: Databento historical `ohlcv-1m` for `GC.v.0` / `NQ.v.0` (L0, included in the plan),
     enough bars for each strategy's longest window (`strategies/windows.warmup_days`).
   - Live: the existing `OrderFlowFeed` trades stream already sees every print — build the
     current minute bar from it (open/high/low/close/volume/delta) and close it on the minute.
   - Resample M1 → M5/M15/M30/H1/H4 the way `scripts/bt_futures.py` does (left-labelled), so
     live bars equal backtest bars.
   - Exposes `get_historical_data(symbol, tf, count)` with the DataFetcher shape (time, OHLC,
     tick_volume, real_volume, spread=0).

2. **Per-slot data source** (`InstrumentSettings.data_source: "mt5" | "databento"`, default mt5)
   - `BotService` (bot_service.py ~1386) asks the candle feed instead of `DataFetcher` when the
     slot says databento. Everything after (strategy engine, risk, exits) is unchanged.
   - Map: `GC.v.0 → XAUUSD`, `NQ.v.0 → US Tech 100` (`ORDERFLOW_MAP`, already set on live).

3. **Basis translation** (signal → Deriv order)
   - basis = futures last − MT5 mid, a 5-minute median of 1-second samples (already computed for
     the levels snapshot).
   - Entry: market on MT5 at its own price. Stop and targets keep their **distance in points**
     from entry (distance, not level, carries the risk); limit entries shift by the basis.
   - Refuse a signal when the basis moved > 2× its 1-hour median in the last minute (roll day,
     feed gap) and when the feed is older than 90 s.

4. **Order-flow filter** in the strategy slot: `of_filter: {"feature": "cvd6h", "op": "<",
   "threshold": 0.0113}` read from the same feed (features as in `scripts/of_optimise.py`,
   minutes strictly before the signal).

5. **Shadow mode** (`execution: "shadow"` on the slot)
   - Every would-be order is written to `shadow_trades` (time, side, futures entry/stop/target,
     translated MT5 entry/stop/target, MT5 bid/ask at that second) and filled against MT5 ticks
     by the position manager's simulator — no broker order.
   - Daily report on the Order flow tab: shadow R vs the futures backtest for the same days,
     average entry slippage in R, signals refused by the basis guard.

## Rollout
1. Comparison backtest on MT5 history (`scripts/bt_mt5_compare.py`) — does the edge survive
   Deriv's prices and costs?
2. Candle feed + data-source switch + shadow mode on **demo** for 1–2 weeks.
3. Go live at 0.25% risk per trade if shadow R is within 25% of the futures replay over the
   same days and average slippage is under 0.1 R; scale to 1% after 30 trades.

## Limits
- Databento live data is licensed for personal use: the levels and bars stay internal;
  VVIP clients only receive the resulting trades.
- Futures trade 23h; Deriv gold/NQ hours differ slightly — signals outside Deriv's session are
  dropped, not queued.
