# CompoundingPlan_Spec.md
## AlgoEdge — Compounding Plan System Specification
### Stepped Fixed-Dollar Risk | Synthetic Indices | Gold | Forex Volatility Pairs

---

## Overview

The compounding system is an **optional, toggleable** growth engine. When enabled, it replaces percentage-based risk sizing with a **stepped fixed-dollar risk plan** — the user climbs a predefined table of risk tiers as their account grows, and steps back down when the account shrinks. This mirrors the anti-martingale principle: press when winning, reduce when losing.

**Key principle:** The compounding plan does NOT affect strategy logic, entry signals, or exit rules. It only changes **how many dollars are risked per trade**. All SMC signals, TP/SL levels, and trailing rules remain unchanged.

Can be **enabled or disabled per user in Settings → Risk → Compounding**. When disabled, the standard percentage-based risk system runs.

---

## 1. The 1:3 RR Compounding Plan (Default)

### 1.1 Corrected Reference Table

> **Note:** The source reference image contains a typo at Step 4 (shows $230, should be $320). The corrected, mathematically consistent table is:

| Step | Risk ($) | Reward at 3R ($) | Account After Win ($) | Account Threshold to Enter ($) |
|------|----------|------------------|-----------------------|-------------------------------|
| 1    | 20       | 60               | 80                    | 0                             |
| 2    | 20       | 60               | 140                   | 80                            |
| 3    | 30       | 90               | 230                   | 140                           |
| 4    | 30       | 90               | **320** *(image: 230)*| 230                           |
| 5    | 50       | 150              | 470                   | 320                           |
| 6    | 50       | 150              | 620                   | 470                           |
| 7    | 100      | 300              | 920                   | 620                           |
| 8    | 100      | 300              | 1,220                 | 920                           |
| 9    | 200      | 600              | 1,820                 | 1,220                         |
| 10   | 200      | 600              | 2,420                 | 1,820                         |
| 11   | 250      | 750              | 3,170                 | 2,420                         |
| 12   | 250      | 750              | 3,920                 | 3,170                         |
| 13   | 300      | 900              | 4,820                 | 3,920                         |
| 14   | 300      | 900              | 5,720                 | 4,820                         |
| 15   | 400      | 1,200            | 6,920                 | 5,720                         |
| 16   | 400      | 1,200            | 8,120                 | 6,920                         |
| 17   | 500      | 1,500            | 9,620                 | 8,120                         |
| 18   | 500      | 1,500            | 11,120                | 9,620                         |

### 1.2 Plan Analysis

**Starting capital:** $20 (designed for Deriv synthetics minimum deposit)

**Risk tiers:**
- Micro tier ($20 risk): Steps 1–2
- Small tier ($30 risk): Steps 3–4
- Base tier ($50 risk): Steps 5–6
- Standard tier ($100 risk): Steps 7–8
- Growth tier ($200 risk): Steps 9–10
- Advanced tier ($250 risk): Steps 11–12
- Professional tier ($300 risk): Steps 13–14
- Senior tier ($400 risk): Steps 15–16
- Expert tier ($500 risk): Steps 17–18

**Mathematical properties:**
- Total gain from Step 1 to 18 (all wins): $11,100 from a $20 account
- Average gain per step: $617
- Risk scales non-linearly — stays conservative early (risk doubles every ~4 wins)
- At Step 18 with $500 risk, that's only ~4.5% of the $11,120 account (conservative)

**Why 2 steps per risk tier:**
Each risk amount is used for 2 consecutive trades before upgrading. This prevents reckless advancement from a single lucky win and gives the strategy time to prove consistency at each level.

---

## 2. Compounding Engine Logic

### 2.1 Step Determination (Auto Mode)

```python
def get_current_step(account_balance: float, plan: CompoundingPlan) -> CompoundingStep:
    """
    Determine the correct step based on current account balance.
    Returns the highest step whose entry threshold <= account balance.
    """
    current_step = plan.steps[0]  # default to step 1
    for step in plan.steps:
        if account_balance >= step.entry_threshold:
            current_step = step
        else:
            break
    return current_step
```

### 2.2 Step Advance Modes

**AUTO (default):** Step advances automatically when account balance crosses the next entry threshold. No manual action required.

**CONSERVATIVE:** Requires `n` consecutive wins AT the current risk level before advancing. Smoother progression, avoids advancing prematurely.

```python
def should_advance_step(
    consecutive_wins_at_level: int,
    required_wins: int,     # user-configured, default 2
    account_balance: float,
    next_step_threshold: float,
) -> bool:
    return (
        consecutive_wins_at_level >= required_wins and
        account_balance >= next_step_threshold
    )
```

**MANUAL:** Step only changes when the user explicitly clicks Advance/Retreat in the dashboard. Good for users who want full control.

### 2.3 Step Downgrade (Drawdown Protection)

When the account falls, the system steps back to the appropriate lower risk level. Two modes:

**THRESHOLD (default):** Steps down when account drops below a lower step's threshold.

```python
def check_downgrade(account_balance: float, current_step: int, plan: CompoundingPlan) -> int:
    """Returns the appropriate step for the current balance."""
    for i, step in enumerate(reversed(plan.steps)):
        if account_balance >= step.entry_threshold:
            return step.step_number
    return 1  # floor at step 1
```

**LOSS-COUNT:** Steps down after `n` consecutive losses at the current level (regardless of balance).

```python
if consecutive_losses_at_level >= max_losses_before_downgrade:
    current_step = max(1, current_step - 1)
    reset_consecutive_counters()
```

### 2.4 State Tracking (Redis)

```
compounding:state:{user_id} → {
    "current_step": 7,
    "risk_amount": 100.0,
    "entry_balance": 620.0,
    "consecutive_wins": 1,
    "consecutive_losses": 0,
    "total_wins_at_level": 1,
    "last_updated": 1718000000
}
```

### 2.5 Risk Amount → Lot Size Conversion

The compounding plan outputs a **dollar risk amount**. The position sizer converts this to lots:

```python
def risk_dollars_to_lots(
    risk_dollars: float,
    entry: float,
    stop_loss: float,
    symbol_profile: InstrumentProfile,
) -> float:
    sl_distance = abs(entry - stop_loss)
    point_value = symbol_profile.point_value_per_lot
    sl_points = sl_distance / symbol_profile.point_size
    raw_lots = risk_dollars / (sl_points * point_value)
    return round_to_step(raw_lots, symbol_profile.lot_step)
```

---

## 3. Multi-TP Compounding Integration

When multi-position mode is active alongside compounding:

```
Base risk = $100 (from current compounding step 7)
TP split:  40% / 35% / 25%

TP1 lot = calculated to risk $40  (40% of $100)
TP2 lot = calculated to risk $35  (35% of $100)
TP3 lot = calculated to risk $25  (25% of $100)
Total exposure = $100 (matches step risk exactly)
```

This ensures the compounding plan's risk amount is always honored exactly, even in multi-position mode.

---

## 4. Custom Compounding Tables

Users can replace the default 18-step plan with their own:

```json
{
  "plan_name": "My Custom Plan",
  "rr_ratio": 3,
  "steps": [
    {"step": 1, "risk": 50,  "entry_threshold": 0},
    {"step": 2, "risk": 50,  "entry_threshold": 200},
    {"step": 3, "risk": 100, "entry_threshold": 350},
    {"step": 4, "risk": 100, "entry_threshold": 650},
    {"step": 5, "risk": 200, "entry_threshold": 950}
  ]
}
```

Validation rules:
- Each step's `entry_threshold` must equal previous step's `account_after_win`
- `account_after_win` = `entry_threshold` + `reward_at_rr`
- At least 2 steps required
- Risk amounts must be > 0 and <= 10% of starting capital for Step 1

---

## 5. Compounding Backtesting

The backtesting engine respects compounding mode identically to live:

```python
def backtest_with_compounding(candles, config: UserConfig, initial_balance: float):
    balance = initial_balance
    compounding = CompoundingEngine(config.compounding, config.risk)
    
    for signal in generate_signals(candles, config.smc):
        step = compounding.get_current_step(balance)
        risk_amount = step.risk_amount
        
        lots = risk_dollars_to_lots(risk_amount, signal, config.instrument)
        trade = simulate_trade(signal, lots, config.risk)
        
        balance += trade.pnl
        compounding.update(trade.won, balance)
        
        record_trade(trade, step=step.step_number, risk_amount=risk_amount)
```

Backtest reports include per-step breakdown:
- How many trades executed at each step
- Win rate per step
- Average R per step
- Step transition events (advances and downgrades)
- Time spent at each step

---

## 6. Instrument-Specific Configuration

Different instruments require different compounding plan configurations, because:
1. Their pip/point values differ dramatically
2. Their volatility profiles differ
3. Some (synthetics) trade 24/7; others have sessions

### 6.1 Instrument Profiles

See `InstrumentProfile` in `params.py` for the full definition. Key differences:

| Instrument | Type | Session Filter | News Filter | Point Value (0.01 lot) | Min Lot | Recommended Swing Length |
|---|---|---|---|---|---|---|
| V75 Index | Synthetic | OFF | OFF | ~$0.01/point | 0.001 | 3 |
| V25 Index | Synthetic | OFF | OFF | ~$0.01/point | 0.001 | 3 |
| V50 Index | Synthetic | OFF | OFF | ~$0.01/point | 0.001 | 3 |
| V100 Index | Synthetic | OFF | OFF | ~$0.01/point | 0.001 | 3 |
| Boom 1000 | Synthetic | OFF | OFF | ~$0.01/point | 0.001 | 5 |
| Crash 1000 | Synthetic | OFF | OFF | ~$0.01/point | 0.001 | 5 |
| XAUUSD | Commodity | ON (London/NY) | ON | $1.00/pip | 0.01 | 5 |
| EURUSD | Forex | ON (London/NY) | ON | $1.00/pip | 0.01 | 5 |
| GBPUSD | Forex | ON (London/NY) | ON | $0.92/pip | 0.01 | 5 |
| US30 | Index | ON (NY only) | ON | $1.00/point | 0.01 | 5 |

### 6.2 Synthetic Indices — Special Handling

<cite index="100-1">Deriv's synthetic indices are algorithm-based instruments that maintain statistically consistent volatility conditions, simulating real market dynamics in a controlled environment. They are ideal for algorithmic development, educational use, and strategy optimisation.</cite>

**Key properties for our bot:**

**1. No session filter:** <cite index="105-1">Unlike Forex, there is no 'London Open' or 'New York Session' for synthetic indices. They have constant volatility. However, many traders find that the markets are 'cleanest' during high-volume periods when most retail traders are active, typically between 07:00 and 16:00 GMT.</cite>

**2. No news filter:** <cite index="100-1">Volatility indices are fully algorithm-driven and are not influenced by macroeconomic events, political developments, or market sentiment. Their price movements are generated by audited algorithms rather than supply-and-demand dynamics. This means traders do not need to account for earnings releases, central bank announcements, or geopolitical news when analysing these markets.</cite>

**3. Lot size warning:** <cite index="105-1">You cannot apply your EUR/USD lot sizes to Volatility Indices. If you try to trade 1.0 lot on V75 with a $1,000 account, your account will likely be gone in seconds. On V75, a move from 500,000 to 500,001 is one point. Because the index price is so high, it can move 20,000 to 50,000 points in a single day.</cite>

**4. SMC works on synthetics:** V75 especially shows clean market structure with identifiable Order Blocks, FVGs, and liquidity pools because its algorithmic nature creates consistent, repeatable patterns that respond well to technical analysis.

**5. Account requirements:** Deriv MT5 synthetic accounts can start from as little as $5–$20, making the compounding plan's starting point ($20 risk) ideal for Deriv synthetics traders.

**Synthetic symbol names on Deriv MT5:**
```
"Volatility 10 Index"    → V10
"Volatility 25 Index"    → V25
"Volatility 50 Index"    → V50
"Volatility 75 Index"    → V75  ← Primary target
"Volatility 100 Index"   → V100
"Boom 1000 Index"        → Boom 1000
"Crash 1000 Index"       → Crash 1000
"Boom 500 Index"         → Boom 500
"Crash 500 Index"        → Crash 500
"Step Index"             → Step Index
```

**Recommended SMC parameters for synthetics (V75):**

```python
SYNTHETIC_SMC_PARAMS = SMCParams(
    swing_length_htf=3,      # Shorter lookback — faster price action
    swing_length_ltf=2,      # Very tight LTF structure detection
    ob_impulse_min_ratio=1.5, # Lower threshold — synthetics create smaller impulses
    liq_sweep_min_pips=10,   # In points (not pips) for synthetics — adjust to symbol
    fvg_min_gap_pips=5,      # 5 points minimum gap
    session_filter_enabled=False,   # 24/7 trading
    news_filter_enabled=False,      # No news events affect synthetics
    kill_zone_london_start=7,       # Optional preferred hours (not mandatory)
    kill_zone_ny_start=12,
)
```

### 6.3 Gold (XAUUSD) — Special Handling

Gold requires wider stops due to its volatility, but the compounding plan works identically:

```python
GOLD_SMC_PARAMS = SMCParams(
    swing_length_htf=5,
    swing_length_ltf=3,
    ob_impulse_min_ratio=2.0,
    liq_sweep_min_pips=30,   # Gold moves in larger pip ranges
    fvg_min_gap_pips=20,     # Larger minimum FVG
    sl_buffer_pips=10,        # Wider buffer for gold volatility
    session_filter_enabled=True,
    news_filter_enabled=True,  # Gold very sensitive to USD news
)

GOLD_RISK_PARAMS = RiskParams(
    sl_method="SWING_POINT",   # Wider SL for gold
    sl_buffer_pips=10,
    atr_trail_multiplier=2.0,  # Wider trail for gold volatility
    max_spread_pips=5.0,       # Gold spreads can widen significantly
)
```

---

## 7. Frontend — Compounding Plan UI

### 7.1 Compounding Settings Panel

```
┌─── COMPOUNDING PLAN ──────────────────────────────────────────────────────┐
│                                                                             │
│  ◉ Enable Compounding Plan    ○ Disable (use % risk)                        │
│                                                                             │
│  PLAN SELECTION                                                             │
│  ◉ Default: 1:3 RR Plan (18 steps, $20 → $11,120)                          │
│  ○ Custom plan  [Upload / Create custom table]                              │
│                                                                             │
│  ADVANCE MODE                                                               │
│  ◉ Auto (advance when balance crosses next threshold)                       │
│  ○ Conservative (require [2] consecutive wins before advancing)             │
│  ○ Manual (I control when to advance/retreat)                               │
│                                                                             │
│  DOWNGRADE MODE                                                             │
│  ◉ Auto (step back when balance drops below lower threshold)                │
│  ○ Loss Count (step back after [3] consecutive losses)                      │
│                                                                             │
│  CURRENT STATUS                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Step 7 of 18  |  Risk per trade: $100  |  Target: 1:3 RR ($300)    │  │
│  │  Account: $847.20  |  Next step at: $920  ($72.80 to go)             │  │
│  │  Consecutive wins at this level: 1 / 2                               │  │
│  │  ████████████░░░░░░░░  74% to next step                              │  │
│  │                                                                       │  │
│  │  [◀ Step Down]              [Step Up ▶]  (Manual mode only)          │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  COMPOUNDING PROGRESS CHART                                                 │
│  [Visual equity curve showing step boundaries as horizontal lines]          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Live Dashboard Compounding Widget

In the main dashboard's risk widget:

```
┌─── RISK & COMPOUNDING STATUS ─────────────────────────────────┐
│  Account:     $847.20     Daily P&L:   +$100.00  🟢           │
│  Step:        7 / 18      Risk/trade:  $100                   │
│  Next step:   $920.00     Gap:         $72.80                 │
│  Wins today:  2           Streak:      1W 0L                  │
│  ████████████░░░░ 74% to Step 8                               │
└───────────────────────────────────────────────────────────────┘
```

### 7.3 Step Transition Notifications

```
🎯 Step Advanced! You've reached Step 8.
New risk per trade: $100 → $100 (same tier, 2nd trade)

🚀 Step Advanced! You've reached Step 9!
New risk per trade: $100 → $200. Account: $1,243.00
Next milestone: $1,820

⚠️ Step Reduced. Account dropped to $580 (below Step 7 threshold of $620).
Risk reduced: $100 → $50. Protecting your capital.
```

---

## 8. Compounding Analytics

The analytics dashboard includes a dedicated Compounding tab:

- **Equity curve** with step boundaries marked as horizontal dashed lines
- **Step timeline**: bar chart showing which step was active each day
- **Step performance table**: win rate, avg RR, P&L per step
- **Advancement history**: timestamps of every step up/down
- **Projected growth**: if current win rate continues, when will each future step be reached?
- **Worst case scenario**: if max consecutive losses hit at current step, how far does account drop?

---

## 9. Compounding + Backtest Integration

When backtesting with compounding enabled:

1. Start backtest at user-defined `initial_balance`
2. Compounding engine starts at correct step for that balance
3. Every trade uses the step's `risk_amount` as the dollar risk
4. After each trade, step is updated based on new balance
5. Backtest report includes compounding analytics (step timeline, per-step stats)
6. User can compare: "Compounding ON vs OFF — same signals, which grew faster?"

This comparison is a key tool for validating whether the compounding plan genuinely accelerates growth vs simply increasing risk.

---

*Version 1.0 | AlgoEdge Compounding Plan Specification | June 2026*
