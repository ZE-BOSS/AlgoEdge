# SMC Strategy Final Implementation Spec

This document serves as the **SINGLE SOURCE OF TRUTH** for the Smart Money Concepts (SMC) strategy implementation inside `backend/strategies/strategy_one/`.

## 1. Multi-Timeframe Architecture

The strategy evaluates the market across four distinct timeframes hierarchically:
- **H4 (High Timeframe - HTF)**: Determines structural bias (`BULLISH` or `BEARISH`) via the `MarketStructureDetector`. Also tracks the IPDM (Inducement / Phase Detection).
- **H1 (Intermediate Timeframe - ITF)**: Confirms structural alignment. Computes intermediate Order Blocks and tracks H1 BOS. It also calculates the per-bar ATR to measure relative volatility.
- **M15 (Low Timeframe - LTF)**: The core execution timeframe. Identifies the Change of Character (ChoCH) trigger and generates precise Order Blocks (OB) and Fair Value Gaps (FVG) from the ChoCH origin.
- **M5 (Execution Timeframe)**: Evaluates confirmation candlesticks to ensure price action is rejecting the M15 POI before entry.

## 2. Core Technical Components

### 2.1 Market Structure Detection
Market structure is calculated dynamically per timeframe using the `MarketStructureDetector(swing_length, min_bos_count)`:
- **Swing Points**: `swing_length` specifies how many bars before and after a pivot must be lower/higher to classify as a swing high/low. (Default H4=5, H1=3, M15=2, M5=2).
- **BOS (Break of Structure)**: Continuation of trend. The engine requires `min_bos_count` (Default: 2) consecutive BOS in the same direction to establish a "confirmed" trend.
- **ChoCH (Change of Character)**: The first break of a structural swing point in the *opposite* direction of the prevailing trend.

### 2.2 IPDM (Inducement / Phase Detection)
IPDM logic is enforced **exclusively on the H4 timeframe** using the `IPDMDetector`.
The market phases are tracked strictly in sequence:
1. **ACCUMULATION**: Chopping within a tight range. Detected by identifying 3 consecutive overlapping candles or tight swing points.
2. **MANIPULATION**: A fakeout sweeping liquidity beyond the accumulation range. Price must pierce the accumulation high/low but close back inside or near the boundary.
3. **EXPANSION**: The true directional move following the manipulation. Entries are ONLY sought during this phase.

### 2.3 Order Blocks, FVGs, and S&D Zones
- **Order Blocks (OB)**: Detected via `OrderBlockDetector`. An OB is valid if:
  1. It is the last opposite-color candle before an impulsive move.
  2. The impulse is strictly defined as `ob_impulse_ratio` (Default 1.5x) larger than the OB candle itself.
  3. The OB has not been mitigated (touched) more than `ob_max_touch_count` (Default 2) times.
- **Fair Value Gaps (FVG)**: Detected via `FVGDetector`. Defined as a 3-candle pattern where the gap between candle 1's shadow and candle 3's shadow is strictly greater than `fvg_min_gap_pips`. 
- **Supply & Demand (S&D)**: Detected via `SupplyDemandDetector`. Tracks broader areas of structural consolidation before aggressive breakouts. Evaluated as a valid POI fallback if no explicit Order Block is found.

## 3. Strict 3-Confluence Filter Workflow

A strict alignment is required before execution. If ANY check fails, `engine.py` returns `None`.

### Step 1: HTF Alignment
- H4 trend must be `BULLISH` or `BEARISH`. `NEUTRAL` discards the signal.
- The engine supports a `manual_bias_overrides` dictionary per symbol. If the user overrides a symbol to `BULLISH`, the engine ignores the H4 `MarketStructureDetector` output and forces `BULLISH`.

### Step 2: Intermediate Alignment
- The H1 trend must align identically with the H4 bias (unless overridden manually). 

### Step 3: Hard Filters
- **Session Killzones**: If `session_filter_enabled` is True and the instrument is NOT `SYNTHETIC`, the M5 entry timestamp must fall inside the LONDON, NY, or LONDON/NY overlap session.
- **Asian Range Sweep**: If `enforce_asian_range_sweep` is True, the M15 Asian range must have been mapped and explicitly swept by price before continuing.
- **Premium/Discount**: (Optional) Uses H4 swings. Buys must be strictly in the Discount quadrant (<50%); Sells must be strictly in the Premium quadrant (>50%).

### Step 4: POI Selection (M15)
- The M15 timeframe must produce a ChoCH matching the H4 trend direction.
- Near the origin of that ChoCH, the engine searches for unmitigated Order Blocks (`obs`), FVGs (`fvgs`), or Supply & Demand Zones (`in_sd_zone`).
- If no valid POI is found within the M15 timeframe, the signal is rejected (Strict 3-confluence requirement).

### Step 5: Confluence Scorer Gate
The `ConfluenceScorer` ranks the total setup out of 100 points. The signal must score above the `confluence_threshold` (Default: 55).
Points are awarded for:
1. **HTF Bias Alignment** (+15 points)
2. **Candlestick Tier** (+15 points for Tier 1 Engulfing/Pinbar)
3. **Liquidity Sweep** (+15 points if the ChoCH swept recent highs/lows)
4. **Fresh OB** (+15 points) OR **S&D Zone Fallback** (+10 points)
5. **FVG Presence** (+10 points if inside OB, +5 if stand-alone)
6. **H1 Alignment** (+10 points)
7. **LTF ChoCH** (+10 points)
8. **OTE / Fibonacci** (+5 points if entry falls within the 62%-79% Optimal Trade Entry (Premium/Discount) grid derived from H4 swings).
9. **Killzone** (+5 points).

### Step 5: M5 Confirmation Stick
- Price must be currently inside the M15 POI.
- The engine examines the latest closed M5 candle (`candles.iloc[-1]`).
- For Buys: It must be a bullish engulfing or bullish pin bar.
- For Sells: It must be a bearish engulfing or bearish pin bar.
- Entry price is explicitly the `close` of this M5 confirmation candle.

## 4. Stop Loss & Take Profit (Hybrid Approach)

### Hybrid Stop Loss Logic
Stop Loss is calculated dynamically on the M15 structure, incorporating a `buffer` (default 5.0 * pip_size).
1. **Priority 1 (Structural Swing)**: Finds the absolute lowest/highest M15 swing point (`m15_swings`) that formed the ChoCH. Placed beyond that swing.
2. **Priority 2 (OB Extreme - Fallback)**: If no clear structural swing is below/above entry, SL is placed exactly at the bottom/top of the tapped Order Block + buffer.
3. **Priority 3 (Candle Extreme - Emergency Fallback)**: If neither exists, SL is placed at the extreme shadow of the M5 confirmation entry candle.

### Take Profit Placement
TP is strategically placed at the nearest major H1 liquidity pool (swing point):
- **Buys**: `tp = float(min(h1_highs, key=lambda s: float(s["price"]))["price"])`
- **Sells**: `tp = float(max(h1_lows, key=lambda s: float(s["price"]))["price"])`

## 5. Live Trading Integration (`bot_service.py`)

The strategy is orchestrated dynamically in `bot_service.py` via the registry:
- Every symbol has a `strategy_id` defined in its `InstrumentSettings`.
- `bot_service.py` loops through `self.symbols`, queries the `registry` for the engine matching `strategy_id`, and instantiates an independent engine instance per symbol in `self.engines[symbol]`.
- All strategy parameters (including Killzone toggles, IPDM, compounding, and risk configurations) are identically applied across live trading and the simulated backtesting environment (`api/routes/backtest.py`).
