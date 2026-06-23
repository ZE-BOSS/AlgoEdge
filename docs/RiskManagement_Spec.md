# RiskManagement_Spec.md
## AlgoEdge — Risk Management System Specification
### Multi-TP | Trailing Stop | Break-Even | Multiple Positions | User-Controlled

---

## Overview

Risk management is the **primary layer** of the AlgoEdge system. Every position, every parameter, every execution path passes through the risk engine before anything reaches MT5. The user controls all risk parameters from the frontend settings panel, and those parameters are applied identically in both **live trading** and **backtesting** — what you backtest is exactly what runs live.

---

## 1. Risk:Reward Framework

### 1.1 RR Tiers (User Selectable)

| Tier | Label | RR Ratio | Use Case |
|------|-------|----------|----------|
| Minimum | Conservative | 1:3 | Default, lower risk tolerance |
| Standard | Balanced | 1:5 | Standard SMC setups |
| Extended | Trend Rider | 1:7 | Strong trend continuation moves |
| Maximum | Runner | 1:10 | HTF setups, weekly/daily targets |

- User sets their **preferred RR tier** in Settings
- Any signal whose calculated RR does not reach the **minimum tier (1:3)** is automatically rejected
- Signals meeting a higher tier are flagged for the user to decide whether to extend TP targets

### 1.2 Dynamic RR Calculation

```python
def calculate_rr(entry: float, sl: float, tp: float, direction: str) -> float:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return reward / risk if risk > 0 else 0.0

# Signal is rejected if:
if calculated_rr < user_config.min_rr:   # default min = 3.0
    reject_signal("RR below minimum threshold")
```

---

## 2. Multiple Take Profit System (TP1 / TP2 / TP3)

### 2.1 Architecture

Every trade can open **up to 3 sub-positions** (TP1, TP2, TP3), each:
- Same entry price
- Same stop loss
- Different take profit level
- Different lot size (configurable)

MT5 implementation: 3 separate orders placed simultaneously, each with a unique magic number suffix (`USER_MAGIC + 10` for TP1, `+ 20` for TP2, `+ 30` for TP3).

### 2.2 TP Level Calculation

```
Given:
  Entry Price  = E
  Stop Loss    = SL
  Risk         = R = |E - SL|  (1 R unit)

TP1 = E + (R × tp1_rr_multiplier)   # default 1:3 minimum
TP2 = E + (R × tp2_rr_multiplier)   # default 1:5 standard
TP3 = E + (R × tp3_rr_multiplier)   # default 1:7 or 1:10 (next liquidity)

For SELL trades:
TP1 = E - (R × tp1_rr_multiplier)
TP2 = E - (R × tp2_rr_multiplier)
TP3 = E - (R × tp3_rr_multiplier)
```

### 2.3 Position Size Split (User Configurable)

Default allocation across the three TP levels (adds to 100%):

| TP Level | Default % | Description |
|----------|-----------|-------------|
| TP1 | 40% | Closes first — books profit quickly, de-risks |
| TP2 | 35% | Mid target — standard move |
| TP3 | 25% | Runner — extended move / maximum RR target |

**Example:** 0.10 lot total risk → TP1 = 0.04 lots, TP2 = 0.035 lots, TP3 = 0.025 lots. MT5 rounds to 2 decimal places — `position_sizer` handles this rounding.

User can adjust split percentages: e.g. 50/30/20, 33/33/34, or 60/40/0 (no TP3).

### 2.4 TP Level Override Rules

- If calculated RR for TP2 < user's minimum RR: **disable TP2**, use only TP1 + TP3
- If calculated RR for TP3 < 1:5: **disable TP3**, use TP1 + TP2 only
- If only TP1 is achievable with minimum RR: **use single position mode** (no split)
- TP3 is always anchored to the **next external liquidity pool** (SMC target), not a fixed multiplier

---

## 3. Stop Loss System

### 3.1 Initial Stop Loss

Placed at one of the following (user selectable):

| Method | Description | When to Use |
|--------|-------------|-------------|
| `OB_EXTREME` | Below/above the Order Block that triggered the entry | Default |
| `SWING_POINT` | Below/above the manipulation candle's wick | Wider, more room |
| `FVG_EDGE` | Beyond the FVG zone boundary | Tighter, high confluence setups |
| `ATR_BASED` | Entry ± (ATR × multiplier) | Volatility-adjusted |

**Buffer:** Always add `sl_buffer_pips` beyond the chosen method to avoid stop hunting.

```python
def calculate_initial_sl(
    entry: float,
    direction: str,
    ob_extreme: float,
    swing_wick: float,
    sl_method: str,
    sl_buffer_pips: float,
    pip_value: float,
) -> float:
    buffer = sl_buffer_pips * pip_value

    if sl_method == "OB_EXTREME":
        raw_sl = ob_extreme
    elif sl_method == "SWING_POINT":
        raw_sl = swing_wick
    elif sl_method == "ATR_BASED":
        raw_sl = entry - (atr * atr_multiplier) if direction == "BUY" else entry + (atr * atr_multiplier)
    else:
        raw_sl = ob_extreme  # fallback

    # Apply buffer
    if direction == "BUY":
        return raw_sl - buffer
    else:
        return raw_sl + buffer
```

### 3.2 Break-Even (BE) System

Break-even moves the SL to the entry price (or entry + small profit buffer) after price reaches a defined R milestone.

**User-configurable parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `be_trigger_rr` | 1.0 | Move SL to BE after price reaches 1R in profit |
| `be_buffer_pips` | 2.0 | Move SL to entry + 2 pips (not exactly entry, to cover spread) |
| `be_on_tp1_hit` | `true` | Auto-move to BE when TP1 is closed |

**Break-even logic:**

```python
def check_break_even(position: Position, current_price: float, params: RiskParams) -> Optional[float]:
    """
    Returns the new SL price if break-even should be triggered, else None.
    """
    r_units_in_profit = position.unrealized_rr(current_price)

    if r_units_in_profit >= params.be_trigger_rr and not position.be_applied:
        buffer = params.be_buffer_pips * position.pip_value
        if position.direction == "BUY":
            new_sl = position.entry_price + buffer
        else:
            new_sl = position.entry_price - buffer

        # Only move SL in the favorable direction
        if position.direction == "BUY" and new_sl > position.current_sl:
            return new_sl
        elif position.direction == "SELL" and new_sl < position.current_sl:
            return new_sl

    return None
```

### 3.3 Trailing Stop Loss System

Four trailing methods, user selects per-strategy:

---

#### Method 1: Fixed Pip Trail (`FIXED_PIPS`)
```
Trail by a fixed number of pips behind price.
SL = current_price - trail_pips (BUY)
SL = current_price + trail_pips (SELL)

Good for: Consistent markets, scalping extensions
Config: trail_pips = 15 (default)
```

#### Method 2: ATR Trail (`ATR_TRAIL`)
```
Trail by ATR × multiplier (volatility-aware).
trail_distance = ATR(14) × atr_trail_multiplier
SL = highest_price_since_entry - trail_distance (BUY)

Good for: Volatile markets (Gold, BTC), riding trends
Config: atr_trail_multiplier = 1.5 (default)
Formula: SL = max(High - ATR_trail, previous_SL)  [ratchet — never moves back]
```

#### Method 3: Structure Trail (`STRUCTURE_TRAIL`) — SMC Native
```
Trail SL to below each confirmed Higher Low (BUY) or above each Lower High (SELL).
SL moves only after a new swing point is CONFIRMED (closed M15 candle).

Good for: SMC setups, riding institutional moves
Config: trail_timeframe = "M15", swing_length = 3
Most conservative — best for catching large moves
```

#### Method 4: Percentage Trail (`PCT_TRAIL`)
```
Trail by a percentage of current price.
trail_distance = current_price × trail_pct
SL = current_price × (1 - trail_pct) (BUY)

Good for: Higher-priced instruments (Gold, BTC)
Config: trail_pct = 0.005 (0.5%)
```

**Per-Position Trailing:** Each of TP1, TP2, TP3 sub-positions can have **different trailing configurations**:

| Sub-Position | Recommended Trail | Rationale |
|--------------|------------------|-----------|
| TP1 (40%) | No trail — hits TP1 and closes | Book profit quickly |
| TP2 (35%) | ATR trail after TP1 hit | Ride the move with protection |
| TP3 (25%) | Structure trail after TP2 hit | Maximum runner potential |

### 3.4 Trailing Stop State Machine

```
INITIAL:
  SL at initial calculated level
  No trailing active

AFTER TP1 HIT:
  TP1 sub-position closes
  BE applied on TP2 + TP3 positions
  TP2 trailing activates (ATR or user config)

AFTER TP2 HIT:
  TP2 sub-position closes
  TP3 structure trail activates
  SL trails to each new confirmed swing point

FINAL EXIT (TP3):
  Either TP3 hits → full close at target
  Or trailing SL is hit → close remaining at trail level
  Or manual close → user closes from dashboard
```

---

## 4. Multiple Positions Configuration

### 4.1 User Options

Users can enable or disable multiple-position mode:

```json
{
  "multi_position_mode": true,
  "tp_levels": 3,
  "tp_split": [40, 35, 25],
  "tp1_rr": 3.0,
  "tp2_rr": 5.0,
  "tp3_rr": 7.0,
  "sl_method": "OB_EXTREME",
  "sl_buffer_pips": 5.0,
  "be_trigger_rr": 1.0,
  "be_buffer_pips": 2.0,
  "be_on_tp1_hit": true,
  "trail_method_tp2": "ATR_TRAIL",
  "trail_method_tp3": "STRUCTURE_TRAIL",
  "atr_trail_multiplier": 1.5,
  "trail_pips": 15.0
}
```

**Single-position mode** (legacy): One order, one TP, one SL. Simpler, used when backtest shows better performance with single exit.

### 4.2 MT5 Order Placement for Multi-Position

```python
async def place_multi_position_trade(signal: TradeSignal, config: UserRiskConfig):
    positions = []
    total_volume = calculate_lot_size(config, signal)

    for i, tp_level in enumerate(config.get_tp_levels(signal)):
        volume_split = total_volume * config.tp_splits[i]
        volume_rounded = round_to_step(volume_split, symbol_info.volume_step)

        if volume_rounded < symbol_info.volume_min:
            continue  # Skip if too small

        result = await mt5_bridge.place_order(
            symbol    = signal.symbol,
            direction = signal.direction,
            volume    = volume_rounded,
            price     = signal.entry_price,
            sl        = signal.stop_loss,
            tp        = tp_level,
            magic     = user_config.magic_base + (i + 1) * 10,
            comment   = f"SMC_{signal.direction[:1]}_TP{i+1}_{user_id[:6]}",
        )

        if result["success"]:
            positions.append({
                "tp_level": i + 1,
                "ticket": result["ticket"],
                "volume": volume_rounded,
                "tp_price": tp_level,
                "trail_method": config.trail_methods[i],
            })

    return positions
```

---

## 5. Position Sizing Engine

### 5.1 Risk-Based Lot Calculation

```python
def calculate_lot_size(
    account_balance:  float,
    risk_pct:         float,
    entry_price:      float,
    stop_loss_price:  float,
    symbol:           str,
    contract_size:    float = 100000,
) -> float:
    """
    Calculate lot size so that if SL is hit, the loss equals exactly risk_pct% of balance.
    
    Formula: Lot = (Balance × Risk%) / (SL_distance_pips × pip_value_per_lot)
    """
    risk_amount   = account_balance * (risk_pct / 100)
    sl_distance   = abs(entry_price - stop_loss_price)
    pip_size      = get_pip_size(symbol)             # e.g. 0.0001 for EURUSD
    sl_pips       = sl_distance / pip_size
    pip_value     = pip_size * contract_size         # USD value of 1 pip per 1.0 lot

    if sl_pips == 0:
        return 0.0

    raw_lot = risk_amount / (sl_pips * pip_value)

    # Clamp and round to broker's volume constraints
    min_lot  = get_symbol_min_lot(symbol)
    max_lot  = get_symbol_max_lot(symbol)
    step     = get_symbol_lot_step(symbol)

    clamped  = max(min_lot, min(max_lot, raw_lot))
    rounded  = round(clamped / step) * step
    return round(rounded, 2)
```

### 5.2 Kelly Criterion (Optional, Advanced)

For users who want mathematically optimal sizing:

```python
def kelly_lot_size(win_rate: float, avg_win_r: float, avg_loss_r: float,
                   balance: float, max_kelly_fraction: float = 0.25) -> float:
    """
    Kelly fraction: f* = (W × B - L) / B
    Where W=win_rate, L=loss_rate, B=avg_win/avg_loss ratio
    
    We use half-Kelly or quarter-Kelly (max 25%) to reduce volatility.
    """
    loss_rate = 1 - win_rate
    b = avg_win_r / avg_loss_r if avg_loss_r > 0 else 1
    kelly = (win_rate * b - loss_rate) / b
    fraction = min(kelly, max_kelly_fraction)
    return balance * max(fraction, 0)
```

---

## 6. Portfolio-Level Risk Controls

### 6.1 Daily Loss Circuit Breaker

```
Each day at 00:00 GMT, daily_pnl counter resets.

If daily_pnl reaches -(max_daily_loss_pct × account_balance):
  → PAUSE all strategy engines for that user
  → Close all open positions at market
  → Send push notification: "Daily loss limit reached — trading paused"
  → Strategy resumes next calendar day at 00:01 GMT
  → Log event to database with timestamp
```

### 6.2 Weekly Drawdown Guard

```
If weekly_pnl reaches -(max_weekly_loss_pct × account_balance):
  → PAUSE strategy for remainder of the week
  → Notification: "Weekly drawdown limit hit"
  → Resume Monday 00:01 GMT
```

### 6.3 Consecutive Loss Streak

```
If consecutive_losses >= max_consecutive_losses (default: 5):
  → PAUSE strategy
  → Notification: "5 consecutive losses — review required"
  → User must manually re-enable from dashboard
  → This prevents automated compounding of losing streaks
```

### 6.4 Correlation Guard

```
If two open positions are on highly correlated pairs:
  (e.g. EURUSD + GBPUSD both long — ~85% correlation)
  → Warn user: "Correlated positions detected — total exposure may exceed risk limits"
  → Optionally block the second trade if combined risk > max_correlated_risk_pct
```

### 6.5 Risk Limits Summary Table

| Parameter | Default | User Range | Description |
|-----------|---------|------------|-------------|
| `risk_per_trade_pct` | 1.0% | 0.25–3.0% | Risk per single trade |
| `max_daily_loss_pct` | 5.0% | 1–10% | Daily circuit breaker |
| `max_weekly_loss_pct` | 10.0% | 3–20% | Weekly circuit breaker |
| `max_consecutive_losses` | 5 | 3–10 | Streak breaker |
| `max_concurrent_positions` | 3 | 1–10 | Max open trades |
| `max_correlated_risk_pct` | 4.0% | 1–8% | Max on correlated pairs |
| `min_rr` | 3.0 | 3.0–10.0 | Minimum RR to trade |
| `tp1_rr` | 3.0 | 2.0–5.0 | TP1 RR target |
| `tp2_rr` | 5.0 | 4.0–8.0 | TP2 RR target |
| `tp3_rr` | 7.0 | 5.0–10.0 | TP3 RR target / max |
| `tp_splits` | [40,35,25] | any 3 summing 100 | % per TP level |
| `be_trigger_rr` | 1.0 | 0.5–2.0 | R multiple to trigger BE |
| `be_buffer_pips` | 2.0 | 0–10 | Pips above entry for BE |
| `sl_method` | OB_EXTREME | see options | SL placement method |
| `sl_buffer_pips` | 5.0 | 2–20 | SL buffer pips |
| `trail_method_tp2` | ATR_TRAIL | 4 options | TP2 trailing method |
| `trail_method_tp3` | STRUCTURE | 4 options | TP3 trailing method |
| `atr_trail_multiplier` | 1.5 | 0.5–3.0 | ATR trail distance |
| `trail_pips` | 15.0 | 5–50 | Fixed pip trail distance |

---

## 7. Backtesting Risk Parity

### 7.1 Core Principle

The backtester uses the **identical risk engine** as live trading. There is NO separate backtesting risk logic. This guarantees:

> **What you backtest is exactly what runs live.**

### 7.2 Backtest Risk Configuration

When running a backtest:
1. User selects a **risk configuration preset** (or customizes all parameters)
2. Backtest runs using that exact config — same lot sizing, same BE trigger, same trailing, same TP splits
3. Every trade result recorded includes: which TP was hit, trailing SL high water mark, BE trigger time, max adverse excursion
4. Results are compared across different risk configs to find the optimal setup

### 7.3 Backtest Save Options

```
After backtest completes:
  ┌─────────────────────────────────────────────────────────┐
  │  Backtest Complete: EURUSD SMC_v1 (Jan–Jun 2025)        │
  │  Trades: 143  |  Win Rate: 68.5%  |  Sharpe: 1.84      │
  │  Total P&L: $3,241  |  Max DD: 7.2%                    │
  │                                                          │
  │  [💾 Save Full Results]  [📊 Save Summary Only]          │
  │  [🗑️ Discard] [📤 Export CSV]                           │
  └─────────────────────────────────────────────────────────┘

Save options:
  - FULL: All trade records + chart snapshots (can be large — warn user)
  - SUMMARY: Only aggregate stats + equity curve (lightweight)
  - DISCARD: Results shown but nothing persisted
  - CSV: Export trade list to .csv (no DB storage)
```

Saved backtests are viewable in the **Backtest History** page with full drill-down to individual trades.

---

## 8. Risk Analytics & Reporting

Every saved backtest and live session generates:

### 8.1 Per-Trade Metrics
- Entry/exit price and time
- SL placement method used
- Which TP level was hit (TP1/TP2/TP3/SL/Trail)
- Maximum Favorable Excursion (MFE) — how far trade went in our favor
- Maximum Adverse Excursion (MAE) — how far trade went against us before turning
- Realized RR vs planned RR
- Break-even triggered? (Y/N and at what price/time)
- Trail activated? (Y/N and trail method used)

### 8.2 Aggregate Risk Metrics

```python
@dataclass
class RiskReport:
    total_trades:          int
    winning_trades:        int
    losing_trades:         int
    win_rate:              float      # wins / total
    
    # P&L
    total_pnl:             float
    total_pnl_r:           float      # total in R multiples
    avg_win_r:             float
    avg_loss_r:            float
    best_trade_r:          float
    worst_trade_r:         float
    
    # Risk ratios
    profit_factor:         float      # gross_profit / gross_loss
    expectancy_r:          float      # (win_rate × avg_win) - (loss_rate × avg_loss) in R
    sharpe_ratio:          float      # annualized
    sortino_ratio:         float      # like Sharpe but only downside volatility
    calmar_ratio:          float      # annual_return / max_drawdown
    
    # Drawdown
    max_drawdown_pct:      float      # largest peak-to-trough %
    max_drawdown_abs:      float      # largest peak-to-trough $
    avg_drawdown_pct:      float
    max_drawdown_duration: int        # bars in longest drawdown
    
    # Streaks
    max_consecutive_wins:  int
    max_consecutive_losses: int
    avg_consecutive_wins:  float
    avg_consecutive_losses: float
    
    # TP performance breakdown
    tp1_hit_rate:          float      # % of trades hitting TP1
    tp2_hit_rate:          float      # % of trades hitting TP2
    tp3_hit_rate:          float      # % of trades hitting TP3
    sl_hit_rate:           float      # % of trades stopped out
    trail_hit_rate:        float      # % of trades closed by trailing SL
    be_hit_rate:           float      # % of trades where BE was triggered
    
    # Session breakdown
    london_win_rate:       float
    ny_win_rate:           float
    overlap_win_rate:      float
    
    # Symbol breakdown
    per_symbol:            dict       # {symbol: {win_rate, pnl, trades}}
```

---

## 9. Frontend Risk Control Panel (UI Specification)

### 9.1 Risk Settings Page Layout

```
┌─── RISK MANAGEMENT SETTINGS ──────────────────────────────────────────────┐
│                                                                             │
│  POSITION SIZING                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Risk per trade:  [1.0%] ──────●──────── (0.25% — 3.0%)              │  │
│  │ Sizing method:   ◉ Fixed %   ○ Kelly Criterion                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  TAKE PROFIT LEVELS                                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Mode:  ◉ Multi-Position (TP1/TP2/TP3)   ○ Single Position           │  │
│  │                                                                       │  │
│  │  TP1  RR: [1:3.0] ──●────────   Size: [40%] ────●────               │  │
│  │  TP2  RR: [1:5.0] ────●──────   Size: [35%] ──────●──               │  │
│  │  TP3  RR: [1:7.0] ──────●────   Size: [25%] ────────●               │  │
│  │                                                                       │  │
│  │  Minimum RR to take trade: [1:3.0] ──●────────────────               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  STOP LOSS & TRAILING                                                       │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ SL Method:  ◉ OB Extreme  ○ Swing Point  ○ FVG Edge  ○ ATR-Based    │  │
│  │ SL Buffer:  [5.0 pips]                                               │  │
│  │                                                                       │  │
│  │ Break-Even:  ◉ Enable                                                │  │
│  │   Trigger at: [1.0 R]     Buffer: [2.0 pips]                         │  │
│  │   Apply on TP1 hit: ◉ Yes  ○ No                                      │  │
│  │                                                                       │  │
│  │ TP2 Trail:  ◉ ATR Trail  ○ Fixed Pips  ○ Structure  ○ Pct           │  │
│  │   ATR Multiplier: [1.5×]                                             │  │
│  │                                                                       │  │
│  │ TP3 Trail:  ○ ATR Trail  ○ Fixed Pips  ◉ Structure  ○ Pct           │  │
│  │   Timeframe: [M15]   Swing Length: [3]                               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  CIRCUIT BREAKERS                                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Max daily loss:      [5.0%] ──────●──────── (1–10%)                 │  │
│  │ Max weekly loss:    [10.0%] ──────────●──── (3–20%)                 │  │
│  │ Max consecutive SL: [5 trades]                                       │  │
│  │ Max open positions: [3]                                              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│              [🔄 Reset to Defaults]   [💾 Save Configuration]               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Live Risk Dashboard Widget

On the main dashboard, always-visible risk status widget:

```
┌─── RISK STATUS ───────────────────────────────┐
│  Account:  $10,000.00  |  Equity: $10,143.20  │
│  Daily P&L:    +$143.20  (+1.43%)  🟢          │
│  Daily Limit:  -$500.00  (used 0%)             │
│  Open Risk:    $98.50 (0.99% of balance)       │
│  Positions:    2 / 3 max                       │
│  Streak:       3W / 0L    ✅                   │
└───────────────────────────────────────────────┘
```

---

*Version 1.0 | AlgoEdge Risk Management Specification | June 2026*

---

## 10. Compounding Plan System

> **Full Specification:** See `CompoundingPlan_Spec.md` for complete implementation details, the corrected step table, advance/downgrade logic, and instrument profiles.

### 10.1 What It Does

The compounding plan replaces percentage-based risk with **stepped fixed-dollar risk**. As the account grows, the risk amount steps up according to a predefined table. As the account shrinks, it steps back. This is anti-martingale by design — you increase size when winning, reduce when losing.

**Toggle:** User enables/disables in Settings → Risk → Compounding. When disabled, standard `risk_per_trade_pct` applies.

### 10.2 Default Plan (1:3 RR, 18 Steps)

Starting from $20, the plan grows the account to $11,120 if all 18 steps are completed (all wins). Risk tiers scale non-linearly, staying conservative early:

| Steps | Risk per Trade | Account Range |
|-------|---------------|---------------|
| 1–2 | $20 | $0 → $140 |
| 3–4 | $30 | $140 → $320 |
| 5–6 | $50 | $320 → $620 |
| 7–8 | $100 | $620 → $1,220 |
| 9–10 | $200 | $1,220 → $2,420 |
| 11–12 | $250 | $2,420 → $3,920 |
| 13–14 | $300 | $3,920 → $5,720 |
| 15–16 | $400 | $5,720 → $8,120 |
| 17–18 | $500 | $8,120 → $11,120 |

### 10.3 Step Advance Modes

| Mode | Behaviour |
|------|-----------|
| **AUTO** | Advance automatically when balance crosses next threshold |
| **CONSERVATIVE** | Require N consecutive wins before advancing (default N=2) |
| **MANUAL** | User presses Advance/Retreat button in dashboard |

### 10.4 Integration with Multi-TP

When multi-position mode is ON alongside compounding:
```
Step 7 risk = $100 total
TP1 lot: sized to risk $40 (40% split)
TP2 lot: sized to risk $35 (35% split)
TP3 lot: sized to risk $25 (25% split)
Total dollar exposure = exactly $100 ✓
```

### 10.5 Compounding + Backtesting

The backtester uses the **identical compounding engine** as live trading. Run the same backtest with compounding ON vs OFF to compare growth curves. Backtest report includes a step-timeline view showing which risk level was active for each trade.

---

## 11. Instrument Coverage & Primary Focus

### 11.1 Primary Instruments

| Priority | Instrument | Type | Why |
|----------|-----------|------|-----|
| **1st** | Volatility 75 Index (V75) | Synthetic | 24/7, clean SMC structure, low capital entry |
| **1st** | XAUUSD (Gold) | Commodity | High volatility, strong SMC moves, globally traded |
| **2nd** | Volatility 25 / 50 Index | Synthetic | Lower volatility, ideal for learning/testing |
| **3rd** | EURUSD / GBPUSD | Forex | High liquidity, classic SMC behaviour |
| **4th** | US30, BTCUSD | Index/Crypto | Added once core pairs are validated |

### 11.2 Synthetic Indices on Deriv MT5

Synthetic indices are available on Deriv MT5 accounts (separate from forex accounts). Key properties:
- **24/7 trading** — no session filter, no market close gaps
- **No news events** — economic data does not affect price
- **Algorithm-driven** — clean, consistent price behaviour ideal for SMC
- **Low minimum deposit** — $20 is sufficient for the compounding plan
- **Leverage up to 1:1000** on selected indices

For our bot: session filter and news filter are automatically disabled when a synthetic symbol is detected via the `InstrumentProfile`. SMC parameters use shorter swing lengths and tighter FVG minimums.

### 11.3 Position Sizing for Synthetics

Synthetic lot sizing uses points (not pips). The `risk_dollars_to_lots()` function in `compounding.py` handles this automatically per instrument profile. Always fetch live symbol info from MT5 to get accurate point values — they can vary slightly by account type.
