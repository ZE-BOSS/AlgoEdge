> **UPDATED (2026-08, Phase 6 — [Doc-1]).** The state machine described below (§10 onward,
> `strategy_apa/engine.py`) has been rewritten from a single-candidate slot to a bounded list of
> independently-progressing candidates (`max_concurrent_patterns`, default 3), each with its own
> per-state staleness budget (`pattern_max_age_bars`/`bos_max_age_bars`/`retest_max_age_bars`) instead
> of the old UTC-midnight calendar reset. Also fixed since this plan was written: the body-close BOS
> test now reads `close`, not `min(open,close)`/`max(open,close)`; the `is_major` neckline gate widened
> from a hardcoded 0.5×ATR to `neckline_major_atr_tolerance` (default 1.0), with a pattern admitted only
> by the wider tolerance scoring lower on confluence instead of being discarded; the SL cost floor is now
> capped at `max_sl_floor_atr_mult` (default 5.0×ATR) so it can only ever narrow a too-tight stop, never
> silently re-widen an already-wide one. The pattern-detection rules (H&S geometry, Invalidation Zone,
> confirmation) documented from §1 onward are unchanged — only the state machine holding them changed.
> See `implementation/TASKS.md` Phase 6 (6.1–6.5) for the full change record and verification notes.

# Advanced Price Action (APA) — Deterministic Implementation Plan (AlgoEdge)
## v2 — Optimized against Michael FX / Forex Course Academy's A.P.A. framework

**System philosophy:** unchanged from v1 — fully rule-based structure/pattern formulas, no ML, no Monte Carlo, no separate testing layer. Strategy calculates entry + SL only; the Risk Parameter Engine owns TP1–TP5.

**What changed from v1:** v1 used generic candlestick patterns (pin bar/engulfing/inside bar) triggered at generic S/R zones. This source describes one specific, more testable setup instead — a Head-and-Shoulders / ABC structural reversal with a defined "Invalidation Zone" retest entry — which now replaces that as the core pattern. Generic candlestick patterns are dropped as the primary trigger.

---

## 1. Visual Flow

```mermaid
flowchart TD
    A[Market Data Feed - Structure TF] --> B[Calculate Swing Structure - Major and Minor]
    B --> C[Detect 3-Swing Sequence: Shoulder - Head - Shoulder]
    C --> D{Valid H&S/ABC Formation?}
    D -->|No| E[No Setup - Wait]
    D -->|Yes| F[Mark Neckline From Intervening Swing Points]
    F --> G{Body Closes Beyond Neckline - Major Level?}
    G -->|No - Minor/Liquidity Sweep| E
    G -->|Yes - Valid BOS| H[Structural Shift Confirmed]
    H --> I[Draw Invalidation Zone From Right Shoulder Candle Bodies]
    I --> J{Price Retraces Into Zone?}
    J -->|No| K[Wait for Retest]
    J -->|Yes - Body Enters Zone| L[Confirmation Rule Stack]
    L --> M{All Checks Pass?}
    M -->|No| E
    M -->|Yes| N[Signal Fired]
    N --> O[Strategy Calculates Entry + SL]
    O --> P[Hand Off to Risk Parameter Engine]
    P --> Q[Position Size From SL Distance]
    P --> R[TP1 = 1R / TP2 = 3R / TP3 = 5R]
    Q --> S[Trade Executed]
    R --> S
    S --> T[Manage Position: Partials at TP Levels]
    T --> U{Body Closes Back Beyond Head?}
    U -->|Yes| V[Invalidation Exit]
    U -->|No| W[Hold / Trail to Next TP]
```

---

## 2. Comparison: v1 vs. this source

| Element | v1 (generic APA) | Michael FX / A.P.A. (this source) |
|---|---|---|
| Core setup | Candlestick pattern at any S/R zone | Specific Head & Shoulders / ABC structural reversal |
| Entry trigger | Pattern completion (pin bar, engulfing, inside bar) | Retest of the "Invalidation Zone" (shoulder level) after BOS |
| Zone drawn from | Swing point clusters | Candle **bodies** of the shoulder formation specifically |
| BOS validity | Any structure break | Must break a **major** level — body close, not wick; filters liquidity sweeps |
| SL placement | Beyond pattern wick / zone / swing (3 options) | Beyond the **wick** of the shoulder specifically (or both head+shoulder wicks if levels are tight) |
| TP philosophy | N/A — deferred to risk engine | **Forward-looking**: targets future untested structural highs/lows, not prior reaction levels — the source's explicit differentiator from SMC/ICT |
| Timeframes shown | Unspecified | 15-min structure / 5-min entries |

---

## 3. Step-by-Step Implementation Plan

1. **Ingest bars** on a structure timeframe (default 15-min) and an entry timeframe (default 5-min), matching the source's demonstrated pairing.
2. **Calculate swing structure on two tiers**: *minor* swings (fractal M=3, tight lookback — for shoulder/head detection) and *major* swings (fractal M=8+, wider lookback — for BOS validity). This formalizes "major vs minor breakout."
3. **Detect a 3-point swing sequence** matching Shoulder → Head → Shoulder: the middle swing point (Head) exceeds both neighboring swing points (Shoulders) in the same direction; the two Shoulders fall within a tolerance band of each other (default 0.3×ATR).
4. **Mark the neckline**: the intervening swing low(s) (bearish case) or swing high(s) (bullish case) between Head and each Shoulder.
5. **Check BOS validity**: candle **body** (not wick) closes beyond the neckline, **and** that neckline level coincides with a *major* swing level from step 2. A break of only a *minor* level is treated as a liquidity sweep — no BOS, no trade.
6. **Draw the Invalidation Zone**: a box bounded by the candle **bodies** of the Right Shoulder formation (not wicks).
7. **Wait for retest**: price must retrace and a candle **body** must re-enter the Invalidation Zone.
8. **Run the Confirmation Rule Stack** (§4).
9. **If all checks pass** → signal fires → calculate entry and SL (§5).
10. **Hand off** direction, entry, SL to the Risk Parameter Engine (§6) — unchanged from v1.
11. **Manage the position**: hard invalidation if a candle body closes back beyond the Head level before TP1 — the pattern's structural thesis is void at that point.
12. **Re-arm** once flat.

---

## 4. Confirmation Rule Stack

1. Valid 3-point Shoulder-Head-Shoulder sequence detected (step 3).
2. BOS confirmed on a **major** level via body close (steps 4–5).
3. A candle body has entered the Invalidation Zone on retest (steps 6–7).
4. Tight-levels check: if Head and Shoulder sit within 0.2×ATR of each other, SL calculation must switch to cover both (see §5), not just the nearer one.
5. No conflicting open position already exists on this instrument.

---

## 5. Strategy-Owned SL Formula

- **Default**: SL = beyond the **wick** extreme of the Right Shoulder + small buffer (default 0.05×ATR).
- **Tight-levels case**: if Head and Shoulder are within 0.2×ATR of each other, SL = beyond the wick extreme of the **Head** instead — covers both levels in one stop, per the source's explicit rule.

This stays the only risk figure the strategy calculates.

---

## 6. Risk Parameter Engine Hand-off — unchanged

| Tier | R-Multiple | Ratio | Used by default |
|---|---|---|---|
| TP1 | 1R | 1:1 | Yes |
| TP2 | 3R | 1:3 | Yes |
| TP3 | 5R | 1:5 | Yes |
| TP4/TP5 | — | — | Available, off by default |

**Flag — a philosophy conflict worth a decision:** this source's stated edge over SMC/ICT is a *forward-looking* TP — targeting the next untested structural swing ahead of price, not a fixed R-multiple. Your risk engine's TP1/2/3 grid is fixed-R by design. Two options:
- **Keep the R-multiple grid as-is** (consistent with every other AlgoEdge strategy, simplest) — this plan does that by default.
- **Log the structural target as a reference field only** (next major swing high/low ahead, calculated but not used to place the live TP order) so you can compare, per trade, whether the fixed R-grid over- or under-shoots where the source's method would have exited. Doesn't touch the risk engine's ownership of TP — purely a diagnostic field on the signal record.

---

## 7. Instrument-Class Notes — unchanged

| Instrument class | Exit-on-invalidation |
|---|---|
| Deriv synthetics (24/7) | Hard flatten on Head-level body close |
| Prop firm FX/metals/indices (FundedNext, Bloom Funded, etc.) | Hard flatten — avoid reversing into new exposure against daily-loss/consistency rules |

---

## 8. Tunable Parameters

| Parameter | Default | Range to try | Effect |
|---|---|---|---|
| Structure timeframe | 15-min | 15-min–1H | Higher = fewer, higher-conviction H&S formations |
| Entry timeframe | 5-min | 1-min–15-min | Lower = tighter entries, more noise on the retest trigger |
| Minor swing fractal (M) | 3 | 2–5 | Shoulder/Head detection sensitivity |
| Major swing fractal (M) | 8 | 6–15 | Higher = stricter BOS filter, fewer but more valid breaks |
| Shoulder symmetry tolerance | 0.3×ATR | 0.15–0.5×ATR | Wider = more formations qualify as valid H&S/ABC |
| Tight-levels threshold | 0.2×ATR | 0.1–0.4×ATR | Controls when SL switches to cover both Head+Shoulder |
| SL buffer | 0.05×ATR | 0–0.15×ATR | Extra room beyond the wick stop |
| Invalidation zone source | Right Shoulder body | Right Shoulder / Left Shoulder / both | Which candle bodies define the retest box |
| Tight-levels threshold | ~~0.2×ATR~~ **0.35×ATR** | 0.1–0.4×ATR | Raised — see note |
| `sl_buffer_atr_mult` | **0.5** | 0–1.0 | Added — proportional cost cushion on top of the wick stop |
| `min_sl_pips` / `min_sl_atr_mult` | **12.0 / 1.0** | — | Added — absolute + volatility-relative stop floors |
| Session filter | ~~(none)~~ **enabled, 07:00–16:00 UTC** | — | Added — see note |

### 9. Revision note — 2026-08 cost-realism audit

§5's SL formula is correct as *geometry* but has **no cost model behind it**, and that gap
is load-bearing. The retest entry sits inside the Invalidation Zone, which §6 bounds by
shoulder **bodies**, while the stop sits at the shoulder **wick** — the two can be a
fraction of a pip apart. Measured across 11 real USDCHF runs, APA's median stop was
**3.47 pips with a minimum of 0.45 pips**, against a 1.5–2.5 pip spread: stops routinely
placed *inside the cost of entry*, hit by bid/ask bounce at the instant of fill. 30% of
legs exited within a single bar. Consequences:

- **Untradeable sizes.** `risk$ / stop_distance` drove APA to **40 lots on a $25,000
  account** — $4,000,000 notional, ~160× account leverage; MT5 rejects this with retcode
  10019 (No money).
- **Phantom profitability.** APA's realised per-trade expectancy is **−0.367R**. Its
  positive dollar P&L came entirely from winners being sized 1.63× larger than losers —
  a mechanical artifact of tight stops producing huge positions, not an edge.
- **Negative under real costs.** With 2.0 pip spread + 0.4 slippage + $6/lot, APA goes
  **+$6,283 → −$8,597**.

Three deliberate deviations from this document follow:

1. **Tight-levels threshold 0.2 → 0.35×ATR** (still inside the documented 0.1–0.4 range).
   The tight-levels branch selects the *Head* wick, which is always further from entry
   than the Right Shoulder wick — it is the **wider-stop** branch. Raising the threshold
   biases the engine toward it.
2. **`sl_buffer_atr_mult` 0.0 → 0.5, plus new `min_sl_pips` / `min_sl_atr_mult` floors.**
   §5's 0.05×ATR buffer is ~0.35 pips on USDCHF M15 — under a quarter of the spread.
   0.5×ATR adds ~3–4 pips (the proportional cushion); the 12-pip floor makes the
   degenerate case impossible (the absolute backstop). 12 pips ≈ 4× the ~3.0-pip
   round-trip friction, holding cost at ~0.25R instead of ~0.9R.
   ⚠ `sl_buffer_atr_mult` **is** read by the engine; `min_sl_pips` / `min_sl_atr_mult`
   are **not yet** and are inert until wired.
3. **Session filter enabled, 07:00–16:00 UTC.** This document specifies no session filter,
   but it was demonstrated on a chart, not a 24/5 FX feed. Left off, APA fires through the
   Asian session where USDCHF spread widens to 3–5 pips and the M15 range collapses to
   2–3 pips — precisely the regime that produced the sub-spread stops and the single-bar
   exits. 07:00–16:00 UTC is London open through the London/NY overlap, where USDCHF depth
   is best; 16:00–20:00 UTC is NY-only flow on a European cross.

Sample-size caveat that applies to all of the above: APA's forensic sample is **43 trades
on one symbol over 7 months**. That is far too small to infer an edge in either direction —
these changes are justified by *cost arithmetic*, which is deterministic, not by measured
performance, which is not.
