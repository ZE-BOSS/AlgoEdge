# AlgoEdge — Phase 14: Strategy Factory, Fundamentals-on-Chart, Symbol Identity

**Date:** 2026-08-23
**Written from:** your walkthrough feedback after the Phase 13 UI landed.
**Supersedes:** Phase 13 Part D.1 (Strategy Lab), which under-scoped what you
actually asked for — it built a *viewer* for existing strategies where you asked
for a *factory* that creates them.

---

## PART A — What your walkthrough established

Restating the gaps plainly, because three of them are scope misses on my side
rather than bugs.

| # | Your finding | My assessment |
|---|---|---|
| **A1** | Strategy Lab has no way to CREATE a strategy — no name field, no place to paste an MD spec, no generation | **Scope miss.** I built browse/preview/optimize and never built the create path, which was the point of the screen |
| **A2** | Fundamentals only *displays* data; it cannot be used *in* a strategy, and nothing renders on a chart | **Scope miss.** Part D.2 specified panels, not integration. Panels alone do not answer "where do I enter" |
| **A3** | No help/explanation anywhere — order flow "volume by price", depth "empty book", GEX ticker rules are all unexplained | **Fair.** Every panel assumes knowledge it never supplies |
| **A4** | Strategy Lab symbol is a hardcoded dropdown; Backtester lets you type any symbol | Real defect |
| **A5** | Preview produced no output, no chart, and is fixed at 45 days | Real defect |
| **A6** | GER40 vs GER30 — same asset, different broker names | Real gap: `SYMBOL_ALIASES` maps *spellings*, not *broker identities* |
| **A7** | Backtest froze the UI, dropped the socket, was very slow | **Fixed** — see Phase 13 §13.17 |
| **A8** | Marking lines ran to the chart edge | **Fixed** — §13.18 |
| **A9** | Chart teleported instead of sliding | **Fixed** — §13.19 |
| **A10** | Results UI expanded out of frame | **Fixed** — §13.20 |
| **A11** | APA took very few trades over 3 months | **Explained** — see Part B |
| **A12** | Win rate and profit factor both 0.0 | **Explained, and it is real** — see Part B |

---

## PART B — Answers from the run I executed on your terminal

XAUUSD / APA / 2026-06-01 → 2026-08-23, read off the Run Report panels.

### B.1 Why APA took so few trades

```
Evaluated        8
Risk-approved    7      (−1, 13%)
Filled           7
Closed           7

Blocked (8 recorded)
  same_direction_already_open ......... 7
  SL/TP invalid against fill price .... 1
```

**Roughly half of APA's setups never became trades, and one gate accounts for
all of them:** `same_direction_already_open` — the `max_positions_per_symbol = 1`
rule, which is decision **D-2** (kept at 1, deliberately). APA holds multi-day
positions, so a second setup arriving while the first is still open is simply
discarded.

So "APA takes few trades" is not the strategy being quiet. It is the position
limit silently halving its throughput. Three options, and this is your call:

1. **Raise `max_positions_per_symbol`** for APA specifically — Phase 12's
   per-slot config already supports a per-leg override; it is not wired to the UI.
2. **Enable `allow_pyramiding`** with `min_bars_between_entries` as the spacing
   guard (both fields exist, both are unwired).
3. **Leave it.** One position per symbol is a legitimate risk stance — but you
   should choose it knowingly rather than inherit it.

### B.2 Why the win rate is 0.0%

```
Exit reason   legs   share   net P&L
SL             17     81%    −483.78
TRAIL_SL        4     19%     +12.34
```

**Zero legs reached a take-profit.** Not a reporting bug — nothing hit TP.

The mechanism is a configuration collision:

```
tp1_rr         = 1.5
be_trigger_rr  = 1.5      <-- identical
```

Break-even arms at exactly the distance TP1 sits at. Price that travels far
enough to pay TP1 has, by the same bar, moved the stop to break-even; ATR
trailing (multiplier 3) then owns the exit. TP1 is unreachable *by construction* —
it can only ever be overtaken by the break-even it triggers.

That is why 81% of legs die at SL and the other 19% scratch at the trail. Fix by
separating the two: either `be_trigger_rr` above `tp1_rr` (bank TP1, then
protect), or `tp1_rr` below `be_trigger_rr` (take a partial before BE arms).
**I have not changed either value — this is a strategy decision, not a bug fix,
and guessing at it would corrupt the comparison against your existing corpus.**

### B.3 Risk deployment is healthy

Median realised risk **0.885%** against a **1.00%** target — drift −11.5%, range
0.373–0.933%. Within tolerance. The 0.373% floor is a lot-step clamp, now
visible per-trade via `sizing_diagnostics.binding_constraint`.

---

## PART B2 — APA against its own specification

You asked whether APA does what its doc says. I re-checked every requirement in
`implementation/doc_conformance_audit.md` (§1.2, rows E1–E11 / S1–S2 / X1–X2 /
T1–T2) against the code as it stands today, rather than trusting the audit's
own verdicts — several had been fixed since it was written.

### B2.1 Fixed since the audit

| # | Requirement | Status now |
|---|---|---|
| **E5** | BOS must be a candle **body close** beyond the neckline, not a wick | **FIXED.** `engine.py:284` now tests `latest["close"] < neckline` (bearish) / `> neckline` (bullish). Was `min(open, close)` — a wick-inclusive test that admitted liquidity sweeps as breaks of structure. The audit called this "the single most consequential defect in this engine", and it is closed. |
| **E6** | BOS must break a **major** level | **FIXED and made configurable.** The hardcoded `atr * 0.5` tolerance is now `neckline_major_atr_tolerance`; a pattern between 0.5x and the tolerance is admitted at reduced confluence rather than discarded. |

### B2.2 Fixed in this pass

| # | Requirement | What was wrong |
|---|---|---|
| **E8** | `invalidation_zone_source` supports right / left / both | The zone list was seeded with the right shoulder unconditionally and only `"both"` ever appended the left, so selecting **`left_shoulder` silently behaved as `right_shoulder`**. A config value that is accepted and then ignored is worse than one that is rejected — nothing in the result tells you it had no effect. Now branches on all three values. |

### B2.3 Still not conformant — the one that matters

| # | Requirement | Status |
|---|---|---|
| **X1 / X2** | **"Hard invalidation exit: if a candle body closes back beyond the Head level before TP1, flatten the position"** — spec §3.11, §7, and both instrument-class rows of the §7 table | **ABSENT IN-TRADE.** |

This is not a small gap, and it is not simply unimplemented — it is
**architecturally unreachable in the current design**:

- `invalidation_head` is computed and stored on the candidate (`engine.py:341`),
  and read exactly once, at `engine.py:443` — **before** entry, during the
  confirmation stage.
- The moment the signal fires, the candidate is removed from the list and the
  engine has no further contact with the position.
- `BaseStrategy` exposes `on_bar`, `on_tick`, `get_required_timeframes` and
  `notify_outcome`. **There is no bar-level hook for managing an open position.**
  Break-even and trailing are owned by the risk engine, not the strategy, so a
  strategy-specific exit rule has nowhere to live.

So APA currently holds every position to SL, TP or trail even after price has
closed back through the Head — the exact condition its own spec says invalidates
the thesis. Combined with the exit-ladder collision fixed on 2026-08-23, this is
why losers ran: nothing was allowed to cut them early.

**This needs a new hook, not a patch.** Proposed:

```python
class BaseStrategy:
    async def on_position_bar(
        self, symbol: str, timeframe: str, candles: pd.DataFrame,
        position: OpenPosition,
    ) -> TradeAction | None:
        """
        Called once per closed bar for each open position this strategy owns.
        Return a TradeAction to close/modify, or None to leave it alone.
        """
```

Called from the same place the risk engine already runs BE/trailing per bar, so
backtest and live share one call site (Part 12 rule 5: one implementation per
rule). APA then registers the Head level at entry and flattens on a body close
beyond it.

**Scope note, stated plainly:** this changes what every backtest produces for
APA, because trades that previously ran to SL will now exit earlier. That is the
point — but it means APA results before and after are not comparable, and the
existing corpus becomes a baseline rather than a continuation.

### B2.4 Lower-priority deviations, recorded not fixed

- **`sl_buffer_atr` and `sl_buffer_atr_mult` are summed** (`engine.py:305`) — two
  ATR multipliers for one buffer, doc specifies one. Effective 0.55xATR against a
  documented 0.05. Functional, undocumented, worth a decision.
- **Cost-floor stop override** — when the structural stop sits inside the cost
  floor, the stop is *moved* and the trade still taken, so the position's stop no
  longer coincides with its invalidation thesis. Recorded via `sl_floored` and
  costs 15 confluence points, but "skip the setup when geometry and economics
  disagree" is never offered as an option.
- **Confluence score components are invented** (`engine.py:107-159`) — reasonable
  and documented, but no spec sanctions the weights, and **the score gates
  nothing**: no code path rejects a low-confluence APA setup.
- **T2** — the forward-looking structural target the doc offers as a diagnostic
  is not emitted. It is the one field that would answer whether the fixed R-grid
  is mismatched to APA's actual reach.

---

## PART B3 — The finding that supersedes the exit-ladder work

Measured 2026-08-24, and it changes the diagnosis in Part B.2.

### B3.1 The exit ladder is not the binding constraint

`scripts/compare_exit_ladder.py` runs one window under four exit configurations
with identical data, identical signals and identical costs. Result:

```
variant                          trades   W   L     WR       P&L    TP1     SL
old: BE/trail at 1.5R (= TP1)        12   0  12     0%   -263.16      0     12
new: BE/trail at 2.0R, EITHER        12   0  12     0%   -263.16      0     12
TP-fill only (no R trigger)          12   0  12     0%   -263.16      0     12
no BE, no trail                      12   0  12     0%   -263.16      0     12
```

**Byte-identical, including the variant with break-even and trailing switched
off entirely.** An exit rule that changes nothing when disabled was never firing.

The break-even race described in Part B.2 was real and is fixed, but it was not
what produced the losses. It cannot have been: nothing ever got far enough to
trigger it.

### B3.2 Why — Maximum Favourable Excursion is ~0R

`scripts/diagnose_mfe.py`, same signals:

```
  #  dir      entry       stop     risk   MFE_R   MAE_R  bars  outcome
  1  BUY   4059.020   4052.646    6.374    0.11    1.32     5  SL
  2 SELL   4491.600   4499.726    8.126    0.00    1.13     1  SL
  3 SELL   4494.380   4502.963    8.583    0.08    1.69     2  SL
  4 SELL   4495.840   4505.155    9.315    0.00    1.40     1  SL
  5 SELL   4499.360   4509.194    9.834    0.00    1.12     1  SL

  median MFE 0.00R   mean 0.04R   best 0.11R
  reached 0.5R: 0/5    1.0R: 0/5    1.5R: 0/5
```

**Not one trade ever travelled a tenth of its own risk in profit.** Three of five
were stopped on the FIRST bar after entry, with price never ticking in favour at
all. This is not a thin edge — it is entries that are wrong immediately and
consistently.

### B3.3 The mechanism, read from the code

`strategy_apa/engine.py` around the AWAIT_RETEST -> AWAIT_CONFIRMATION handoff:

```python
# Step 3: Wait for candle BODY to enter the Invalidation Zone.
if body_bottom <= iz_top and body_top >= iz_bottom:
    c["status"] = "AWAIT_CONFIRMATION"
...
# Step 4: Confirmation — all 5 rules from §4 (run on same bar)
...
entry = latest["close"]
```

The sequence is:

1. Price retraces and a candle body **touches** the Invalidation Zone.
2. The confirmation rules run **on that same bar**.
3. Entry is that same candle's **close**.
4. The stop sits just beyond the zone the candle has only just entered.

The five confirmation rules check that the pattern is *not yet invalidated*
(no body close beyond the Head, stop on the correct side, non-degenerate SL/TP).
**None of them require price to have REJECTED the zone.** There is no demand for
a reversal candle, a wick rejection, a close back out, or any momentum shift.

So the engine enters *into a retrace that is still in progress*, with its stop
immediately behind it. A retrace that has not yet shown rejection usually
continues — and the stop is the first thing it reaches. That is exactly the
observed signature: MFE 0.00R, stopped on bar 1.

The trade is being taken one bar too early. The zone touch is treated as the
trigger when the spec (step 7 -> step 8) frames it as the *precondition* for
confirmation.

### B3.4 What to change — needs your decision

This is a strategy-logic change, so I have not made it. Three options:

1. **Require a rejection candle** — after the body enters the zone, wait for a
   candle that closes back out of it in the trade's direction. Latest entry,
   fewest trades, highest quality. Most faithful to "retest then confirm".
2. **Require a close beyond the zone edge** — enter only once a candle closes
   past the zone boundary on the correct side. Middle ground.
3. **Keep the entry, widen the stop** — place the stop beyond the whole retrace
   allowance rather than the shoulder wick. Keeps the trade count but degrades
   R:R, and does not fix a wrong entry, only survives it.

**My recommendation is (1)**, because MFE ~0R says the problem is entry timing,
not stop placement — a wider stop on an entry that never goes in your favour
just loses more slowly.

Whichever you pick, the markings pipeline now renders the zone, the retest bar
and the entry on the chart, so the change is verifiable visually rather than
only in aggregate statistics.

### B3.5 Consequence for Part B.2

The break-even and trailing fixes stay — the race was real and would have bitten
as soon as trades started reaching 1.5R. But they are **latent** fixes: they will
show no effect in any backtest until the entry problem above is addressed. Do not
read "the exit fix changed nothing" as "the exit fix was wrong".

---

## PART C — Symbol identity across brokers (A6)

`SYMBOL_ALIASES` currently maps spelling variants (`DAX`, `DE40`, `GER30` → `GER40`)
but assumes one broker. Your case is different: **GER40 (FundedNext) and GER30
(Deriv) are the same instrument under two brokers**, and 22 profiled symbols are
simply absent from Deriv.

### C.1 Design — canonical instrument + per-broker symbol map

```python
@dataclass
class Instrument:
    canonical: str              # "GER40" — the id configs and strategies use
    asset_class: str
    aliases: set[str]           # spelling variants, broker-independent
    broker_symbols: dict[str, str]   # {"Deriv-Demo": "GER30", "FundedNext": "GER40"}
```

Resolution becomes: **config names the canonical instrument → the active broker's
symbol map produces the tradeable symbol.** A strategy config written on
FundedNext then runs unchanged on Deriv, which is the actual requirement.

### C.2 Auto-discovery rather than a hand-maintained table
Hand-maintaining this per broker repeats the mistake the live profile overlay
just fixed. Instead, on connect:

1. Enumerate `mt5.symbols_get()`.
2. Match each against known instruments by normalised name + `path` + contract
   spec (an index with `contract_size=10` and `digits=1` named `GER30` or `GER40`
   is the same instrument).
3. Cache the map per broker; log every resolution; flag ambiguities for you
   rather than guessing.

### C.3 UI
A resolution strip on Settings → Broker: canonical name, this broker's symbol,
availability, and any unresolved instrument. Unavailable symbols get disabled in
every picker with the reason ("not listed by Deriv-Demo") instead of failing at
data fetch.

---

## PART D — Strategy Factory (A1)

The screen you asked for: **create and delete strategies from the dashboard, never
hand-code one again.**

### D.1 Flow

```
  Describe            Generate           Review            Activate
 ┌──────────┐       ┌──────────┐      ┌──────────┐      ┌──────────┐
 │ paste MD │──────▶│  Claude  │─────▶│  diff +  │─────▶│ registry │
 │ spec, or │       │  writes  │      │ preview  │      │ + branch │
 │ describe │       │ engine + │      │ on chart │      │ + PR     │
 │ in prose │       │ params   │      │          │      │          │
 └──────────┘       └──────────┘      └──────────┘      └──────────┘
```

1. **Describe** — paste the strategy's MD spec (the format `docs/strategy-*.md`
   already uses), or write prose. Attach fundamentals to use as inputs.
2. **Generate** — Claude writes `backend/strategies/strategy_<slug>/{engine,params}.py`
   against `BaseStrategy`, emitting `MarkingCollector` calls for every condition it
   tests, so the new strategy is chart-visible from its first run.
3. **Review** — full diff, plus a signals-only preview on the replay chart over a
   window you choose. **Nothing is written to disk until you approve the diff.**
4. **Activate** — registered in `strategies/registry.py`, appears in Backtester
   and live selectors, committed to a branch, PR opened against `dev`.

### D.2 Generation contract

Claude is constrained, not freehand:

- Writes **only** inside `backend/strategies/strategy_<slug>/`, never elsewhere.
- Must subclass `BaseStrategy` and implement `on_bar` / `get_required_timeframes`.
- Must return `TradeSignal` with `metadata={**mk.as_metadata()}`.
- Every parameter is a typed dataclass field with a docstring — this is what
  makes the schema-driven form and the tuning sliders work with no extra code.
- Generated code is **AST-validated and import-tested in a subprocess** before it
  is offered for review. A module that will not import never reaches you.

### D.3 Safety — stated plainly

This writes code into your repository and opens pull requests. Three hard rules:

- **No auto-merge, ever.** The PR targets `dev` and waits for a human.
- **No writes outside the strategy package**, enforced by path check, not by prompt.
- **Delete is a soft-delete**: deregistered and moved to
  `backend/strategies/_archive/`, with its own PR. Saved backtests referencing it
  keep working.

### D.4 Deletion semantics
Removing a strategy from the Lab removes it from the Backtester picker and the
live selector, and blocks the bot from starting it — but **does not** touch saved
runs or journal history, which must stay readable.

---

## PART E — Fundamentals on the chart, and in the strategy (A2)

This is the half that makes the panels worth having.

### E.1 A chart tab on every fundamentals panel
Each panel gains a **Chart** view beside its table view, rendering on the same
`ReplayChart` the backtest uses:

| Panel | On-chart representation |
|---|---|
| Order flow | Signed-volume bubbles at price; CVD in a sub-pane; VPOC and value-area bands |
| Depth | Resting-size heat strip at the right edge, with the imbalance ratio |
| GEX | Gamma-flip level and per-strike walls as horizontal bands, reprojected onto price |
| Correlation | Overlay of the correlated symbol, normalised, plus a rolling-correlation sub-pane |
| Calendar | Event markers on the time axis, coloured by impact |

### E.2 Strategy overlay on the same chart
A strategy selector on the Fundamentals page. Picking one runs its signal
generation over the chosen window and draws its markings **on the same chart as
the fundamentals** — which is exactly the "does my technical setup line up with
where gamma actually sat" question you described.

### E.3 Fundamentals as strategy inputs
A `FundamentalGate` block attachable to any strategy, in three modes:

- **Filter** — veto entries when the condition fails (e.g. no longs below the gamma flip)
- **Confluence** — contribute to `confluence_score` without vetoing (the Part 7 §7.3 rule: contributors, never a veto)
- **Trigger** — the fundamental itself opens the trade (a CVD-divergence entry, standalone)

Each gate declares its provider, so a backtest states plainly when the data was
unavailable for part of the window rather than silently skipping it.

### E.4 Backtestability — the honest constraint
Order flow and depth are **live-only**: MT5 serves recent ticks, not months of
history, and `market_book_get` has no history at all. So:

- **Backtestable**: correlation, calendar, GEX (where an archive exists).
- **Live/forward-test only**: order flow, depth.

A `FundamentalGate` in Filter mode over a window with no data must **fail the
backtest loudly**, not pass silently — a strategy that appears to backtest with a
gate that was never evaluated is worse than one that refuses to run.

---

## PART F — Help everywhere (A3)

Every panel gets a collapsible **"What this is / How to use it"** with: what the
number means, how it is computed, its caveat, and one concrete way to trade on it.
Highest priority, being the ones you flagged:

- **Volume by price** — signed volume per price level; VPOC is where the most
  traded; value area is the 70% band. Use: fade the edges, expect reaction at VPOC.
- **Depth "empty book"** — Deriv CFDs do not publish level-2 for most symbols;
  `market_book_add` fails or returns nothing. This is a broker limitation, not a
  fault, and the panel should say so rather than showing an empty table.
- **GEX ticker** — takes an **index code** (`SPX`, `NDX`, `RUT`), not a broker
  symbol (`SPX500`, `NDX100`). The picker should offer valid codes rather than a
  free-text field that 404s.
- **Correlation** — rolling return correlation from your own bars; use it to avoid
  stacking correlated risk across portfolio legs.

---

## PART G — Sequence

| Step | Work | Why this order |
|---|---|---|
| 1 | ~~Help accordions + GEX ticker picker + depth explanation (F)~~ **DONE 2026-08-25** | `HelpNote.jsx` + `FundamentalsHelp.jsx`; GEX ticker suggest-list with a broker-CFD warning |
| 2 | ~~Strategy Lab: free-text symbol, window picker, working preview (A4/A5)~~ **DONE 2026-08-25** | Free-text symbol with suggestions; date range with 30/90/210/365d presets, replacing the hardcoded 45 days |
| 3 | Symbol identity map + broker auto-discovery (C) | Unblocks cross-broker configs; GER30/GER40 |
| 4 | Fundamentals chart tabs (E.1) | The visual half you asked for |
| 5 | Strategy overlay on fundamentals chart (E.2) | Needs 4 |
| 6 | Strategy Factory: generate → review → activate (D) | Largest; needs the preview from 2 |
| 7 | `FundamentalGate` in strategies (E.3) | Needs 3 and 6 |
| 8 | Git branch + PR automation (D.1 step 4) | Last; least reversible |

---

## PART H — Decisions, answered 2026-08-23

| # | Decision | Implementation |
|---|---|---|
| 1 | **Move break-even later so TP1 gets paid first** | Done, then REVISED on your note that RR triggers should stay live. Final state: `be_mode`/`trail_mode` stay **EITHER**, and the fix is the THRESHOLD — `be_trigger_rr` and `trail_activation_rr` 1.5 -> 2.0, above `tp1_rr`. The bug was never the mode; it was two conditions on the same price where one is a level TOUCH and the other a limit FILL, and the touch wins by a bar. Separating them removes the race without removing a trigger — `TP_HIT` alone would never arm BE at all when TP1 does not fill. `validate_exit_ladder()` now flags coincidence (within 2%), not ordering. |
| 2 | **Position limit must be editable from the frontend** | Done. `max_positions_per_symbol` was sent to the backend but had no input (task 3.8/E5) — the request hardcoded `\|\| 1`. Now in the Backtester's Advanced panel alongside `allow_pyramiding` and `min_bars_between_entries`, plus `be_mode` / `trail_mode` selectors. Settings already exposed it for live. |
| 3 | **Full auto branch + pull request** | Planned in D.1 step 4. Hard rules stand: PR targets `dev`, **never auto-merged**, writes confined to `backend/strategies/strategy_<slug>/` by path check. |
| 4 | **User selects the generation model** | The existing `ModelPicker` already serves the full catalogue with per-model output ceilings, effort levels and pricing. Strategy Lab mounts the same component — no separate model list. |
