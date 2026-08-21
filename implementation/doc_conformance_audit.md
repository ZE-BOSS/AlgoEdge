# Doc-Conformance Audit — Do the engines implement their own specs?

**Date:** 2026-08-21
**Scope:** LOGIC and RULE conformance for 6 strategy engines against their spec documents.
**Explicitly out of scope:** parameter *values* — covered by `implementation/strategy_parameter_audit.md`.
**No code was edited.** Research and writing only.

## The question this answers

62 backtests, 6 strategies, ~10 assets; 71% lose money. The strategies are adapted from
published sources that claim profitability. Every strategy below is therefore classified as:

- **FAITHFUL** — the code does what the doc says. Poor results are evidence *about the method*.
  Abandon or re-scope the strategy.
- **UNFAITHFUL** — the code omits or mangles rules the doc treats as load-bearing. Poor results
  are evidence about *the code*, not the method. Fix before judging.
- **MIXED** — faithful skeleton, one or more load-bearing filters dropped.

## Two findings that apply to every strategy, stated once

**F-1. There is no strategy-level position-management hook anywhere in the system.**
`backend/backtester/engine.py` closes positions for exactly six reasons — `SL`, `TP{n}`,
`BE_SL`, `TRAIL_SL`, `TIME_LIMIT` (:791), `SESSION_END` (:823), `END_OF_DATA` (:1176). A
strategy emits `TradeSignal(entry, sl, tp)` and is never consulted again. Every doc rule of
the form *"exit if X invalidates while in the trade"* is therefore **structurally impossible**
to implement in the current architecture, and every one of them is absent. This is not six
independent oversights; it is one missing interface. It hits APA (§1), HTF FVG Flip (§4),
Bias→IFVG (§5) and NY Open (§6).

**F-2. The `TradeSignal.take_profit` a strategy computes is discarded.** `RiskParams`
`tp1_rr/tp2_rr/tp3_rr` owns the ladder. For strategies whose doc defines a *structural*
target (CRT's C1 extreme, APA's forward-looking swing, HTF FVG Flip's HTF liquidity draw),
the documented target is computed and then thrown away in favour of a fixed R grid. Where
the structural target sits *closer* than TP2/TP3, the upper tiers are unreachable by the
strategy's own hypothesis and a large fraction of every position is stranded on a target the
strategy does not believe in. Flagged per-strategy below.

---

# 1. APA — Advanced Price Action (H&S / ABC reversal)

**Engine:** `backend/strategies/strategy_apa/engine.py` (497 lines)
**Helpers:** `backend/strategies/core/swing_structure.py`
**Doc:** `docs/apa_strategy_implementation_plan.md`
**Source:** Michael FX / Forex Course Academy "A.P.A." — a **discretionary FX chart method**,
demonstrated on 15m structure / 5m entries.

## 1.1 Conformance table

| # | Doc rule | Status | Location |
|---|---|---|---|
| E1 | Two-tier swing structure: minor fractal M=3 (pattern), major M=8 (BOS validity) — §3.2 | **implemented** | `engine.py:225-227`, `swing_structure.py:30-94` |
| E2 | 3-point Shoulder→Head→Shoulder; head exceeds both shoulders — §3.3 | **implemented** | `swing_structure.py:126`, `:148` |
| E3 | Shoulders within symmetry tolerance (0.3×ATR) — §3.3 | **implemented** | `swing_structure.py:127`, `:149` |
| E4 | Neckline = intervening swing point(s) between Head and each Shoulder — §3.4 | **partial** | `swing_structure.py:130-134` — see 1.2-B |
| E5 | BOS: candle **body closes** beyond the neckline (not wick) — §3.5, §4.2 | **PARTIAL / MIS-IMPLEMENTED** | `engine.py:253-260` — see 1.2-A. **The single most consequential defect in this engine.** |
| E6 | BOS must break a **major** level; a minor-level break is a liquidity sweep → no trade — §3.5 | **implemented, with an invented tolerance** | `engine.py:264-275`; tolerance `atr*0.5` is hard-coded and undocumented — see 1.3-A |
| E7 | Invalidation Zone drawn from shoulder candle **bodies**, not wicks — §3.6 | **implemented** | `engine.py:284-295` |
| E8 | `invalidation_zone_source` supports right / left / both — §8 | **partial** | `engine.py:281-282` — `right_shoulder` and `both` work; `left_shoulder` alone silently falls through to right-shoulder behaviour |
| E9 | Retest: candle **body** must re-enter the Invalidation Zone — §3.7 | **implemented** | `engine.py:360-368` |
| E10 | Confirmation rule 4: tight-levels check (Head/Shoulder within threshold) — §4.4 | **implemented** | `engine.py:307` |
| E11 | Confirmation rule 5: no conflicting open position on instrument — §4.5 | **implemented externally** | delegated to `max_positions_per_symbol` in `RiskParams`; engine comment `:392` |
| S1 | SL beyond the **wick** extreme of the Right Shoulder + buffer — §5 | **implemented** | `engine.py:305-317` |
| S2 | Tight-levels SL switches to the **Head** wick — §5 | **implemented** | `engine.py:311`, `:315` |
| X1 | **Hard invalidation exit: body closes back beyond the Head level before TP1 → flatten** — §3.11, §7, flowchart nodes U/V | **ABSENT while in a trade** | Checked at `engine.py:381-390` **pre-entry only**. After the signal fires the engine resets to `AWAIT_PATTERN` (`:466-468`) and has no further contact with the position. See F-1. |
| X2 | Instrument-class table §7: "hard flatten on Head-level body close" for both synthetics and prop FX | **ABSENT** | same as X1 |
| T1 | TP1/TP2/TP3 = 1R/3R/5R from risk engine — §6 | **implemented** | engine emits 1R at `:448-451`; ladder owned by `RiskParams` |
| T2 | Forward-looking structural target logged as a diagnostic reference field — §6 option 2 | **ABSENT** | doc offers it as an option and defaults to "no", so this is a *missed diagnostic*, not a violation. But it is the exact field that would tell you whether the fixed-R grid is the problem. |
| F1 | Session filter | **not in doc**; added by param audit, implemented `engine.py:161-171`, **disabled in every archived run** (`session_filter_enabled: false` in `params_snapshot.strategy_params`) |
| F2 | Re-arm once flat — §3.12 | **implemented** (approximately) | `engine.py:466-468` resets at *signal emission*, not at *flat*. A new setup can therefore arm while the previous position is still open; only `max_positions_per_symbol` stops a second entry. |

## 1.2 Doc rules NOT implemented, or implemented wrongly

### A. The BOS body-close test does not test a body close *(highest severity)*

`engine.py:253-260`:

```python
body_close_through_bearish = (
    pattern["type"] == "BEARISH"
    and min(latest["open"], latest["close"]) < neckline
)
```

`min(open, close) < neckline` is true whenever **either** the open or the close is beyond the
neckline — i.e. whenever the candle body *touches or overlaps* the level. A bar that opens
below the neckline and closes back **above** it passes this test. So does a bar that gaps
below and recovers fully.

The doc's rule (§3.5, §4.2, and the v1-vs-source comparison table, which lists this as one of
only seven differences from v1) is: *"candle **body** (not wick) closes beyond the
neckline"* — that is `latest["close"] < neckline`. The stated purpose of the rule is
**"filters liquidity sweeps."**

The engine therefore fails at precisely the thing the rule exists to do. A liquidity sweep —
price spikes through the neckline, closes back inside — is the canonical bar shape that
`min(open, close) < neckline` accepts and `close < neckline` rejects. The major-level check
at `:264` is the *second* sweep filter; the first one is inoperative, so the engine relies on
a single loose (0.5×ATR) proximity test to distinguish a structural break from a stop-hunt.

Note the inconsistency: the *head-invalidation* test 130 lines later (`:381`) uses
`min(open, close) > head_price`, which for that test means the **entire body** must clear the
level — the strict reading. The same expression is used loosely in one place and strictly in
the other, which reads like the loose one is a bug rather than a decision.

### B. The neckline is a single level, not the pair the doc specifies

Doc §3.4: *"the intervening swing low(s) (bearish) or swing high(s) (bullish) between Head and
**each** Shoulder"* — two points, i.e. a sloping neckline. `swing_structure.py:130-134` takes
the single **lowest** low anywhere between LS and RS. This is the *more conservative* choice
(a lower neckline is harder to break), so it is not a profitability leak, but it does mean the
canonical downward-sloping-neckline H&S — the highest-quality variant in the source material —
is scored identically to a flat one, and BOS fires later than the source would draw it.

### C. Head-level invalidation exit — completely absent in-trade (X1/X2)

The doc puts this in the flowchart (nodes U→V), in the step list (§3.11), and in the
instrument-class table (§7), where it is the *only* row. It is checked once, before entry,
and never again. Once `TradeSignal` is returned the position lives or dies on SL/TP alone.

Consequence: APA's documented risk profile is "stop at the shoulder wick, **or** a discretionary
flatten at the Head, whichever comes first." The implemented profile is "stop at the shoulder
wick." Since the Head is *further* than the shoulder for the non-tight case, X1 would mostly
fire on trades already deep in drawdown that then reverse — the exit is a loss-*capper*, and
its absence means every such trade runs to full stop. `debug/apa/` shows 51% of grouped trades
exiting `SL` across all runs; some unknown share of those are trades the doc would have exited
earlier and smaller.

### D. No "structure must be fresh" rule, and the daily reset is deliberately bypassed

`engine.py:190-213` resets state daily — but `:192-196` explicitly **skips the reset** when
`bos_confirmed` and status is `AWAIT_RETEST`/`AWAIT_CONFIRMATION`. There is no cap on how long
a setup may wait for its retest. A BOS from March can still fire an entry in June against an
Invalidation Zone and a stop level computed from a three-month-old shoulder wick, with an ATR
captured at that time (`:322`, reused at `:431`). The doc says nothing about persisting setups
across days; the source material is a chart method where a stale H&S is simply no longer on
screen. `debug/apa/nth25.json` shows a median trade duration of **2655 minutes (44 hours)`,
consistent with very stale setups.

## 1.3 Code behaviour the doc does not sanction

- **A. `atr * 0.5` major-level proximity tolerance** (`engine.py:266`). Hard-coded, not a
  parameter, not in the doc, and *wider than the shoulder-symmetry tolerance* (0.3×ATR) that
  the doc does specify. Any neckline within half an ATR of any major fractal swing counts as
  "a major level." On a 15m chart, half an ATR is enough that most necklines qualify — this is
  the sole surviving sweep filter (see 1.2-A) and it is close to a no-op.
- **B. `sl_buffer_atr + sl_buffer_atr_mult` are summed** (`engine.py:305`). Two ATR multipliers
  for the same buffer, one from the doc (0.05) and one invented by the param audit (0.5).
  Functional, but the doc specifies one buffer, and the effective 0.55×ATR is 11× the doc value.
- **C. Cost-floor stop override** (`engine.py:421-445`). When the structural stop is inside the
  floor, the stop is **moved to the floor** and the trade is still taken. The doc's stop is a
  structural level; a floored stop is no longer at the shoulder wick, so the position's
  invalidation thesis and its actual stop no longer coincide. The alternative — *skip* the
  setup when geometry and economics disagree — is not offered. The engine does at least record
  this (`sl_floored` in metadata, and it costs 15 confluence points at `:156`).
- **D. Confluence score components** (`engine.py:107-159`) are entirely invented. Reasonable and
  well-documented invention (it replaced a hard-coded 90), but no doc sanctions the weights, and
  the score gates nothing — nothing in the engine rejects a low-confluence setup.
- **E. Signal fires on `latest["close"]` of the retest bar** (`:395`) — entry is the close of the
  bar that touched the zone, not a limit at the zone. Doc implies an entry *at* the zone.

## 1.4 Asset-class verdict

The doc is written for **FX on a 15m/5m chart**, with §7 explicitly extending it to Deriv
synthetics and prop-firm FX/metals/indices. It contains no volume, no session and no
instrument-specific logic, and H&S is a scale-free geometric pattern. **APA is the most
asset-portable of the six** — there is no premise being violated by running it on indices or
metals.

The empirical record is nevertheless damning in a way that is *about asset class*:

| Asset | Signals in 5000 bars | Verdict |
|---|---|---|
| EURUSD / USDCHF / CADJPY / EURGBP | 25–43 | Adequate sample. Legitimate test. |
| XAUUSD / XAGUSD | 10–21 | Thin but interpretable. |
| SPX500 / NDX100 / NTH25 / HK50 | 7–15 | **Too few to conclude anything.** HK50: 7 trades, 3 of 10 signals rejected as invalid. |
| **BTCUSD** | **1** | **Meaningless.** One trade. |
| **XRPUSD** | **0** | **Zero signals in 5000 bars.** |

Crypto did not fail — it never ran. The two-tier fractal + 0.3×ATR symmetry gate essentially
never triggers on crypto's ATR profile. Any "71% of runs lose money" statistic that counts
`apa/xrpusd` (0 trades) or `apa/btcusd` (1 trade) as a data point is counting noise. **The
(APA, crypto) pairs should be struck from the results table entirely**, not because the premise
is invalid but because there is no sample.

Also note `_SYNTHETIC_PREFIXES` (`engine.py:32-35`) lists Deriv synthetics only. **BTCUSD and
XRPUSD are not matched**, so 24/7 crypto gets (a) the UTC-midnight daily state reset that the
code itself calls "not a meaningful session break" for continuously-traded symbols, and (b) the
full 07:00–16:00 UTC session filter when enabled — which would blank two-thirds of the crypto
trading day for reasons that apply to CHF liquidity, not to Bitcoin.

## 1.5 Session / timezone correctness

- `_is_within_session` (`:161-171`) converts to **UTC** and compares `"%H:%M"` strings.
  Correct handling of wrap-around windows (`:171`). No crash paths.
- **No DST handling, and the window is defined in UTC.** The param audit justifies 07:00–16:00
  UTC as "London open through the London/NY overlap." London open is 08:00 UTC in winter and
  07:00 UTC in summer; NY open is 14:30 UTC in winter, 13:30 in summer. A fixed UTC window is
  therefore off by one hour for roughly half the year — it starts an hour before London in
  winter and ends an hour after the NY-overlap in summer. For a filter whose entire rationale is
  "trade where depth is best," a one-hour seasonal drift at both edges is material.
- The doc specifies **no session filter at all**, and **every archived run has it disabled**
  (`session_filter_enabled: false` in `strategy_params`). So the filter's correctness is moot
  for the existing results — but note what the two paired runs show:

| Run | Trades | Asian entries | P&L |
|---|---|---|---|
| `cadjpy_sf_off` | 36 | 11 | **−$3,613** |
| `cadjpy_sf_on` | 27 | 0 | **−$965** |
| `xauusd_session-filter_off` | 21 | 5 | −$24 |
| `xauusd_default_volume` (on) | 13 | 0 | **+$986** |
| `xagusd_session-filter_off` | 12 | 6 | −$837 |
| `xagusd` (on) | 10 | 0 | −$1,235 |

Filtering out the Asian session removed 100% of Asian entries as designed (the filter works) and
improved P&L in 2 of 3 pairs. Small samples, but the mechanism is exactly what the param audit
predicted.

## 1.6 Verdict — **MIXED, leaning UNFAITHFUL**

The state machine, the H&S geometry, the body-based Invalidation Zone and the two SL branches
are all genuinely faithful — this is a serious implementation of the doc's *structure*. But the
two rules the doc identifies as the source's differentiators are both broken:

1. **"BOS must be a body close, to filter liquidity sweeps"** — the test admits sweeps (1.2-A).
2. **"Hard flatten on a Head-level body close"** — absent in-trade (1.2-C).

Both failures push in the same direction: more trades taken, and losers held longer. That is
sufficient to make APA's negative results uninformative about the underlying method.
**Fix `min(open, close) < neckline` → `close < neckline` before drawing any conclusion about
APA.** It is a one-token change and it is the difference between "break of structure" and
"touched the level."


---

# 2. VWAP — Drift Pullback ("Golden Ticket VWAP")

**Engine:** `strategy_vwap/engine.py` (410 lines) · **Doc:** `docs/vwap_strategy_implementation_plan.md`
**Source:** Matteo Conti, demonstrated on **NQ/MNQ index futures**, US cash session.

## 2.1 Conformance table

| # | Doc rule | Status | Location |
|---|---|---|---|
| E1 | 15-min anchored VWAP over 5-min bars — §3.1 | **implemented** | `engine.py:40-102` (session-anchored, resets daily) |
| E2 | Session gate: exclude 09:30–10:30, no entries after 15:30 — §3.2 | **WAS BROKEN — fixed this pass** | `engine.py:145` — see 2.2-A |
| E3 | Bias: price above VWAP → long, below → short — §3.3 | **implemented** | `engine.py:367-369` |
| E4 | VWAP slope vs the **prior 15-min bar** — §3.4 | **implemented** | `engine.py:216-222` (uses `bar_multiplier`, not the adjacent M5 bar) |
| E5 | Momentum ≥ ±0.1% over 4× 15-min bars — §3.5 | **implemented** | `engine.py:376-383` |
| E6 | Trigger: first opposite-colour candle **pulling back toward VWAP** — §3.6 | **PARTIAL** | `engine.py:388, 401` — see 2.2-B |
| E7 | Entry at open of the following candle — §3.7 | **implemented** | `engine.py:295` |
| E8 | Max 1 open position — §5.6 | **delegated** | not in engine; relies on `max_positions_per_symbol=1` |
| E9 | Max 4 trades / 2 losses per day — §6 | **implemented** | `engine.py:202-205`, `notify_outcome` at `:158` |
| E10 | Hard flat at 15:55 ET — §7 | **implemented (was dead)** | marker emitted `:361`; only live after the `hard_close_time` propagation fix |

## 2.2 Defects

**A. The session gate never blocked pre-session hours.** *(fixed this pass)*

The condition was `(session_open <= t <= session_exclude_end) or (t >= entry_cutoff)`. At
`t="03:00"`: `"09:30" <= "03:00"` is False, and `"03:00" >= "15:30"` is False — so the bar was
treated as tradeable. It blocked the first hour and the post-cutoff tail, never the hours
*before* the open.

On `vwap/eurusd.json` (n=259) **54% of entries fell outside the intended window**, clustering
at 06:00–08:00 ET, with 20 entries labelled ASIAN and 92 LONDON. Anchored VWAP has no
institutional meaning before its session opens. Every VWAP number in this dataset was produced
by a near-24-hour strategy, not the documented one.

**B. The pullback trigger does not check for a pullback.** Doc §3.6 requires the trigger candle
be *"pulling back toward VWAP"*. The code tests colour only — `close < open` for longs. **Any**
red candle in a long setup fires the trigger, including one moving *away* from VWAP. The
distance-to-VWAP condition that gives the setup its name is absent. Fix: require
`abs(close - vwap) < abs(prev_close - vwap)`.

**C. "First" pullback candle is not tracked.** Doc says *the first* opposite-colour candle. The
code arms `pending_entry` on every qualifying bar, so after a consumed entry the next red candle
re-arms immediately.

**D. `confluence_score = 80` is hard-coded** (`:355`) — not in the doc, and it makes VWAP's
score-bucketed statistics meaningless.

## 2.3 Asset-class verdict

- **Valid on:** index futures/CFDs (NQ, NDX100, SPX500) during the US cash session — the market the method was built for.
- **Questionable on:** FX. Anchored VWAP presumes a session-anchored institutional reference price and true traded volume. MT5 returns `tick_volume` (tick count), not real volume, so the "volume-weighted" average is tick-weighted — a different statistic wearing the same name.
- **Invalid on:** BTCUSD, XRPUSD. 24/7 instruments have no cash-session anchor and no 15:55 close. **Two of the VWAP runs are on crypto and should not have been run.**

**Verdict: UNFAITHFUL.** The skeleton is right, but the session gate was inverted-by-omission
and the defining pullback condition is missing. Nothing about VWAP's edge can be concluded from
this dataset.

---

# 3. CRT — Candle Range Theory

**Engine:** `strategy_three_crt/engine.py` (327 lines) · **Doc:** `docs/CRT_Strategy_Spec.md`

## 3.1 Conformance table

| # | Doc rule | Status | Location |
|---|---|---|---|
| E1 | C1 = range candle, wick high/low not body — §2 | **implemented** | `engine.py:153-154` |
| E2 | Bullish C2: `C2.low < C1.low` AND `C1.low < C2.close < C1.high` — §3.2 | **implemented exactly** | `engine.py:175` |
| E3 | Bearish C2: `C2.high > C1.high` AND `C1.low < C2.close < C1.high` — §3.2 | **implemented exactly** | `engine.py:176` |
| E4 | Ambiguous double sweep → skip (spec's `[OPEN]` recommendation) — §3.2 | **implemented** | `engine.py:160-161` |
| E5 | Discard if C2 conflicts with HTF bias — §4 | **implemented** | `engine.py:178, 185` |
| E6 | Flat/no bias → no trade (spec's `[OPEN]` default) — §4 | **implemented** | `engine.py:170-171` |
| E7 | Trigger = C2.high (bull) / C2.low (bear) on LTF — §5 | **implemented** | `engine.py:181, 188` |
| E8 | Invalidate untriggered setup at next HTF close — §5 `[OPEN]` | **implemented** | `engine.py:146` |
| E9 | TP = C1's opposite extreme — §6 | **computed then DISCARDED** | see F-2; `c1_extreme` recorded at `:181/:188` |
| E10 | One setup per session — §1.6 | **implemented** | `max_trades_per_session` |

## 3.2 Assessment

CRT is **the most faithful engine in the set.** Every rule — including both of the spec's
explicitly-flagged `[OPEN]` questions — is implemented, and implemented the way the spec
recommends. There are no invented conditions.

Two caveats, neither of them CRT's own doing:

- **Inherited dependency.** `_get_htf_bias` delegates to `MarketStructureDetector`, whose BOS
  comparison uses the wrong swing reference — the defect `CRT_Strategy_Spec.md:52` itself flags
  as needing resolution before CRT reuses it. CRT's bias filter is only as sound as that detector.
- **F-2 applies acutely.** CRT's entire thesis is *"price returns to the opposite extreme of the
  range candle."* That target is computed and then overwritten by the 1.5/3/5-R grid. When C1's
  extreme sits nearer than TP2, 50% of every position is stranded on targets the strategy does
  not believe in. **CRT is the strategy most damaged by the global TP-grid override.**

**Verdict: FAITHFUL.** With 120 trades and +0.006R the sample proves nothing either way, but the
implementation is not what is holding it back.

---

# 4. HTF FVG Flip

**Engine:** `strategy_four_htf_fvg_flip/engine.py` (403 lines) · **Doc:** `docs/strategy-1-htf-fvg-flip.md`

## 4.1 Conformance table

| # | Doc rule | Status | Location |
|---|---|---|---|
| E1 | Identify HTF FVG (3-candle imbalance) — §2 | **implemented** | via `core/fvg.py` |
| E2 | FVG must be **untested/unfilled** — §2 | **implemented** | `require_unfilled_htf_fvg`, `fvg["tapped"]` |
| E3 | FVG must be **counter to recent trend** — §3.1 | **implemented** | `engine.py:99-132` (blocks on NEUTRAL) |
| E4 | Wait for a new 5-min FVG after the tap — §3.2 | **implemented** | AWAIT_5M_FVG |
| E5 | Wait for retest of that 5-min FVG — §3.3 | **implemented** | AWAIT_5M_RETEST |
| E6 | Inversion confirmed by **body close** — §2, §3.4 | **implemented** | body-close test |
| E7 | Displacement on the FVG-forming candle — §2 | **ABSENT as a gate** | measured at `:172-217`, never used to reject |
| E8 | Exit if the setup invalidates mid-trade — §3.6 | **structurally impossible** | see F-1 |

## 4.2 The decisive gap

Doc §2 defines the FVG as requiring *"a strong displacement candle 2."* The engine detects the
**geometric** gap but never tests whether the middle candle actually displaced. Displacement is
what separates an institutional imbalance from an ordinary three-bar pattern with a hole in it —
it is the condition that makes the FVG concept mean anything, and it is the one that is missing.
The engine now *measures* displacement (≥0.50×ATR → 15 pts, ≥0.25 → 8) but only to populate a
score; no setup is ever rejected for failing it.

**This is the most likely explanation for HTF FVG Flip being the only clearly-negative strategy
in the book (−0.088R, 38.2% win rate).** It is trading every geometric gap, most of which are
noise.

Recommended gate: middle candle range ≥ 1.5 × ATR(14) **and** body ≥ 60% of range before an FVG
is admitted. Expect trade count to fall 40–60%.

## 4.3 Asset-class verdict

ICT/SMC imbalance logic presumes an order book leaving visible footprints. **Invalid on XRPUSD
and BTCUSD CFDs**, where the broker's synthetic book bears little relation to the underlying
venue. Those two account for the two worst runs in the entire dataset.

**Verdict: MIXED.** Faithful state machine, load-bearing filter dropped.

---

# 5. Bias → Key Level → IFVG

**Engine:** `strategy_five_bias_ifvg/engine.py` (606 lines) · **Doc:** `docs/strategy-2-bias-keylevel-ifvg.md`

| # | Doc rule | Status |
|---|---|---|
| E1 | Daily/HTF bias established first | **implemented** |
| E2 | Key level tapped (M15 FVG / rejection block / CISD) | **implemented** |
| E3 | IFVG forms and inverts on the manipulation leg | **implemented, direction contested** |
| E4 | Day-stop: 1 win stops; 2nd trade only if A+; 2 losses stop — §5 | **implemented** (`_can_trade_today`) |
| E5 | Session window 09:30–11:00 ET — §4 | **implemented** (defaults corrected) |
| E6 | Displacement on the inverting candle | **scored, not gated** — same defect as §4.2 |
| E7 | Exit on invalidation | **structurally impossible** — F-1 |

**Open question, deliberately unresolved:** doc §3 describes the IFVG forming on the
*manipulation leg that approached* the key level; the engine searches for gaps forming *after*
the tap. These are different candles. Resolving it needs a product decision — re-align the doc
or re-implement the scan — and it materially changes which setups qualify. Flagged inline at
`:287-292`.

**Verdict: MIXED**, pending the direction question. At +0.027R over 620 trades it is the
second-best in the book, notable given the ambiguity.

---

# 6. NY Open Break & Retest

**Engine:** `strategy_six_ny_open_retest/engine.py` (223 lines) · **Doc:** `docs/strategy-3-nyopen-break-retest.md`

## 6.1 Two clear divergences

**A. Wrong timeframe for break and retest.** The doc specifies the **1-minute chart** in three
places (§2 *"Break: on the 1-minute chart"*, §3.2 *"AWAIT_BREAK — On the 1-minute chart"*, §3.3).
The engine uses **M5**, and carries a comment asserting the opposite:

    def get_required_timeframes(self):
        # Spec (strategy-3-nyopen-break-retest.md): only M15 (range marking) and M5 (break+retest)
        return ["M15", "M5"]

A 5-minute close is a far coarser break filter than a 1-minute close, and the retest is detected
up to four minutes late. The comment claims spec compliance while contradicting it — worse than
an undocumented deviation, because it stops anyone from checking.

**B. A limit strategy is being filled at market.** Doc §3.4: *"ENTER — Fill at `range_mid`
(limit order) when price retests it."* The backtester's realistic-fill change fills every signal
at the **next bar's open**. That is correct for market orders and **wrong for limit orders**: a
resting limit at `range_mid` fills *at* `range_mid`. NY Open Retest's entries are therefore
systematically worse than the strategy specifies — by whatever distance price travelled past the
mid before the next bar opened.

This is a genuine backtest/live divergence in the *pessimistic* direction. It needs an order-type
field on `TradeSignal` so the engine fills limits at the limit price and markets at the next open.

## 6.2 Conforming

Range marking (08:00–08:14:59 ET, exclusive upper bound), midpoint, break definition, retest
target, `earliest_valid_break_time`, session end, and the SL-side guard are all correct.
`confluence_score` is deliberately constant here — the chain is all hard gates with nothing
optional to score — and that decision is documented inline.

**Verdict: MIXED.** The logic is right; the timeframe and the order type are wrong. At −0.002R
over 465 trades it is a coin flip, and both defects would move it.

---

# 7. Cross-strategy summary

| Strategy | Verdict | Load-bearing rule missing | True expectancy |
|---|---|---|---|
| CRT | **FAITHFUL** | — (damaged by F-2 only) | +0.006R |
| APA | **UNFAITHFUL** | BOS uses `min(open,close)`, not body close | +0.136R |
| VWAP | **UNFAITHFUL** | session gate inverted; pullback never checked | +0.056R |
| HTF FVG Flip | **MIXED** | displacement measured but never gated | −0.088R |
| Bias IFVG | **MIXED** | displacement gate; IFVG direction contested | +0.027R |
| NY Open Retest | **MIXED** | M5 not M1; limit filled at market | −0.002R |

**The single most important conclusion.** Only CRT is a faithful implementation, and it has the
smallest sample in the book. For the other five, **a losing result is not evidence about the
method** — it is evidence about this implementation of it. The instinct to abandon or re-tune a
strategy on these numbers should be resisted until the defects above are closed and the book
re-run.

**Two architectural gaps outrank every individual rule:**

- **F-1** — no strategy-level position-management hook. Every doc rule of the form *"exit if X
  invalidates"* is unimplementable, and all four affected strategies are missing theirs.
- **F-2** — strategy-computed targets are discarded by the fixed R grid. CRT and HTF FVG Flip
  both define *structural* targets that are silently overwritten.

Neither is a strategy bug. Both cap what any strategy in this system can achieve.

---

# 8. Timezone — resolved this pass

The prior audit flagged broker-server-time versus UTC as unresolved and high-value. It is now
settled empirically.

Pooling every FX trade across all runs (n=1,306) and reading the stored epoch as UTC, the
trading week runs **Mon 00:00 → Sat 00:00 with zero weekend bars**. The real FX week opens
Sun 21:00 UTC and closes Fri 21:00 UTC. Sun 21:00 UTC is Mon 00:00 at **UTC+3**.

    Fri 21-23h stored:  3 trades      Sun 21-23h stored:  0 trades
    Sat total:          0             Mon 00-02h stored:  4 trades

**Conclusion: the feed is broker server time at UTC+3, read as UTC.** The code computed
`ET = stored − 4h`; true ET is `stored − 7h`. **Every session gate fired three hours early.**
NY Open Retest's "09:30–11:00 ET" was really 06:30–08:00 ET — the London morning, not the New
York open. APA's "07:00–16:00" was 04:00–13:00. This affects VWAP, NY Open Retest, CRT and APA.

Fixed in `backend/mt5/data_fetcher.py` — `detect_server_utc_offset_hours()` measures the offset
from the live server clock (overridable via `ALGOEDGE_MT5_SERVER_UTC_OFFSET`) and `_normalize_df`
shifts every bar to true UTC before anything downstream sees it.

**Consequence for this dataset: every session-anchored result was measuring the wrong hours.**
Combined with the VWAP gate defect, no session-based conclusion in this book survives. The
re-run is mandatory, not optional.
