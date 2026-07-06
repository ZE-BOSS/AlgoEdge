# Implementation Plan: Multi-Strategy Audit & Target Architecture

This document is the comprehensive audit and implementation plan for upgrading the AlgoEdge codebase to a robust, high-performance multi-strategy architecture. It incorporates extensive feedback regarding the integration of both the `SMC` and `CrashBoom` strategies, frontend performance optimization, dynamic charting, advanced analytics, and the resolution of architectural discrepancies.

---

## 1. Current State (Audit Summary)

### Documentation & Strategy Inventory
- **`SMC_Strategy-1.md` & `strategy2.md`**: Canonical specs for the Smart Money Concepts (SMC) strategy.
- **`CrashBoom_Strategy_Spec.md`**: Spec for the Crash & Boom synthetic indices strategy (Continuous Drift + Discrete Jump).
- **`RiskManagement_Spec.md`**: Spec for Multi-TP, Trailing Stop, and Break-Even rules.
- **`CompoundingPlan_Spec.md`**: Spec for stepped fixed-dollar risk compounding.
- **`Frontend_PWA_LLM_Spec.md`**: Spec for PWA and LLM integration.

### Codebase Status & Existing Discrepancies
- **Monolithic Engine**: The current codebase relies on a monolithic `smc/engine.py`. There is no actual multi-strategy architecture allowing `CrashBoom` to run simultaneously alongside `SMC`.
- **Global Parameter Leakage**: The frontend settings (`Settings/Strategy.jsx`) blindly display SMC parameters regardless of the selected strategy or symbol. Parameters are not stored per-symbol.
- **Frontend Sluggishness**: The UI (`Backtester.jsx`) hangs during backtesting and live journal rendering. This is caused by base64 encoded chart snapshots bloating JSON payloads and latency in DB/Redis calls.
- **Trade Fragmentation**: Multi-TP positions (TP1, TP2, TP3) are incorrectly logged as completely separate trades rather than sub-positions of a single grouped trade.
- **Broken / Ignored Features**:
  - News Filter and Session Filter incorrectly apply to Synthetic Indices.
  - Manual Bias Override is missing.
- **Statistics Deficit**: The backtest report lacks deep analytics (Win rate by bias, win rate by confluence type, starting/ending balances per period).

---

## 2. Discrepancies Resolution: SMC_Strategy_Spec.md vs strategy2.md vs Codebase

A deep audit revealed structural conflicts between the older `SMC_Strategy_Spec.md`, `strategy2.md`, and the `engine.py` codebase. The following resolutions have been chosen for implementation:

1. **Entry Confirmation Model**
   - **Conflict**: `SMC_Strategy_Spec.md` mechanically enters on an LTF BOS, ignoring candlesticks. `strategy2.md` enters on M5 candlestick patterns.
   - **Resolution**: Use the `strategy2.md` confirmation model (M5 candlestick patterns like Hammer/Engulfing). However, this M5 confirmation must occur strictly at the POIs defined in `SMC_Strategy_Spec.md`. Ensure that valid POIs include S&D (Supply & Demand) and Fibonacci zones, not just OBs and FVGs.
2. **Confluence Requirements**
   - **Conflict**: `SMC_Strategy_Spec.md` requires a hard minimum of 3 confluences. `strategy2.md` requires only 1 valid POI. The codebase uses a dynamic scoring system (e.g., >= 65).
   - **Resolution**: Enforce a strict minimum of 3 confluences. Crucially, there must be at least one confluence present on *each* of the following timeframes simultaneously: H1, M15 (which must be an OB, FVG, Fib, or S&D), and M5 (candlestick confirmation).
3. **Stop Loss Methodology**
   - **Conflict**: `SMC_Strategy_Spec.md` places SL `beyond_poi`. `strategy2.md` uses structural swing fallback.
   - **Resolution**: Implement hybrid logic. If an Order Block (OB) is present at the POI, use the `SMC_Strategy_Spec.md` SL placement (beyond the POI + buffer). If no OB is present, fall back to placing the SL at the structural swing point.
4. **IPDM Logic**
   - **Conflict**: `SMC_Strategy_Spec.md` enforces strict IPDM phases. Codebase currently exempts IPDM phase gating to improve frequency.
   - **Resolution**: Re-enable and enforce the `SMC_Strategy_Spec.md` IPDM logic (Accumulation, Manipulation, Expansion), but calculate and track it exclusively on the **H4 timeframe**.
5. **Risk & Sizing Logic**
   - **Conflict**: `SMC_Strategy_Spec.md` dictates Kelly/fixed-pct sizing internally. `strategy2.md` uses decoupled 5-TP tiers.
   - **Resolution**: Keep the `strategy2.md` decoupled risk and sizing logic.

---

## 3. Target Architecture & Implementation Details

The system must run as a **TRUE MULTI-STRATEGY system** supporting both `SMC` and `CrashBoom` seamlessly. **Crucially, all the features listed below apply to BOTH Live Trading and Backtesting.**

### 3.1 Multi-Strategy Engine & Parameters *(For Live Trading & Backtesting)*
- **Per-Symbol Configuration**: Strategy assignment and parameters are configured *per-symbol* (e.g., EURUSD -> SMC, Crash 500 -> CrashBoom).
- **Dynamic Parameter Forms**: Refactor React forms (`Settings.jsx`, `Backtester.jsx`) to read a schema definition based on the selected strategy. SMC parameters will not leak into CrashBoom forms.
- **Saved Trade Parameters**: Every generated trade logs a snapshot of the exact strategy parameters used, enabling historical review of which parameters yielded specific win rates.

### 3.2 Dynamic Charting & Frontend Performance *(For Live Trading & Backtesting)*
- **Eliminate Base64 Images**: Completely remove base64 static chart images from the backend API.
- **Dynamic Multi-Chart Component**: Build a `MultiTimeframeChart.jsx` component using TradingView Lightweight Charts with interactive tabs for H4, H1, M15, M5, and M1 (if CrashBoom).
- **Algorithmic Zone Marking**: The frontend charts will dynamically draw strategy zones based on backend metadata payloads:
  - *H4 Chart*: Mark Bias direction.
  - *H1 Chart*: Mark BOS (Break of Structure) zones.
  - *M15 Chart*: Mark Confluences (FVG, ChoCH, OB, S&D).
  - *CrashBoom*: Mark indicator zones (EMA boundaries, spikes).
- **Frontend Caching & Lazy Loading**: Chart data is fetched lazily per active tab and cached. Redis will be run locally to reduce pub/sub latency. Backtest results will be chunked/paginated.

### 3.3 Trade Management & Balances *(For Live Trading & Backtesting)*
- **Trade Grouping**: Update the database schema so that TP1, TP2, and TP3 are tracked as `sub_positions` of a single unified `Trade` entity.
- **Balance Tracking**: Explicitly record `balance_before` (balance prior to trade entry) and `balance_after` (balance after all sub-positions are closed) for every grouped trade.
- **Time-Based Filtering**: Filter and group trades by Day, Week, Month, and Year (calculated dynamically from the trade's specific timestamp, not calendar start).
- **Period Balances**: For each time-filtered group, display the `Starting Balance`, `Closing Balance`, and total `P&L` for that period.

### 3.4 Deep Statistics Engine *(For Live Trading & Backtesting)*
- **Verify Win Rate**: Audit and repair the core win rate calculation.
- **Granular Analytics**:
  - Calculate Win Rate by Bias (Bullish vs. Bearish).
  - Calculate Win Rate by Confluence Score.
  - *SMC Confirmations*: Win rate when FVG is present, OB is present, ChoCH is present.
  - *CrashBoom Confirmations*: Win rate per specific technical indicator used.

### 3.5 Infrastructure & Bug Fixes
- **Database Migration**: Migrate `backend/data/database.py` from PostgreSQL to a local SQLite database (`algoedge.db`) so it can be pushed to GitHub.
- **Manual Bias Override *(Both Modes)***: Introduce a toggleable "Manual Bias" parameter per symbol. If set to Bullish/Bearish, the engine overrides the H4 structural bias. Signals conflicting with the manual bias are rejected.
- **Fix Compounding Engine**: Audit and repair the `CompoundingPlan` module. *(Note: Compounding code updates will be submitted for manual approval before finalizing).*
- **Filter Exemptions**: Ensure `NewsFilter` and `SessionFilter` explicitly ignore symbols with the `SYNTHETIC` tag.

---

## 4. Execution Roadmap

1. **Phase 1: Foundation (Database & Redis)**
   - Migrate `database.py` to SQLite.
   - Configure local Redis instance for pub/sub.
2. **Phase 2: Core Refactoring**
   - Deconstruct the monolithic `smc/engine.py` into shared `core/` math, `smc/engine.py`, and `crashboom/engine.py`.
   - Update DB schema for Trade Grouping and Balance Tracking (`balance_before`, `balance_after`).
3. **Phase 3: Strategy Discrepancies Resolution**
   - Implement the hybrid Stop Loss logic, H4 IPDM tracking, and strict 3-confluence requirement across H1/M15/M5.
4. **Phase 4: Backend API & Analytics**
   - Implement deep stats (Win rate by bias/confluence/confirmation).
   - Strip base64 charting; implement OHLCV chunked payload delivery.
5. **Phase 5: Frontend Refactoring**
   - Build `MultiTimeframeChart.jsx` with zone drawing and caching.
   - Make Parameter Forms dynamic per strategy/symbol.
   - Implement Day/Week/Month/Year filters in Journal/Backtester.
6. **Phase 6: Manual Review Items**
   - Fix compounding engine and submit for manual approval.
