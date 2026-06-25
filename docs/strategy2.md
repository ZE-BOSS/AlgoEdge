# Definitive SMC Strategy — Step-by-Step Implementation Spec

**IMPORTANT**
This document is the authoritative source of truth for how the strategy works. Every code module must implement exactly this flow. Derived from the definitive 4-Layer Multi-Timeframe Model + IPDM Phase Filter.

## Overview: The 4-Layer Multi-Timeframe Model + IPDM Phase Filter

┌─────────────────────────────────────────────────────────────┐
│  LAYER 1: HTF BIAS (H4)                                    │
│  → Determine overall market direction (uptrend/downtrend)   │
│  → HH/HL = BULLISH bias | LH/LL = BEARISH bias            │
│  → If no clear bias → NO TRADE                              │
└────────────────────┬────────────────────────────────────────┘
                     │ Bias confirmed
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2: BOS + BIAS DOUBLE-CONFIRM (H1)                    │
│  → ALSO confirm HH/HL or LH/LL on H1 (must agree with H4) │
│  → Identify Break of Structure on H1                        │
│  → Must see at least 2 consecutive BOS in bias direction    │
│  → After BOS: wait for price to RETEST back to BOS area     │
│  → Map H1 rejection zones for TP targets                    │
└────────────────────┬────────────────────────────────────────┘
                     │ Retest in progress
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  ╔═══════════════════════════════════════════════════════╗  │
│  ║  IPDM PHASE FILTER (runs on H1 data)                 ║  │
│  ║  ACCUMULATION → tight range, low ATR    → WAIT       ║  │
│  ║  MANIPULATION → liq sweep + wick reject → DO NOT ENTER║  │
│  ║  EXPANSION    → BOS confirmed, momentum → ENTRY OK   ║  │
│  ╚═══════════════════════════════════════════════════════╝  │
│  Only proceed to Layer 3 when EXPANSION phase detected      │
│  (or when Manipulation just completed → Expansion starting) │
└────────────────────┬────────────────────────────────────────┘
                     │ Expansion phase confirmed
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3: ChoCH DETECTION (M15)                             │
│  → At retest zone, look for ChoCH on M15                    │
│  → ChoCH: price breaks past 2+ swing highs/lows            │
│    with FULL BODY CLOSE (not wick)                          │
│  → Find entry at OB / FVG / S&D zone / Fib zone            │
│  → Fib zones: 50%-61.8% (primary) + 61.8%-78.6% (secondary)│
└────────────────────┬────────────────────────────────────────┘
                     │ ChoCH confirmed
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 4: CANDLESTICK CONFIRMATION (M5)                     │
│  → Confirm entry with reversal/continuation pattern on M5   │
│  → Mostly: Engulfing, Pin Bar, Morning/Evening Star         │
│  → Score confluence ≥ 65 → EXECUTE                          │
│  → Calculate TPs from H1 rejection zones                    │
│  → Final TP = ChoCH reversal point (where market broke 2+   │
│    swing highs/lows before reversing)                       │
└─────────────────────────────────────────────────────────────┘

## STEP 1 — HTF Bias Determination (H4)
**Purpose:** Determine if we're looking for BUYS or SELLS.

1. Load H4 candles for the symbol.
2. Run MarketStructureDetector on H4 data.
3. IF last 3 structural moves show at least 2 HH or HL:
    `bias = BULLISH` → look for BUY setups only
4. IF last 3 structural moves show at least 2 LH or LL:
    `bias = BEARISH` → look for SELL setups only
5. IF mixed (HH then LL, or no clear pattern):
    `bias = NEUTRAL` → NO TRADE, skip this symbol

*Code requirement:* Load H4 data via `DataFetcher.get_historical_data(symbol, "H4", count=200)`. Run a separate MarketStructureDetector instance on H4 data.

## STEP 2 — H1 Bias Double-Confirmation + BOS Identification
**Purpose:** Double-confirm bias AND identify BOS levels for entries and TP zones.

1. Load H1 candles for the symbol.
2. Run MarketStructureDetector on H1 data.

### STEP 2A — BIAS DOUBLE-CONFIRM:
H1 structure must AGREE with H4 bias:
- H4 BULLISH → H1 must also show HH/HL structure
- H4 BEARISH → H1 must also show LH/LL structure
- If H1 disagrees with H4 → NO TRADE (conflicting structure)

### STEP 2B — BOS IDENTIFICATION:
- **BULLISH BOS:** Price CLOSES above the most recent H1 swing high → Confirms uptrend continuation
- **BEARISH BOS:** Price CLOSES below the most recent H1 swing low → Confirms downtrend continuation
- **CRITICAL RULE:** Must have at least 2 consecutive BOS in same direction.
  - First BOS after ChoCH = warning only, do NOT trade
  - Second BOS = trend CONFIRMED, now look for entries
  - Track via `consecutive_bos` counter in MarketStructureDetector

### STEP 2C — MAP H1 REJECTION ZONES:
Scan H1 data for zones where price was previously rejected:
- Previous swing highs/lows that acted as support/resistance
- Areas where price reversed sharply (strong wicks / engulfings)
- Supply/demand zones on H1
- These zones become TP TARGETS during trade execution

After BOS confirmed (2+): Wait for price to pull back / retest back toward the BOS level.

## STEP 3 — Wait for Retest + IPDM Phase Gate
**Purpose:** Price must return to the BOS zone AND the IPDM cycle must be in the right phase.

After 2+ BOS confirmed on H1:
The "retest zone" = the area around the last BOS level.
- **For BULLISH setup:** Price broke above swing highs → now wait for price to pull BACK DOWN toward the broken swing high level. This pullback on H1 = an entire mini-trend on LTF (M15).
- **For BEARISH setup:** Price broke below swing lows → now wait for price to pull BACK UP toward the broken swing low level.

### IPDM PHASE GATE (run IPDMDetector on H1 data):
The pullback/retest itself follows the IPDM cycle:
1. **ACCUMULATION** (tight range near BOS level):
   → Price is consolidating near the retest zone. ATR < 70% of 50-period average ATR. Price range < 50% of average range. DO NOT ENTER YET — SM is building positions. WAIT for manipulation phase.
2. **MANIPULATION** (liquidity sweep / fake breakout):
   → Price spikes THROUGH the BOS level (fakes a breakdown). This is the liquidity sweep — SM grabbing retail stops. Detection: wick through liquidity pool + close BACK inside range. DO NOT ENTER — this is the trap. But mark this as: manipulation completed, expect expansion.
3. **EXPANSION** (real move begins):
   → After manipulation: price reverses strongly in bias direction. ATR > 120% of baseline (momentum confirmed). BOS/ChoCH confirmed after the sweep. THIS IS THE ENTRY WINDOW — proceed to Layer 3 (M15 ChoCH).

**RULE:** Only proceed to M15 ChoCH detection when:
- IPDM phase is EXPANSION, OR
- Manipulation just completed (sweep + rejection detected)
- Never enter during ACCUMULATION or MANIPULATION

*Key insight:* "A pullback on HTF is an entire trend on LTF." The H1 pullback to BOS follows the Accumulation→Manipulation→Expansion cycle. The manipulation (liquidity sweep) IS the trap — the expansion after it IS the entry.

## STEP 4 — ChoCH Detection on M15
**Purpose:** Confirm the pullback is ending and trend will resume.

1. Drop to M15 chart at the retest zone.
2. The pullback on H1 appears as a mini-trend on M15:
   - If H1 is BULLISH: the pullback looks like a bearish trend on M15
   - If H1 is BEARISH: the pullback looks like a bullish trend on M15
3. Look for **ChoCH** on M15 — the moment the mini-trend REVERSES back in the direction of the H1 bias.

### BULLISH ChoCH (for bullish H1 setup):
Price has been making LH/LL on M15 (pullback)
→ Price breaks ABOVE 2 previous M15 swing highs
→ Break must be with FULL BODY CANDLE CLOSE (not just wick)
→ This signals: pullback is done, uptrend resuming

### BEARISH ChoCH (for bearish H1 setup):
Price has been making HH/HL on M15 (pullback)
→ Price breaks BELOW 2 previous M15 swing lows
→ Break must be with FULL BODY CANDLE CLOSE (not just wick)
→ This signals: pullback is done, downtrend resuming

**CRITICAL:** ChoCH requires breaking past 2+ swing highs/lows, NOT just a single candle flip. Full body CLOSE required (not wick).

*Code requirement:* Run MarketStructureDetector on M15 data with min_bos_count=2. Add body-close validation — ChoCH check must use candle['close'] to compare against swing levels, NOT candle['high']/candle['low'].

## STEP 5 — Entry Point Identification + M5 Candlestick Confirmation
**Purpose:** Find the precise entry after ChoCH, confirmed by M5 candle pattern.

After M15 ChoCH confirmed, identify the entry zone (in priority order). **Only ONE of the following is required:**
1. **ORDER BLOCK** (highest priority):
   → The last opposing candle before the ChoCH impulse on M15. Must be fresh (0 prior touches). Entry at the OB zone edge.
2. **FAIR VALUE GAP** (high priority):
   → 3-candle gap left by the ChoCH displacement candle. Entry at 50% of FVG (Consequent Encroachment).
3. **If neither OB nor FVG found — FALLBACK options:**
   a. **FIBONACCI ZONES** (two tiers):
      → PRIMARY: 50.0%–61.8% retracement (VERY IMPORTANT — highest priority fib)
      → SECONDARY: 61.8%–78.6% retracement (OTE zone)
      → Calculate retracement of the last M15 price leg
   b. **SUPPLY/DEMAND ZONE**:
      → Previous rejection areas (zones where price reversed before). Previous support/resistance areas from H1 mapping.

### FINAL CONFIRMATION — Drop to M5:
→ Load M5 candles at the entry zone
→ Look for REVERSAL or CONTINUATION candlestick pattern:
   - Reversal: Engulfing, Hammer/Shooting Star, Morning/Evening Star
   - Continuation: Strong momentum candle in bias direction
→ Pattern must appear AT the entry zone (OB/FVG/Fib/S&D)
→ Only execute if candlestick confirms on M5

**Entry** = market order on M5 candlestick confirmation candle close.

## STEP 6 — Stop Loss Placement
- **BULLISH setup:** SL = Below the previous swing LOW on M15 (below the low of the pullback / manipulation candle) + buffer of 3-5 pips
- **BEARISH setup:** SL = Above the previous swing HIGH on M15 (above the high of the pullback / manipulation candle) + buffer of 3-5 pips

SL must be BEYOND the OB/FVG extreme — never inside the zone.
AFTER 1:1 R:R reached: Move SL to a few pips beyond the confirmation candlestick (the M5 candle that confirmed the entry).

## STEP 7 — Take Profit Targets (Zone-Based)
**IMPORTANT:** TPs are calculated from H1 rejection zones and structural levels, NOT from fixed R:R ratios alone. The final TP targets the ChoCH reversal point.

### ZONE-BASED TP CALCULATION:
1. Scan H1 data for rejection zones between entry and final target: Previous swing highs/lows on H1, Areas where price was rejected, Supply/demand zones on H1. These become intermediate TP levels.
2. **FINAL TP = The point where market made the ChoCH:** i.e., the point at which price reversed AFTER breaking past the last 2 swing highs (bearish) or 2 swing lows (bullish). This is the structural reversal point on H1.
3. Assign TPs to zones (closest to farthest from entry):
   - **TP1** (close first portion at ~1:1 R:R): First H1 rejection zone / start of pullback. After TP1 hit: move SL to breakeven (entry + buffer).
   - **TP2** (intermediate): Next H1 rejection zone between TP1 and final target. Any significant S/D zone or previous BOS level. Minimum 3:1 R:R required.
   - **TP3 / FINAL TP** (last portion): The ChoCH reversal point on H1. Where price originally broke past 2+ swing highs/lows and then reversed. Minimum 5:1 R:R.

IF full confluence (OB + FVG both present): Pursue final TP more aggressively. Use minimum 1:3 R:R for longer entries.

## STEP 8 — Trade Management
1. All TP positions open at entry (no deferred stacking).
2. When TP1 hits → move ALL remaining positions to breakeven.
3. Trailing: SL trails to below each new Higher Low (buys) or above each new Lower High (sells) on M15.
4. If trade open for 3 sessions without TP1 → close at market.
5. Close all positions at 23:00 GMT Friday (weekend gap risk).

## Multi-Timeframe Data Requirements
| Timeframe | Purpose | Data Needed |
| :--- | :--- | :--- |
| **H4** | Bias determination | ~200 candles (MarketStructureDetector) |
| **H1** | BOS identification + rejection zones + TP targets | ~500 candles (structure + zones) |
| **M15** | ChoCH detection + OB/FVG at retest | ~1000 candles (fine-grained structure) |
| **M5** | Candlestick confirmation at entry zone | ~500 candles (pattern detection) |
