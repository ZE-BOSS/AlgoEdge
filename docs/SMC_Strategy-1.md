# SMC_Strategy.md
## Smart Money Concepts — Full Algorithmic Strategy Specification
### Including Sniper Entry Model + Candlestick Confirmation Bible

> **Source basis:** *Mind of the Market — The Smart Money Blueprint* (Pamela Donald), ICT/SMC research corpus, Candlestick pattern literature, and SMC entry model research (2024–2026).
> This document is the authoritative strategy reference for the AlgoEdge trading bot SMC engine.

---

## Table of Contents

1. [SMC Philosophy — What the Bot Must Understand](#1-smc-philosophy)
2. [The Full SMC Concept Map](#2-full-smc-concept-map)
3. [Market Structure — The Engine of SMC](#3-market-structure)
4. [Liquidity — The Fuel of Price](#4-liquidity)
5. [Order Blocks — The Footprint of Smart Money](#5-order-blocks)
6. [Fair Value Gaps (FVGs) — Imbalance Zones](#6-fair-value-gaps)
7. [Supply and Demand Zones](#7-supply-and-demand-zones)
8. [Institutional Price Delivery Model (IPDM)](#8-institutional-price-delivery-model)
9. [The Candlestick Confirmation Bible](#9-candlestick-confirmation-bible)
10. [The Sniper Entry Model — Full Step-by-Step](#10-sniper-entry-model)
11. [Trade Management Rules](#11-trade-management-rules)
12. [Session & Time Filter Rules](#12-session--time-filter-rules)
13. [Confluence Scoring System (Algorithmic)](#13-confluence-scoring-system)
14. [Signal Validation Checklist (Code Gates)](#14-signal-validation-checklist)
15. [Algorithmic Detection Rules — Code-Ready Specs](#15-algorithmic-detection-rules)

---

## 1. SMC Philosophy

Smart Money refers to institutional capital — banks, hedge funds, and large financial entities — that **engineers price movement** to fill large orders, trap retail positions, and profit from the mass behavior of retail traders.

**The core insight:** Price is NOT random. It is driven by liquidity. Smart Money needs liquidity to fill their positions. They manufacture moves to collect that liquidity before driving price to their real target.

### The Three Laws the Bot Operates By

**Law 1 — Liquidity is the magnet.** Price always moves toward liquidity. Every swing high is a pool of buy-stop orders. Every swing low is a pool of sell-stop orders. Smart Money sweeps these pools before reversing.

**Law 2 — OBs and FVGs are institutional footprints.** When price moves impulsively and creates a BOS/ChoCH, it leaves behind OBs (where the orders were placed) and FVGs (gaps from moving too fast). Price will return to these zones.

**Law 3 — Structure reveals intent.** The sequence of BOS and ChoCH on multiple timeframes tells you exactly what Smart Money is doing. A bullish BOS confirms trend continuation. A ChoCH warns of trend reversal. Always read from the top timeframe down.

### Retail vs Smart Money — The Bot Takes the SM Side

| Behavior | Retail (What to Avoid) | Smart Money (What to Follow) |
|----------|------------------------|------------------------------|
| Entries | Buy breakouts, sell breakdowns | Buy after liquidity sweeps in discount |
| Stop Loss | Below "support" (stop hunt zone) | Beyond OB, protected by institutional order |
| TP Target | Vague, emotion-based | Next external liquidity pool |
| Structure | Uses indicators (RSI, MACD) | Reads BOS/ChoCH, OBs, FVGs |
| Patience | Reacts to price | Waits for price to come to them |

---

## 2. Full SMC Concept Map

```
MARKET STRUCTURE (BOS / ChoCH)
    │
    ├── Determines BIAS (Bullish or Bearish)
    │        │
    │        ▼
    │   LIQUIDITY POOLS (BSL / SSL)
    │        │ Price sweeps them → manipulation phase
    │        ▼
    │   IPDM PHASE DETECTION
    │   [Accumulation → Manipulation → Expansion]
    │
    ▼
POINT OF INTEREST (POI) IDENTIFICATION
    │
    ├── ORDER BLOCK (OB)     ← Bullish or Bearish
    ├── FAIR VALUE GAP (FVG) ← Bullish or Bearish
    ├── SUPPLY / DEMAND ZONE ← Broader institutional zone
    └── OTE ZONE             ← Fibonacci 61.8–78.6% of last leg
    
    ↓ Price returns to POI
    
SNIPER ENTRY CONFIRMATION (LTF)
    │
    ├── ChoCH on LTF (M5 or M1) — structure shift confirmed
    ├── LTF Order Block or FVG inside HTF zone
    └── CANDLESTICK CONFIRMATION PATTERN
            ├── Bullish: Engulfing, Hammer/Pin Bar, Dragonfly Doji,
            │           Rejection Wick, Morning Star, Inside Bar
            └── Bearish: Bearish Engulfing, Shooting Star/Pin Bar,
                         Gravestone Doji, Evening Star, Inside Bar
    
    ↓ Entry confirmed
    
TRADE EXECUTION
    SL: Beyond OB/FVG extreme + buffer
    TP1: 1:1 RR (partial close 50%)
    TP2: Next external liquidity / HTF target
    Trailing: SL to BE after TP1 hit
```

---

## 3. Market Structure

### 3.1 Definitions

**Swing High:** A candle whose high is higher than `swing_length` candles on both sides.
**Swing Low:** A candle whose low is lower than `swing_length` candles on both sides.
**Higher High (HH):** Current swing high > previous swing high → bullish.
**Higher Low (HL):** Current swing low > previous swing low → bullish confirmation.
**Lower High (LH):** Current swing high < previous swing high → bearish.
**Lower Low (LL):** Current swing low < previous swing low → bearish.

### 3.2 Break of Structure (BOS)

- A **Bullish BOS** occurs when price closes **above** the most recent **swing high** in an existing uptrend.
  → Confirms trend continuation. Look for longs on LTF.
- A **Bearish BOS** occurs when price closes **below** the most recent **swing low** in an existing downtrend.
  → Confirms trend continuation. Look for shorts on LTF.
- BOS = trend continuation signal.
- After BOS, expect a pullback into the last formed OB or FVG before the next leg continues.

### 3.3 Change of Character (ChoCH)

- A **Bullish ChoCH** occurs when price closes **above** the most recent **swing high** in a **downtrend** (Must break atleast two swing highs).
  → First warning that downtrend may be reversing. Wait for LTF confirmation.
- A **Bearish ChoCH** occurs when price closes **below** the most recent **swing low** in an **uptrend** (Must break atleast two swing lows).
  → First warning that uptrend may be reversing. Wait for LTF confirmation.
- ChoCH = potential reversal signal. Requires additional confirmation before entry.
- **Strong ChoCH rule (from the book):** ChoCH is most effective when price breaks through **two or more** Supply/Demand zones — not just one candle flip.

### 3.4 Three Market Phases

```
TRENDING PHASE:
  Clear BOS in one direction
  Structure: HH → HL → HH → HL (bullish)
  Structure: LH → LL → LH → LL (bearish)
  
REVERSAL PHASE:
  ChoCH signals shift in intent
  Watch for liquidity grab BEFORE ChoCH
  OB forms after ChoCH candle
  
CONSOLIDATION PHASE:
  Market moving sideways — NO TRADE ZONE
  Price accumulating/distributing
  Wait for breakout with liquidity sweep to confirm next phase
```

### 3.5 Algorithmic Market Structure Rules

```python
# Bias determination (run on HTF — H4)
BULLISH_BIAS if:
    - Last 3 structural moves: at least 2 are HH or HL
    - Most recent BOS was bullish
    - No bearish ChoCH has invalidated the structure
    
BEARISH_BIAS if:
    - Last 3 structural moves: at least 2 are LH or LL
    - Most recent BOS was bearish
    - No bullish ChoCH has invalidated the structure
    
NEUTRAL (no trade) if:
    - Mixed: HH then LL with no clear direction
    - Price in consolidation range < 50 pips (Forex majors)
    - HTF shows ChoCH but no confirming BOS yet
```

### 3.6 Macro vs Micro Structure

- **Macro (HTF: D1, H4, H1):** Sets the overall bias direction. Never trade against macro structure.
- **Micro (LTF: M15, M5, M1):** Used for entry timing and precision. A ChoCH on M5 can confirm entry in the direction of H4 BOS.
- **Rule:** A run/trend on HTF is a trend on LTF. A pullback on HTF is an entire trend on LTF. Use this perspective to find precise low-risk entries.

---

## 4. Liquidity

### 4.1 What Liquidity Is

Liquidity is where **pending orders cluster** — stop-losses, buy-stops, sell-stops, breakout orders from retail traders. Smart Money needs this pool to fill their massive institutional orders at favorable prices.

### 4.2 Types of Liquidity Pools

**Buy-Side Liquidity (BSL):**
- Equal highs (two or more highs at the same level)
- Previous swing highs
- Session highs (London open high, NY open high)
- Above resistance levels (retail breakout entries sit here)

**Sell-Side Liquidity (SSL):**
- Equal lows (two or more lows at the same level)
- Previous swing lows
- Session lows
- Below support levels (retail SLs sit here)

**Trendline Liquidity:**
- Multiple trendline touches = a pool building
- Retail traders enter on the break → SM grabs their stops

**Session Liquidity:**
- Liquidity builds during Asian session consolidation
- London and NY kill zones are where liquidity is most aggressively swept

### 4.3 Liquidity Sweep (The Manipulation Candle)

A liquidity sweep is the **manipulation phase** of IPDM. Price wicks sharply beyond a liquidity pool (equal highs/lows, swing point) and then immediately reverses. This is not random — it is intentional.

**Algorithmic detection:**
```python
LIQUIDITY_SWEEP detected when:
    For Bullish Setup (price sweeps SSL before reversing up):
        - Price wicks below identified SSL level
        - Wick penetration: at least 5 pips / 50% of candle range below SSL
        - Candle CLOSES above SSL (wick only, not body)
        - Closing above SSL = rejection confirmed
    
    For Bearish Setup (price sweeps BSL before reversing down):
        - Price wicks above identified BSL level
        - Wick penetration: at least 5 pips / 50% of candle range above BSL
        - Candle CLOSES below BSL
```

### 4.4 Inducement (IDM) — The Trap Before the Entry

Inducement is a false, shallow pullback designed to draw early buyers/sellers into the wrong position BEFORE price makes the real move to the true POI.

**Example:** In a bullish trend after a BOS:
1. Price creates a pullback (inducement)
2. Early retail traders BUY from the IDM zone
3. Price drops lower to the true OB/demand zone
4. Their stops are hit (adding to SM's liquidity pool at the real OB)
5. SM buys from the real OB — price expands up

**Code rule:** If price has NOT yet swept the inducement (IDM) zone, do NOT enter from the first OB in the pullback. Wait for the deeper move to the true OB.

### 4.5 Internal vs External Liquidity

- **Internal Liquidity (IRL):** FVGs and OBs within the current price range. Price mitigates these before targeting external liquidity.
- **External Liquidity (ERL):** Previous swing highs/lows, equal highs/lows OUTSIDE the current range. The ultimate target of each institutional move.
- **Pattern:** ERL → IRL → ERL. Price takes external liquidity, pulls back to fill internal (FVG/OB), then moves to the next external target.

---

## 5. Order Blocks

### 5.1 Definition

An Order Block is the **last opposing candle** before a **strong impulsive move** that creates a BOS or ChoCH. It represents the zone where Smart Money placed their large institutional orders.

- **Bullish OB:** Last **bearish (red) candle** before a strong bullish impulse that breaks structure.
- **Bearish OB:** Last **bullish (green) candle** before a strong bearish impulse that breaks structure.

### 5.2 OB Validity Criteria (All Must Pass)

```
✅ 1. STRUCTURAL SIGNIFICANCE
   The OB candle must have caused (or be directly before) a BOS or ChoCH.
   Not every candle qualifies — only those tied to structural breaks.

✅ 2. LIQUIDITY BACKING
   The move from the OB must have swept liquidity before or during the impulse.
   OBs without a liquidity event are lower quality.

✅ 3. IMPULSIVE MOVE SIZE
   The impulse away from the OB must be at least 2x the OB candle's size.
   Weak moves do not indicate institutional participation.

✅ 4. FRESHNESS (UNMITIGATED)
   OB must NOT have been returned to (mitigated) since formation.
   First touch = highest probability. Second touch = lower probability.
   Third touch = invalidated — DO NOT TRADE.

✅ 5. HTF ALIGNMENT
   Bullish OB only valid when HTF bias is bullish.
   Bearish OB only valid when HTF bias is bearish.
   Counter-trend OBs are lower quality and skipped in MVP.
```

### 5.3 OB Zone Boundaries

```
Bullish OB zone:
    Top:    High of the OB candle
    Bottom: Low of the OB candle
    (Some traders use Open-to-Close only; both methods valid)
    
Entry refinement:
    Aggressive: Enter at OB top (first touch)
    Conservative: Wait for 50% of OB (equilibrium of the candle)
    Sniper: Enter at OB top after LTF ChoCH confirmation
    
Stop Loss placement:
    SL = OB low - buffer (5–10 pips or 1.5× spread)
    Never SL INSIDE the OB — always BEYOND it
```

### 5.4 OB Types

**Continuation OB:** Forms within a trend. Price BOS → pulls back to OB → continues the trend. Higher probability in trending markets.

**Reversal OB:** Forms at the end of a trend. ChoCH occurs at or after the OB. Lower probability — needs extra confirmation (multiple timeframe confluence, strong liquidity sweep).

**Mitigation Block:** An OB that has been partially mitigated (price touched the top 30–40% of the zone). Can still be valid for entries at the remaining untested portion.

### 5.5 Algorithmic OB Detection Logic

```python
def detect_order_block(candles, bias, swing_length=5):
    """
    Finds valid Order Blocks given the market bias.
    Returns list of OB zones with quality score.
    """
    obs = []
    swings = detect_swing_highs_lows(candles, swing_length)
    bos_events = detect_bos_choch(candles, swings)
    
    for bos in bos_events:
        if bos.direction != bias:
            continue  # only OBs that align with bias
        
        # Walk back from BOS candle to find last opposing candle
        bos_idx = bos.candle_index
        impulse_start = bos_idx
        
        for i in range(bos_idx - 1, max(0, bos_idx - 20), -1):
            candle = candles[i]
            
            if bias == "BULLISH" and candle.is_bearish():
                # Last bearish candle before bullish impulse = Bullish OB
                ob = OrderBlock(
                    direction="BULLISH",
                    top=candle.high,
                    bottom=candle.low,
                    open=candle.open,
                    close=candle.close,
                    formation_time=candle.timestamp,
                    is_mitigated=False,
                    impulse_size=calc_impulse_size(candles, i, bos_idx),
                    has_fvg=check_fvg_in_impulse(candles, i, bos_idx),
                    swept_liquidity=bos.swept_liquidity,
                )
                if ob.impulse_size >= 2.0:  # quality filter
                    obs.append(ob)
                break
                
            elif bias == "BEARISH" and candle.is_bullish():
                ob = OrderBlock(
                    direction="BEARISH",
                    top=candle.high,
                    bottom=candle.low,
                    open=candle.open,
                    close=candle.close,
                    formation_time=candle.timestamp,
                    is_mitigated=False,
                    impulse_size=calc_impulse_size(candles, i, bos_idx),
                    has_fvg=check_fvg_in_impulse(candles, i, bos_idx),
                    swept_liquidity=bos.swept_liquidity,
                )
                if ob.impulse_size >= 2.0:
                    obs.append(ob)
                break
    
    return obs

def check_mitigation(ob, recent_candles):
    """
    An OB is mitigated when price CLOSES inside it (not just wicks).
    Updates ob.is_mitigated = True when this occurs.
    """
    for candle in recent_candles:
        if candle.timestamp <= ob.formation_time:
            continue
        if ob.direction == "BULLISH":
            if candle.close < ob.top and candle.close > ob.bottom:
                ob.is_mitigated = True
                ob.mitigation_count += 1
                break
        else:
            if candle.close > ob.bottom and candle.close < ob.top:
                ob.is_mitigated = True
                ob.mitigation_count += 1
                break
```

---

## 6. Fair Value Gaps (FVGs)

### 6.1 Definition

An FVG is a **three-candle price imbalance** where price moves so quickly that the wick of candle 1 and the wick of candle 3 do not overlap. This creates a "gap" — an area not properly traded by both buyers and sellers.

```
Bullish FVG (gap below price after a bullish impulse):
    Candle[i-1].high  <  Candle[i+1].low
    FVG zone: from Candle[i-1].high to Candle[i+1].low
    
Bearish FVG (gap above price after a bearish impulse):
    Candle[i-1].low  >  Candle[i+1].high
    FVG zone: from Candle[i+1].high to Candle[i-1].low
```

### 6.2 Why FVGs Are High Probability

FVGs act as **magnets** because:
1. Market makers must fill the imbalance to create fair pricing
2. Unfilled orders remain in these gaps
3. Price almost always returns to fill (or partially fill) FVGs before continuing

### 6.3 FVG Entry Refinement

```
Entry within FVG:
    Aggressive: Enter at FVG edge (first entry into gap)
    Standard:   Enter at 50% of FVG (equilibrium / "CE" - Consequent Encroachment)
    Conservative: Enter at far edge of FVG
    
Stop Loss: Just beyond the far edge of FVG (5–10 pip buffer)
Take Profit: Next swing high/low or opposing liquidity pool
```

### 6.4 OB + FVG Confluence = Highest Probability Setup

When a **Fair Value Gap exists INSIDE an Order Block zone**, this creates the highest probability entry in all of SMC. The interpretation:

- **OB = Intention** (where SM placed their orders)
- **FVG = Urgency** (they moved price so fast they left a gap)
- **Confluence = SM will defend this zone aggressively**

**Code priority:** Always prefer `OB_FVG_CONFLUENCE` setups over standalone OB or standalone FVG signals. These get the highest confidence score.

### 6.5 FVG Types

- **Continuation FVG:** Mid-trend. Price retraces to fill partially, then continues.
- **Reversal FVG:** Forms after ChoCH. Price fills it, then reverses.
- **Breakaway FVG:** Very large gap — may not be fully filled. Enter on the near edge only.

---

## 7. Supply and Demand Zones

### 7.1 Relationship to OBs

Supply/Demand zones are the **broader context** within which OBs sit:
- **Demand Zone** = area where strong buying overwhelmed selling → caused price to rally
- **Supply Zone** = area where strong selling overwhelmed buying → caused price to drop

OBs are the **precision entry tool** within supply/demand zones. Supply/demand zones frame which OBs are most significant.

### 7.2 Valid Zone Criteria

```
DEMAND ZONE valid if:
  ✅ Strong impulsive move away from the area (DBD or DBR pattern)
  ✅ Small consolidation or base before the impulse (tight candles)
  ✅ A BOS or ChoCH was created by the move
  ✅ Liquidity was taken before or during the impulse
  ✅ Zone is FRESH (untested since formation)

SUPPLY ZONE valid if:
  ✅ Same conditions but in reverse (RBD or RBR pattern)
```

### 7.3 Zone Patterns (Drop-Base-Rally / Rally-Base-Drop)

```
Demand (DBR — Drop Base Rally):
  Price drops aggressively
  Forms a tight consolidation BASE
  Rallies strongly (BOS)
  → High probability demand zone

Demand (DBD — continuation):
  Price drops, brief base, drops again
  → Continuation demand (lower quality, skip)

Supply (RBD — Rally Base Drop):
  Price rallies aggressively
  Forms a tight consolidation base
  Drops strongly (BOS)
  → High probability supply zone
```

### 7.4 Zone Confluence Checklist (All 4 Required for A+ Trade)

```
[ ] Zone is preceded by a liquidity grab (sweep of BSL or SSL)
[ ] Zone caused a BOS or ChoCH
[ ] There is an OB or FVG INSIDE the zone
[ ] Zone is aligned with HTF bias
```

---

## 8. Institutional Price Delivery Model (IPDM)

### 8.1 The Three Phases

Every institutional price move follows a predictable three-phase model:

**Phase 1 — Accumulation/Distribution:**
- Sideways, choppy price action
- Retail traders: confused, whipsawed
- SM action: building positions quietly
- Algorithmic detection: ATR below 30-day average, price range < 50% of average range

**Phase 2 — Manipulation:**
- Sharp spike through a liquidity level (equal highs/lows, session high/low)
- Retail traders: chase the breakout, get trapped
- SM action: sweeping stops, filling their orders at better prices
- Algorithmic detection: price wicks through liquidity pool, candle closes BACK inside the range (wick rejection)
- **This is the inducement phase. DO NOT ENTER during this phase.**

**Phase 3 — Expansion/Delivery:**
- Real directional move begins
- Retail traders: wrong direction, stop-hunted
- SM action: delivering price to the next liquidity target
- Algorithmic detection: confirmed BOS/ChoCH AFTER liquidity sweep, price momentum above average

### 8.2 Algorithmic Phase Detection

```python
def detect_ipdm_phase(candles, swing_highs, swing_lows, atr_lookback=20):
    current_atr = calculate_atr(candles, 14)
    avg_atr = calculate_atr(candles, atr_lookback)
    
    recent_range = candles[-20:]['high'].max() - candles[-20:]['low'].min()
    avg_range = average_range(candles, 50)
    
    # Accumulation: Low volatility, tight range
    if current_atr < avg_atr * 0.7 and recent_range < avg_range * 0.5:
        return "ACCUMULATION"
    
    # Manipulation: Spike through liquidity with rejection
    last_candle = candles[-1]
    if (has_liquidity_sweep(last_candle, swing_highs, swing_lows) and
        has_wick_rejection(last_candle, min_wick_ratio=0.6)):
        return "MANIPULATION"
    
    # Expansion: Strong momentum after confirmed structure break
    if (current_atr > avg_atr * 1.2 and
        recent_bos_confirmed(candles, swing_highs, swing_lows)):
        return "EXPANSION"
    
    return "UNKNOWN"
```

### 8.3 Power of 3 (PO3) — Daily Application

Every trading day follows the three-phase model in microcosm:
1. **Asian Session (Accumulation):** Tight range forms. This range DEFINES the day's liquidity.
2. **London Session Open (Manipulation):** Price sweeps above or below the Asian range. This is the trap. Equal highs or Asian high gets swept → this is fake. DO NOT TRADE the initial London breakout.
3. **London Kill Zone / NY Session (Expansion):** After the sweep and ChoCH, the real move begins. This is the entry window.

---

## 9. Candlestick Confirmation Bible

The following patterns are used as the **final confirmation layer** before trade entry. They are checked on the **LTF (M5 or M1)** after price has entered the POI (OB/FVG zone).

A trade signal is only converted to an executed trade after **at least one of these patterns** appears at the entry zone.

### 9.1 Tier 1 — Highest Reliability (Primary Signals)

---

#### 🟢 BULLISH ENGULFING
**What it is:** A large bullish candle whose BODY completely engulfs the body of the previous bearish candle.

**What it means:** Buyers completely overwhelmed sellers. Institutional buying overwhelmed the previous selling pressure. Volume surge confirms institutional presence.

**In SMC context:** Appears inside a bullish OB or FVG after price returns. The engulfing is SM defending their order block.

**Algorithmic detection:**
```python
def is_bullish_engulfing(candle, prev_candle):
    return (
        candle.is_bullish() and
        prev_candle.is_bearish() and
        candle.open <= prev_candle.close and  # opens at or below prev close
        candle.close >= prev_candle.open and  # closes at or above prev open
        candle.body_size() >= prev_candle.body_size() * 1.0  # equal or larger
    )
```

**Entry rule:** Enter at CLOSE of the engulfing candle. SL below the engulfing candle's low.

---

#### 🔴 BEARISH ENGULFING
**What it is:** A large bearish candle whose body completely engulfs the previous bullish candle.

**What it means:** Sellers completely overwhelmed buyers. Institutional selling overwhelmed the previous buying pressure.

**In SMC context:** Appears inside a bearish OB or FVG. SM selling after inducing buyers into the zone.

**Entry rule:** Enter at CLOSE of the bearish engulfing candle. SL above the candle's high.

---

#### 🟢 HAMMER / BULLISH PIN BAR
**What it is:** Small real body at the TOP of the candle with a long lower wick (wick ≥ 2× body size). Color of the body is irrelevant.

**What it means:** Price pushed strongly downward (sweeping SSL), but buyers rejected the lows and closed near the open. Classic liquidity sweep candle.

**In SMC context:** Often IS the manipulation candle itself — price sweeps the OB zone and immediately rejects. The hammer's wick IS the liquidity sweep. This is a very high probability entry signal.

**Valid only when:** Hammer appears AT or BELOW the OB/FVG zone in a bullish setup.

**Algorithmic detection:**
```python
def is_hammer(candle, min_wick_ratio=2.0):
    lower_wick = candle.open - candle.low if candle.is_bullish() else candle.close - candle.low
    upper_wick = candle.high - candle.close if candle.is_bullish() else candle.high - candle.open
    body = candle.body_size()
    return (
        lower_wick >= body * min_wick_ratio and  # long lower wick
        upper_wick <= body * 0.5 and             # minimal upper wick
        body > 0                                  # has a real body
    )
```

**Entry rule:** Enter at OPEN of the next candle OR at the candle's close if it closes strongly. SL below the hammer's low.

---

#### 🔴 SHOOTING STAR / BEARISH PIN BAR
**What it is:** Small real body at the BOTTOM of the candle with a long upper wick (wick ≥ 2× body size).

**What it means:** Price pushed strongly upward (sweeping BSL), but sellers rejected the highs and closed near the open.

**In SMC context:** The shooting star's wick IS the BSL sweep. Appears at or above a bearish OB/FVG.

**Entry rule:** Enter at OPEN of next candle OR at the candle's close. SL above the shooting star's high.

---

### 9.2 Tier 2 — High Reliability (Strong Confirmation)

---

#### DRAGONFLY DOJI (Bullish)
**What it is:** Open ≈ Close ≈ High. Long lower wick. Almost no upper wick.

**What it means:** Sellers pushed price down aggressively, buyers completely rejected and closed back at the open. Indecision resolved in buyers' favor.

**In SMC context:** Appears at demand zones and bullish OBs. Strong confirmation when the long wick sweeps SSL.

**Detection:**
```python
def is_dragonfly_doji(candle, tolerance=0.001):
    body_pct = candle.body_size() / (candle.high - candle.low)
    upper_wick = (candle.high - max(candle.open, candle.close)) / (candle.high - candle.low)
    lower_wick = (min(candle.open, candle.close) - candle.low) / (candle.high - candle.low)
    return body_pct < 0.1 and upper_wick < 0.1 and lower_wick > 0.6
```

---

#### GRAVESTONE DOJI (Bearish)
**What it is:** Open ≈ Close ≈ Low. Long upper wick. Almost no lower wick.

**What it means:** Buyers pushed price up aggressively, sellers completely rejected and closed back at the open. Indecision resolved in sellers' favor.

**In SMC context:** Appears at supply zones and bearish OBs. The long wick sweeps BSL.

---

#### MORNING STAR (Bullish — 3 Candle)
**What it is:** Three candle pattern:
1. Large bearish candle (selling pressure)
2. Small body candle / Doji (indecision, the "star")
3. Large bullish candle closing above 50% of candle 1

**What it means:** Selling pressure exhausted (candle 1), indecision (candle 2), buyers take over (candle 3).

**In SMC context:** The small star candle often sits RIGHT AT the OB or FVG zone. The third candle IS the ChoCH confirmation. This is a complete IPDM expansion signal.

**Detection:**
```python
def is_morning_star(c1, c2, c3):
    return (
        c1.is_bearish() and c1.body_size() > avg_body * 1.2 and  # strong bear
        c2.body_size() < avg_body * 0.5 and                       # small star
        c3.is_bullish() and                                        # bullish
        c3.close > (c1.open + c1.close) / 2                       # closes above 50% of c1
    )
```

---

#### EVENING STAR (Bearish — 3 Candle)
**What it is:** Opposite of morning star:
1. Large bullish candle
2. Small body / Doji (indecision)
3. Large bearish candle closing below 50% of candle 1

**In SMC context:** The star forms at the BSL sweep. The third candle is the confirmed ChoCH.

---

### 9.3 Tier 3 — Good Confirmation (Secondary Signals)

---

#### INSIDE BAR (Continuation or Reversal)
**What it is:** Candle completely inside the previous candle's high/low range.

**What it means:** Market pausing, accumulating before the next move.

**In SMC context:** Inside bar within a POI zone = SM absorbing remaining orders before the push. A bullish breakout of the inside bar (close above the mother candle high) = continuation entry.

**Entry rule:** Enter when the inside bar BREAKS in the direction of bias. SL on the other side of the mother candle.

---

#### REJECTION WICK (Non-Doji)
**What it is:** Any candle with a wick that is 2× or more the body size, indicating rejection at a key level.

**What it means:** Price tested a zone and was rejected. Not as clean as a pin bar but still valid at POIs.

**In SMC context:** A rejection wick touching the OB top (for bullish setups) or OB bottom (for bearish setups) is valid entry confirmation.

---

#### DISPLACEMENT CANDLE (Expansion Signal)
**What it is:** A very large candle (body > 1.5× average body size) that closes strongly in one direction, creating an FVG.

**What it means:** Institutional participation. This IS the BOS/ChoCH candle. It signals the expansion phase has begun.

**In SMC context:** The displacement candle is what creates the OB and FVG. After price returns to the OB/FVG zone, you're looking for the REACTION to these zones — not another displacement candle.

---

### 9.4 Candlestick Patterns to REJECT (In SMC Context)

The following patterns are **not used** as SMC entry confirmation signals:

| Pattern | Why Rejected in SMC |
|---------|---------------------|
| Standard Doji (equal wicks) | Pure indecision with no directional bias — SM hasn't shown their hand yet |
| Spinning Top | Indecision pattern, too ambiguous |
| Small inside bars in consolidation | Need to be in context of POI, not random consolidation |
| Tweezer Tops/Bottoms | Unreliable without structural context |

---

### 9.5 Volume Confirmation (When Available)

- **For all Tier 1 and Tier 2 patterns:** If tick volume data is available from MT5, confirm that the confirmation candle's volume is **≥ 1.5× the 20-bar average volume**. High volume = institutional participation confirmed.
- Low volume confirmation candles are given a reduced confidence score.

---

## 10. Sniper Entry Model — Full Step-by-Step

This is the complete algorithmic entry sequence. Every trade must pass through all six steps.

### STEP 1 — HTF Bias Determination (H4 or D1)

```
1. Load H4 OHLCV data (300 candles)
2. Detect swing highs and lows (swing_length = 5)
3. Detect all BOS and ChoCH events
4. Apply bias rules:
   - BULLISH if: most recent significant structural move = bullish BOS
   - BEARISH if: most recent significant structural move = bearish BOS
   - NEUTRAL if: ChoCH occurred but no confirming BOS yet
5. Cache bias result per symbol — update only on H4 bar close
6. ABORT if NEUTRAL — no trades taken in neutral market
```

### STEP 2 — HTF Liquidity Map (H4 + H1)

```
1. Identify all active liquidity pools on H4:
   - Equal highs (within 10 pips tolerance) = BSL
   - Equal lows (within 10 pips tolerance) = SSL
   - Previous session highs/lows
   - Last 3 swing highs (for bullish setups → SSL are potential sweep targets)
   - Last 3 swing lows (for bearish setups → BSL are potential sweep targets)
   
2. Determine which liquidity pool is the "next target":
   - Bullish bias: price will sweep nearest SSL BEFORE rising to BSL target
   - Bearish bias: price will sweep nearest BSL BEFORE falling to SSL target
   
3. This gives us the DIRECTION of manipulation to watch for
```

### STEP 3 — Point of Interest Identification (H1)

```
1. Find all valid Order Blocks on H1 (using OB detection rules from Section 5)
2. Find all valid FVGs on H1
3. Find Supply/Demand zones on H1 using zone criteria (Section 7)
4. Score each POI using the Confluence Scoring System (Section 13)
5. Select the highest-scoring POI that:
   a. Is in the discount zone (for bullish) or premium zone (for bearish)
   b. Is unmitigated (first touch only for A+ setups)
   c. Is aligned with H4 bias
6. Set price alerts at the POI top (bullish) or bottom (bearish)
```

**Premium/Discount Zones:**
```
Take the range of the last significant swing (Low X → High Y for bullish)
Equilibrium = 50% of that range
Discount = price below 50% (for bullish entries — buying at "discount")
Premium = price above 50% (for bearish entries — selling at "premium")

Optimal Trade Entry (OTE) zone = 61.8% to 78.6% Fibonacci retracement
Best entries happen in the OTE zone within the discount (bullish) or premium (bearish)
```

### STEP 4 — Liquidity Sweep Detection (H1 / M15)

```
Monitor live price feed for:
1. Price approaching the identified liquidity pool
2. Wick penetration of the SSL/BSL by at least 5 pips
3. Candle CLOSING BACK inside the range (wick only = manipulation)

When detected:
   → ALERT: "Liquidity sweep confirmed at [SSL/BSL level]"
   → Shift attention to POI zone — price should retrace here next
   → Start M15 monitoring mode for LTF entry confirmation
```

### STEP 5 — LTF Entry Confirmation (M15 → M5)

```
After liquidity sweep confirmed:

1. Drop to M15 / M5 chart
2. Watch for LTF ChoCH (structure shift in the direction of bias):
   - After bearish liquidity sweep: look for bullish ChoCH on M5
   - After bullish liquidity sweep: look for bearish ChoCH on M5

3. After LTF ChoCH confirmed:
   - Identify the LTF OB that CAUSED the ChoCH
   - Identify any FVG left by the ChoCH displacement candle
   - This LTF OB/FVG is the PRECISION ENTRY ZONE

4. Wait for price to return to the LTF OB/FVG

5. Monitor for CANDLESTICK CONFIRMATION (Section 9):
   - Tier 1 preferred: Bullish/Bearish Engulfing, Hammer/Pin Bar
   - Tier 2 acceptable: Morning/Evening Star, Dragonfly/Gravestone Doji
   - Tier 3 acceptable with higher confluence score required
```

### STEP 6 — Trade Execution Parameters

```
ENTRY:
   Limit order at LTF OB top (bullish) or OB bottom (bearish)
   OR market entry on candlestick confirmation candle close
   
STOP LOSS:
   Bullish: Low of the manipulation candle (SSL sweep) - 5 pip buffer
   Alternative: Low of the LTF OB - 5 pip buffer
   Take the WIDER of the two
   
TAKE PROFIT 1 (TP1):
   Distance = entry to SL × 1.0 (1:1 RR)
   Close 50% of position at TP1
   
TAKE PROFIT 2 (TP2):
   Next significant liquidity pool (BSL for bullish / SSL for bearish)
   Minimum 1:2 RR — reject trade if TP2 cannot reach 1:2
   
TRAILING STOP (after TP1 hit):
   Move SL to breakeven (entry price)
   Trail SL to below each new higher low formed (bullish)
   Trail SL to above each new lower high formed (bearish)
   
MAGIC NUMBER: unique per user (user1=1001, user2=1002) for MT5 tracking
```

---

## 11. Trade Management Rules

### 11.1 Stop Loss Rules

- SL is set on entry and is **HARD** — it does NOT move to a tighter position unless the trade is in profit
- SL moving to BE happens only after price hits TP1 (1:1 RR)
- After TP1, SL trails to the most recent opposing swing point on M15
- **NEVER widen the SL.** If the setup requires a wider SL than the risk rules allow (lot size < minimum), skip the trade

### 11.2 Take Profit Rules

- **TP1 (1:1 R):** Close 50% of position. Book partial profit.
- **TP2 (2:1 R minimum):** Target is next external liquidity pool.
- If price reaches an HTF supply/demand zone before TP2, close the remaining 50%.
- **Trailing after TP1:** Trail by swings, not by pips. Trail only after a new higher low (bullish) or lower high (bearish) is confirmed with a closed M15 candle.

### 11.3 Trade Duration Limits

- If the trade has been open for **3 sessions** (e.g., 3 London sessions = ~72 hours) without hitting TP1, the setup is considered stale. Close at market on the next London open.
- Before weekends: Close all open positions at **23:00 GMT Friday** to avoid gap risk.

### 11.4 Session-Based Position Management

- During news events (±30 minutes): halt trailing and do not adjust positions.
- If price approaches within 20 pips of TP2 and a major news event is in 15 minutes: take profit manually.

---

## 12. Session & Time Filter Rules

### 12.0 Synthetic Indices — Override All Session Rules

Synthetic indices (V75, V25, V50, V100, Boom, Crash, Step) trade **24/7** and are unaffected by economic news. For these symbols:
- Session filter is **automatically disabled** (no London/NY restriction)
- News filter is **automatically disabled** (economic calendar irrelevant)
- The bot runs continuously, evaluating every M15 bar close around the clock
- SMC patterns (OBs, FVGs, BOS, ChoCH) are fully applicable — algorithmic price generation creates consistent, repeatable structure

This is detected via `InstrumentProfile.trades_24_7` and `InstrumentProfile.news_filter` flags. No manual configuration needed.

### 12.1 Active Trading Windows (GMT Times) — Forex, Gold, Indices Only

| Session | Window | Kill Zone (Highest Priority) | Notes |
|---------|--------|------------------------------|-------|
| London Open | 07:00–10:00 | 07:00–08:30 | Best setups here — SM sweeps Asian lows/highs |
| London Mid | 10:00–12:00 | — | Secondary window |
| NY Open | 12:00–15:00 | 12:00–13:30 | Best NY setups — overlap with London |
| London/NY Overlap | 12:00–15:00 | 12:00–13:30 | Highest volume, highest probability |
| NY Afternoon | 15:00–17:00 | — | Tertiary window only |

### 12.2 BLOCKED Time Windows (No New Entries)

```
❌ Asian Session: 22:00–06:00 GMT
   (Low liquidity, choppy, most moves are traps)
   
❌ Pre-London: 06:00–07:00 GMT
   (Accumulation building — wait for London sweep first)
   
❌ Friday 20:00 GMT onwards
   (Liquidity drying up, gap risk building)
   
❌ Sunday 21:00–22:00 GMT
   (Market opens with gap risk from weekend events)
   
❌ ±30 minutes around NFP/FOMC/CPI
   (Extreme volatility, spreads widen dramatically)
   
❌ Major holiday sessions
   (Market half-liquidity, spreads unreliable)
```

### 12.3 News Filter Implementation

```python
def is_news_blocked(current_time: datetime, news_events: list) -> bool:
    """
    Returns True if trading is blocked due to upcoming/recent news.
    news_events: list of {"time": datetime, "impact": "HIGH/MED/LOW"}
    """
    for event in news_events:
        if event["impact"] != "HIGH":
            continue
        delta = abs((event["time"] - current_time).total_seconds() / 60)
        if delta <= 30:  # 30 minutes before and after
            return True
    return False
```

Economic calendar sources (free):
- ForexFactory RSS feed: `https://www.forexfactory.com/ff_calendar_thisweek.xml`
- Myfxbook calendar API
- Investing.com economic calendar

---

## 13. Confluence Scoring System

Every trade signal receives a score from 0–100. Only signals scoring **≥ 65** are executed.

| Factor | Points | Notes |
|--------|--------|-------|
| HTF bias confirmed (H4) | +15 | Mandatory — 0 if not confirmed |
| H1 structure aligns with H4 | +10 | Multi-timeframe alignment |
| Liquidity sweep confirmed | +15 | Essential — 0 if no sweep |
| Fresh OB (first touch) | +15 | 2nd touch: +8, 3rd touch: 0 |
| FVG inside OB (confluence) | +10 | Both OB and FVG in same zone |
| OTE zone (61.8–78.6%) | +5 | Fibonacci optimal entry zone |
| Tier 1 candlestick confirmation | +15 | Engulfing or Pin Bar |
| Tier 2 candlestick confirmation | +10 | Doji, Morning/Evening Star |
| Tier 3 candlestick confirmation | +5 | Rejection wick, inside bar |
| LTF ChoCH confirmed | +10 | M5 or M1 structure shift |
| Active kill zone session | +5 | London or NY kill zone |
| Volume spike ≥ 1.5× avg | +5 | When volume data available |
| Supply/Demand zone alignment | +5 | OB inside broader S/D zone |
| **TOTAL MAXIMUM** | **110** | Scores above 100 still capped at 100 |

```
Score < 65:  Signal REJECTED — no trade
Score 65-74: Signal ACCEPTED — minimum position size (0.75× standard)
Score 75-84: Signal ACCEPTED — standard position size (1.0× standard)
Score 85+:   Signal ACCEPTED — full size (1.0× standard, pursue TP2 aggressively)
```

---

## 14. Signal Validation Checklist

Before any trade is executed by the bot, ALL of the following gates must pass:

```python
class TradeGate:
    """All checks must return True. Any False = trade rejected."""
    
    def htf_bias_confirmed(self) -> bool:
        """H4 must show clear bullish or bearish structure."""
    
    def liquidity_swept(self) -> bool:
        """A liquidity pool must have been swept in last 5 M15 candles."""
    
    def price_in_poi(self) -> bool:
        """Current price must be at or inside the identified OB/FVG zone."""
    
    def ob_is_fresh(self) -> bool:
        """Order block must not have been mitigated previously."""
    
    def rr_minimum_met(self) -> bool:
        """TP1 must achieve at least 3.0× RR (1:3 minimum — hard floor).
        TP2 target ≥ 5× RR, TP3 target ≥ 7× RR when multi-position mode active."""
    
    def spread_acceptable(self) -> bool:
        """Current spread must be < 2× the 20-period average spread."""
    
    def session_active(self) -> bool:
        """Current time must be within an active trading session."""
    
    def not_news_blocked(self) -> bool:
        """No HIGH-impact news within ±30 minutes."""
    
    def daily_loss_not_exceeded(self) -> bool:
        """Today's realized losses must be < max_daily_loss_pct of account."""
    
    def max_positions_not_reached(self) -> bool:
        """Current open positions < max_concurrent_positions (3)."""
    
    def candlestick_confirmed(self) -> bool:
        """At least one valid SMC candlestick pattern detected at POI."""
    
    def confluence_score_sufficient(self) -> bool:
        """Signal's confluence score >= 65."""
```

---

## 15. Algorithmic Detection Rules — Code-Ready Specs

### 15.1 Complete Signal Flow

```
Every M15 bar close triggers:

1. Update HTF data cache (H4, H1, M15)
2. Run bias detection on H4 → if NEUTRAL: stop
3. Update OB and FVG lists (check for new ones, check mitigation of old ones)
4. Update liquidity pool map
5. Check if price is approaching any POI (within 20 pips)
6. If approaching POI:
   a. Check if liquidity sweep occurred in last 5 bars
   b. If sweep detected: enter M5 monitoring mode
7. In M5 monitoring mode:
   a. Check for LTF ChoCH
   b. If ChoCH: identify LTF OB/FVG entry zone
   c. Check for candlestick confirmation pattern
   d. If confirmed: calculate confluence score
   e. If score >= 65 AND all gates pass: EXECUTE TRADE
8. Monitor open trades:
   a. Check if TP1 hit → partial close + move SL to BE
   b. Check if TP2 hit → close remaining
   c. Update trailing SL
   d. Check if SL hit → record trade outcome + generate exit snapshot
```

### 15.2 Key Parameters (Tunable for Backtesting Optimization)

```python
SMC_PARAMS = {
    # Structure Detection
    "swing_length_htf": 5,          # H4 swing detection lookback
    "swing_length_ltf": 3,          # M15 swing detection lookback
    
    # OB Settings
    "ob_impulse_min_ratio": 2.0,    # OB's impulse must be 2× OB size
    "ob_max_touch_count": 1,        # Only trade fresh OBs (1st touch)
    "ob_buffer_pips": 5,            # Buffer beyond OB for SL placement
    
    # FVG Settings
    "fvg_min_gap_pips": 3,          # Minimum gap size to count as FVG
    "fvg_entry_level": 0.5,         # Enter at 50% of FVG (CE level)
    
    # Liquidity Settings
    "liq_sweep_min_pips": 5,        # Minimum wick beyond liquidity level
    "equal_highs_tolerance_pips": 10, # Tolerance for "equal" highs/lows
    
    # Risk Settings
    "risk_per_trade_pct": 1.0,      # % of account per trade
    "min_rr": 3.0,                  # Minimum risk:reward (1:3 floor — all signals below this rejected)
    "tp1_rr": 1.0,                  # TP1 at 1:1 RR
    "max_spread_multiplier": 2.0,   # Max spread vs average spread
    "max_concurrent_positions": 3,  # Max open positions
    "max_daily_loss_pct": 5.0,      # Daily loss limit % of account
    
    # Session Settings
    "session_filter": True,         # Enable session filter
    "news_filter": True,            # Enable news filter
    "news_buffer_minutes": 30,      # Minutes around news to block
    "close_friday_hour_gmt": 20,    # Friday closing hour (GMT)
    
    # Candlestick Settings
    "candle_wick_min_ratio": 2.0,   # Min wick/body ratio for pin bar
    "engulfing_min_size_ratio": 1.0, # Engulfing candle must be >= prev
    
    # Scoring Thresholds
    "min_signal_score": 65,         # Min confluence score to trade
    "full_size_score": 85,          # Score needed for full position
    
    # OTE Zone (Fibonacci)
    "ote_entry_min_fib": 0.618,     # 61.8% retracement
    "ote_entry_max_fib": 0.786,     # 78.6% retracement
}
```

### 15.3 Top Mistakes to Avoid (Algorithmic Safeguards)

Based on the SMC book's "Common Mistakes" chapter and research:

| Mistake | Safeguard |
|---------|-----------|
| Chasing price (FOMO) | Strict POI zone limit orders only — market orders only on candle close confirmation |
| Trading mitigated OBs | `ob.mitigation_count > 0` → reject (or reduce score by 15 points) |
| Ignoring HTF bias | HTF bias gate is mandatory — 0 tolerance |
| Overtrading | Max 3 positions + max 2 trades/session per user |
| Trading Asian session | `session_active()` gate blocks all non-London/NY entries |
| Trading the manipulation (chasing spike) | Only enter AFTER liquidity sweep is complete + ChoCH confirmed |
| Wrong SL placement | SL always beyond OB extreme, never inside the zone |
| Ignoring inducement | IDM detection added to OB quality check |

---

## Appendix A — Quick Reference: Trade Decision Tree

```
Price bar closes (M15)
        │
        ▼
Is HTF (H4) bias clear? ──────NO──────► SKIP
        │ YES
        ▼
Is current price near a POI? ─NO──────► SKIP
        │ YES
        ▼
Did liquidity sweep occur? ───NO──────► SKIP (wait)
        │ YES
        ▼
Is there LTF ChoCH? ──────────NO──────► SKIP (wait)
        │ YES
        ▼
Is there a candlestick confirmation? ─NO► SKIP
        │ YES
        ▼
Confluence score ≥ 65? ───────NO──────► SKIP
        │ YES
        ▼
All safety gates pass? ───────NO──────► SKIP
        │ YES
        ▼
Calculate position size (risk-based)
        │
        ▼
EXECUTE TRADE
Place SL + TP1 + TP2 as OCO orders
Generate entry chart snapshot
Log to database + broadcast via WebSocket
```

---

## Appendix B — SMC Vocabulary Reference

| Term | Definition |
|------|-----------|
| BOS | Break of Structure — confirms trend continuation |
| ChoCH | Change of Character — warns of possible reversal |
| OB | Order Block — last opposing candle before impulse |
| FVG | Fair Value Gap — 3-candle price imbalance |
| BSL | Buy-Side Liquidity — resting stops above swing highs |
| SSL | Sell-Side Liquidity — resting stops below swing lows |
| IDM | Inducement — false signal before real entry zone |
| POI | Point of Interest — OB, FVG, or S/D zone |
| IPDM | Institutional Price Delivery Model — 3-phase move |
| IRL | Internal Range Liquidity — FVGs within current range |
| ERL | External Range Liquidity — swing highs/lows outside range |
| OTE | Optimal Trade Entry — 61.8–78.6% Fib retracement |
| CE | Consequent Encroachment — 50% of FVG zone |
| PO3 | Power of 3 — Accumulation, Manipulation, Distribution |
| HTF | Higher Time Frame (H4, D1) |
| LTF | Lower Time Frame (M5, M1) |

---

## Appendix C — Definitive 4-Layer Execution Model (v2.0)

> **Supersedes:** Section 10 (Sniper Entry Model) for production execution. This appendix reflects the finalized multi-timeframe flow as confirmed during the June 2026 engine audit.

### Timeframe Assignments

| Layer | Timeframe | Purpose |
|-------|-----------|---------|
| 1 | H4 | Bias determination (HH/HL or LH/LL) |
| 2 | H1 | BOS identification + bias double-confirm + rejection zone mapping for TPs |
| IPDM | H1 | Phase filter (Accumulation/Manipulation/Expansion gate) |
| 3 | M15 | ChoCH detection (2+ swing breaks, full body close) + OB/FVG/Fib zones |
| 4 | M5 | Candlestick confirmation (reversal/continuation patterns) |

### Key Rule Updates (v2.0)

1. **ChoCH validation:** Must break past **2+ previous swing highs/lows** with a **FULL BODY candle close** — wicks do NOT count.

2. **BOS timeframe:** BOS is spotted on **H1** (not H4). H4 is used only for directional bias.

3. **H1 Bias Double-Confirm:** H1 structure must **agree** with H4 bias. If H4 says BULLISH but H1 is BEARISH → NO TRADE.

4. **Fibonacci Zones (two tiers):**
   - **PRIMARY:** 50.0%–61.8% retracement (highest priority fib zone)
   - **SECONDARY:** 61.8%–78.6% retracement (OTE zone)

5. **IPDM Phase Gate:**
   - ACCUMULATION → WAIT (SM building positions)
   - MANIPULATION → DO NOT ENTER (SM trapping retail)
   - EXPANSION → ENTRY ALLOWED (real move beginning)

6. **Zone-Based Take Profits:**
   - Scan H1 for rejection zones (previous swing highs/lows, S/D zones, strong wick areas)
   - TP1 = First H1 rejection zone (~1:1 R:R)
   - TP2 = Next H1 rejection zone (≥3:1 R:R)
   - TP3/Final = ChoCH reversal point — where market reversed after breaking 2+ swing highs/lows
   - Between entry and final TP: any H1 rejection zone can be an intermediate TP

7. **Candlestick confirmation on M5:** Mostly reversal (Engulfing, Hammer, Morning/Evening Star) or continuation patterns. Must appear AT the entry zone (OB/FVG/Fib/S&D).

### 4-Layer Flow Diagram

```
LAYER 1 (H4): Is market bullish or bearish?
    └─ HH/HL = BULLISH | LH/LL = BEARISH | Mixed = NO TRADE

LAYER 2 (H1): Does H1 agree? Are there 2+ BOS?
    └─ H1 bias must match H4
    └─ 2+ consecutive BOS confirmed → trend valid
    └─ Map rejection zones on H1 → these become TP targets
    └─ Wait for price to PULL BACK to last BOS level

IPDM GATE (H1): Is the pullback in the right phase?
    └─ Accumulation (tight range) → WAIT
    └─ Manipulation (liq sweep + wick rejection) → DON'T ENTER
    └─ Expansion (momentum after sweep) → PROCEED

LAYER 3 (M15): Has ChoCH confirmed the pullback is ending?
    └─ ChoCH = break past 2+ M15 swing highs/lows
    └─ FULL BODY CLOSE required (not wick)
    └─ Find entry: OB → FVG → Fib 50-61.8% → Fib 61.8-78.6% → S/D zone

LAYER 4 (M5): Does a candlestick pattern confirm?
    └─ Reversal or continuation pattern AT the entry zone
    └─ Score confluence ≥ 65 → EXECUTE TRADE
    └─ Place SL beyond M15 swing extreme + buffer
    └─ Place TPs at H1 rejection zones
```

### Updated Parameters (v2.0)

```python
SMC_PARAMS_V2 = {
    # Fibonacci Zones (updated)
    "fib_primary_min": 0.500,       # 50.0% retracement (primary zone start)
    "fib_primary_max": 0.618,       # 61.8% retracement (primary zone end)
    "ote_entry_min_fib": 0.618,     # 61.8% retracement (OTE start)
    "ote_entry_max_fib": 0.786,     # 78.6% retracement (OTE end)
    
    # TP defaults (updated to spec)
    "tp1_rr": 1.0,                  # TP1 at 1:1 RR (was 3.0)
    "tp2_rr": 3.0,                  # TP2 at 3:1 RR (was 5.0)
    "tp3_rr": 5.0,                  # TP3 at 5:1 RR (was 7.0)
    
    # BOS requirements
    "min_bos_count": 2,             # Minimum consecutive BOS to confirm trend
    
    # Circuit Breaker (updated to trade counts)
    "max_daily_consecutive_losses": 3,    # Was max_daily_loss_pct: 5.0
    "max_weekly_consecutive_losses": 5,   # Was max_weekly_loss_pct: 10.0
}
```

---

*Document Version 2.0 | AlgoEdge SMC Strategy Specification | June 2026*
*Based on: Mind of the Market (Pamela Donald), ICT Concepts, SMC Research 2024–2026*
*Updated: Multi-TF model finalized, IPDM gate added, ChoCH rules tightened, zone-based TPs*

