# Databento signals, Deriv MT5 execution

Status: design, 26 Nov 2026. Goal: run chosen strategies on CME futures data (Databento),
place the orders on Deriv through MT5, and prove it in shadow mode before real money.

## Why
The futures research (Jan–Nov 2026, `/opt/algoedge/research_data/report.json`) found the
edge in futures bars plus order flow (gold VWAP + 6h CVD filter: +30R → better on unseen
Jul–Nov). Deriv's CFD prices follow the futures with a moving basis and their own spread;
the strategy should read the deeper market and only trade on Deriv.

## Pieces

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
