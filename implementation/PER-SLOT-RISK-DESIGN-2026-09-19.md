# Per-slot risk: design (2026-09-19)

**Status: option A approved by the owner on 2026-09-19 and built** (§10 says what
landed and what did not). Every limit is now the slot's own; the only shared
things left are the account's balance, the broker's margin and lot rules, and
prop-firm limits.

## 1. What the owner asked for

> "no general risk management engine… risk engine and risk management will be per
> symbol, per strategy… Maximum trade per day, risk per symbol, risk per trade, and
> everything… No general risk that all must abide by."

The rule also applies to the single backtest, the portfolio backtest and live
trading. A slot's result must be the same in all three.

## 2. Why a slot trades differently in a portfolio today

The portfolio backtest and the live bot run every slot through **one** `RiskEngine`
and **one** `CircuitBreaker`. A single backtest runs one slot through its own. The
shared state is:

| Shared state (risk/engine.py, risk/circuit_breaker.py) | Effect on slot B when slot A trades |
|---|---|
| `daily_pnl`, `weekly_pnl` → the `is_paused` latch | A's losses pause B for the rest of the day or week. |
| Daily/weekly drawdown budget, which includes `open_risk` | A's open trades **shrink B's position size** as the budget fills. |
| `daily_trades_count` vs `max_daily_trades` | A's trades use up B's daily allowance. |
| `open_positions_by_symbol` vs `max_concurrent_positions` | A's open trades block B's entries. |
| `min_rr` (a single value for the whole account) | A strategy whose measured target is below it is rejected. IVW_v1 at 1:2 was rejected under the shipped 3.0. Per-strategy `min_rr` reaches the single backtest and live, but **not** the portfolio entry gate. |
| Exit engine keyed by `strategy_id` (`_exit_engine`) | Two slots running one strategy on different symbols share exit settings. |
| Margin utilisation | Broker physics: the account really has one margin pool. |
| Balance-based sizing (`BALANCE`/`EQUITY`) | Compounding really is on one account balance. |

The last two rows are physical facts about the account and stay shared. The rows
above them are policy, and those become per slot.

Measured by replaying the Backtester's own per-slot trade streams on one account
(scratchpad balance/balance_sim.py). The eight slots were ORB GBPJPY/XAUUSD/US Tech
100/BTCUSD, VWAP XAUUSD/US Tech 100/BTCUSD and TrendDrift Crash 1000, Feb 2024 to
Sep 2026, at 1% risk.

* The portfolio's shipped shared limits (3%/6% drawdown stops, 5 trades a day, 5
  open) blocked **490 of 3,053 trades**.
* Return fell from +422% to +375%.
* The owner's live settings (20%/40%, 20 trades, 15 open) blocked none.

So the shared limits cost real trades only at the tight defaults. The rest of the
single-vs-portfolio gap is the size shrink, the `min_rr` gap and differing exit or
strategy parameters between the single form and the portfolio form. Each of those
disappears when a slot carries its own complete profile.

## 3. The model: a slot is its own book

```
Account (broker facts only)
  balance / equity, margin pool, leverage, min lot / volume step
  prop-firm limits (only when the account IS a prop account: the firm enforces them)
  balancing method (optional, §6)

Slot  (symbol × strategy): everything else
  strategy     full parameter set (measured values pre-filled)
  sizing       risk % per trade, sizing basis, max lot, min stop (× spread)
  entry limits trades/day, open positions, losses/day, bars between entries,
               pyramiding, session filter
  targets      tp count, R:R per target, splits, min R:R
  break-even   mode, trigger, buffer
  trailing     mode, method per target, trigger, ATR multiple, step
  breaker      daily loss %, weekly loss %, consecutive losses → pause N days
  allocation   weight (1.0 = its risk % as set; the balancing method moves it)
```

Each slot gets its own `RiskEngine` and its own `CircuitBreaker`, built from its
profile. No limit in one slot reads another slot's state. The single backtest is a
one-slot book on the same code path, so "single = portfolio leg = live" holds by
construction instead of by parallel copies.

## 4. Code changes

* `core/config_schema.py`: a `SlotProfile` dataclass (§3). `InstrumentSlot` becomes
  a complete profile instead of partial overrides of `RiskParams`. The trading
  fields leave `RiskParams`, and what remains is the account layer.
* `risk/slot_book.py` (new): `SlotBook`, which maps `slot_id → (RiskEngine,
  CircuitBreaker)`. It is built from the profiles, and every
  evaluate/open/close/manage call is routed by `slot_id`. Live persists breaker state
  per slot (`cb_state/<account>/<slot_id>.json`) where it now keeps one file.
* Both backtesters: `BacktestEngine` gets a one-slot `SlotBook`. The
  `PortfolioBacktestEngine` gets an N-slot book and replaces `self.risk_engine` and
  `_exit_engine`. Margin and balance stay at the account level.
* Live: `bot_service` evaluates through `book[slot_id]`, and `position_manager`
  manages each position with its slot's engine. The cached-`RiskEngine`-by-hash
  logic goes away.
* API: `/backtest` and `/backtest/portfolio` take slot profiles. The portfolio
  request's "shared across portfolio" risk block is removed.
* Removed as redundant: `use_measured_params`, `use_strategy_exit_defaults` (a new
  slot is pre-filled with the measured values, and after that its values are simply
  its values), the global `config.orb/vwap/...` strategy sections (each lives on its
  slot), the Backtester's "Load live settings" button (you pick a slot from the book
  instead) and `max_daily_trades_by_strategy`/`strategy_risk_budget_pct` (subsumed).

## 5. UI

* **Settings → Trading Book** (replaces Strategy Slots, the per-strategy parameter
  cards and the trading half of Risk). One table with a row per slot: symbol,
  strategy, risk %, trades/day, max open, losses/day, target, break-even, trail,
  breaker, weight and status. Clicking a row opens a drawer with the sections of §3.
  "Add slot" pre-fills the measured values for that symbol and strategy.
* **Settings → Account**: broker and margin, prop-firm mode, balancing method.
  Nothing that gates an individual trade.
* **Backtester, single**: choose a slot from the book (or "new"). The same drawer
  opens inline, with a "Save to book" button. Nothing needs loading or resetting,
  because the form *is* the slot.
* **Backtester, portfolio**: tick slots from the book, and each row opens the same
  drawer. There is a balancing-method selector. Results show each slot, and the
  combined result sits next to the sum of the slots' single runs, so any difference
  is visible.
* **Dashboard (live)**: a status line per slot: today's trades against its limit,
  its P&L, its breaker state and its current weight.

## 6. Balancing

This is a *multiplier* on each slot's own risk %, never a gate. The balancing test
used the same eight streams, $10k at 1% compounding, sizing only from trades
already closed:

| Method | 8 slots, one broken | 7 healthy slots | 8 slots, first half | 8 slots, second half |
|---|---|---|---|---|
| Equal (1/N) | +422%, DD 43% | **+1,784%**, DD 26% | +100%, DD 43% | +160%, DD 32% |
| Account drawdown scaling | +117%, DD 22% | +769%, DD 15% | +52%, DD 22% | +38%, DD 18% |
| Pause a slot while its last 20 trades < 0 | +369%, DD 36% | +482%, DD 27% | +139%, DD 25% | +101%, DD 36% |
| Momentum (top third 1.5×, bottom 0.5×) | +1,411%, DD 47% | +1,925%, DD 28% | +183%, DD 47% | +385%, DD 27% |
| **Slot drawdown brake, 30R** | **+1,035%, DD 22%** | +1,237%, DD 23% | **+260%, DD 22%** | +186%, DD 22% |

The brake shrinks a slot's size linearly with that slot's own drawdown, to a
quarter at 30R (Grossman-Zhou, applied per slot). It is the only method that is
good in every column. It cuts the damage from a broken slot by about 60%
(TrendDrift Crash 1000 lost 108R), costs about 30% of the return when every slot
is healthy, and needs nothing from any other slot, so it fits §3.

Momentum made the most money in some columns but doubled the worst month. It also
ranks slots against each other, which is a cross-slot rule.

**Shipped off (`slot_brake_r = 0`), armed per slot.** It is the method worth
having, but switching it on changes what every existing slot earns — measured on
IVW/EURUSD, a 30R brake took +$1,387 to +$1,323 on the same 47 trades — and this
redesign was meant to leave returns alone. Set `slot_brake_r` to 30 on a slot
(Settings > Strategy > Risk) to arm it there. The slot picks behind the table
were made on overlapping data, so read the ranking, not the percentages.

## 7. Migration and proof

* Migration: each existing slot's profile = its effective values today (global
  `RiskParams` ⊕ slot overrides ⊕ measured strategy defaults where they applied). A
  slot's own trades do not change on day one. What changes is that other slots stop
  gating them.
* Parity tests that must pass before live switches over:
  1. Slot X run alone gives the same trades as slot X inside a portfolio, with
     static sizing and ample margin, trade for trade.
  2. A two-slot portfolio equals the union of the two single runs.
  3. The live replay (check_live_backtest_parity) equals the backtest, per slot.
  4. Migrating a saved config and running a slot alone gives the same trades as
     today's single backtest of that slot.

## 8. The decision the owner made

With no account-level policy at all, nothing stops every slot losing its daily limit
on the same day. The worst day is the sum of the slots' daily limits. Options:

* **A. Pure per-slot** (as requested). Keep only the broker facts and prop-firm
  limits at the account level.
* **B. Per-slot plus one account "kill switch"**: a single number such as "flatten
  and stop for the day at −X% of balance". It never shrinks sizes or blocks
  individual entries, and it only ends the day.

**Chosen: A.** The account has no risk rule of its own. The combined worst day is
the sum of the slots' own daily limits, which the Settings slot panel and the
dashboard's per-slot row make visible rather than hidden.

## 9. Phases

1. Backend `SlotProfile` + `SlotBook`, both backtesters on it, parity tests 1, 2
   and 4.
2. Live on the `SlotBook`, per-slot breaker persistence, migration, parity test 3.
3. UI: Trading Book, Account, the Backtester slot editor, portfolio grid and
   dashboard.
4. The balancing multiplier, if §7 shows a method that earns its place.


## 10. What landed (2026-09-19)

**Backend**
* `risk/slot_book.py` — `resolve_slot_risk_config` (the one resolver every path
  uses) and `SlotBook` (a RiskEngine + CircuitBreaker per slot, live state kept
  across settings changes, closes routed to the slot that opened them).
* `risk/circuit_breaker.py` — one state file per slot, and each slot's realised
  record in R (`cum_r`, `peak_r`, `drawdown_r()`).
* `risk/engine.py` — the per-slot drawdown brake (`slot_brake_r`, default 30R,
  floor 0.25), applied to the slot's own drawdown only; `min_rr_by_strategy`, so
  a strategy's measured target can lower the R:R gate for that strategy alone.
* `backtester/portfolio_engine.py` — every leg's entries, exits, sizing and
  accounting go through its own slot engine. Two reporting differences with the
  single engine were fixed on the way: a leg's `entry_time` is now the FILL bar
  (it was the signal's bar, one bar early) and the forced close at the end of a
  run is `END_OF_DATA` in both engines.
* `api/routes/backtest.py` — `BacktestRequest.slot_risk` and
  `PortfolioSymbolConfig.risk`, both resolved through the shared resolver.
* `services/bot_service.py` — the live bot runs each slot on its own engine and
  breaker; `services/position_manager.py` manages a position under its slot's
  exits; `api/routes/system.py` reports `slot_breakers`, and the reset endpoints
  act on every slot.
* `core/config_schema.py` — `InstrumentSlot.risk`, a slot's own settings block.

**UI**
* Settings > Strategy: a "Risk" panel on each slot row — sizing, entry limits,
  targets, break-even, trailing and the brake, each field showing the account
  default it falls back to.
* Backtester: a run carries the slot's own saved risk ("Slot's live risk"),
  which replaced the "Load live settings" button; each portfolio row shows the
  profile it will run under.
* Dashboard: a row per slot — trades today against its own cap, its P&L, the
  level it stops itself at, its drawdown in R, and whether it has paused itself.

**Proved by** `tests/test_slot_risk_parity.py` (10 tests): a slot trades
identically alone and in a basket; one slot's daily limits never touch another's;
a daily cap is counted per slot; each slot keeps its own targets and size; the
brake's shape at 15R and 30R; and the single route, the portfolio row and live
all resolve one slot profile to the same config.

**Not done yet** (deliberately, to keep the change reviewable):
* `use_measured_params` and `use_strategy_exit_defaults` still exist; the slot
  panel supersedes them but removing them is a separate cleanup.
* The Trading Book table as a page of its own — the per-slot panel lives on the
  existing Strategy Slots list instead.
* The balancing multiplier beyond the per-slot brake (momentum tilting, §6).
* Live breaker state starts fresh per slot: the old single `cb_state.json` is not
  migrated, so on the first deploy each slot's daily counters begin at zero and
  open positions are recounted from MT5.


## 11. Audit (2026-09-20)

Run against a copy of the database with the real backend and the real UI, plus
the test suite. What it found, and what was done:

| Found | Fix |
|---|---|
| **The Backtester page crashed** — `Cannot access 'userCfg' before initialization`: the slot-risk helper was declared above the query it reads. The page rendered the error boundary instead of the form. | Helper moved below its dependency. Caught only by loading the page, not by lint or the build. |
| `/api/account/state` still read the single `cb_state.json`, so it reported "no risk state" and could no longer tell a stale account's state apart. | Reads every per-slot file and summarises them; `/api/account/reset` clears them too; the Broker page row says how many slots. |
| `SlotBook.reconcile_from_mt5` paired slots with breakers **by position** in two lists, and the engines are built lazily, so a slot could be handed another slot's open positions. | Keyed by slot (`circuits_by_key`). |
| The startup reconcile ran before the slot book existed, so it silently did nothing — a stale open-position count would have blocked that slot's next entry indefinitely. | `_reconcile_slots_with_mt5()`, run once on the first scan after start. |
| The Backtester decided the measured-exits switch from its own form copy, which is seeded once and then goes stale: a backtest could apply different exits from live. | Both runs take the saved Settings value whenever the run follows the slot's live risk. |
| The slot Risk panel showed "account default —" for every field: the Strategy page's config never carried the `risk` block. | It carries it read-only, and strips it from its own save so this page can never overwrite the Risk page. |
| `slot_brake_r` existed only as a slot key, so it had no account default and no home in Settings. | Added to `RiskParams` (0 = off) and to the Risk page, and it reaches live through `build_live_risk_config`. |
| A dead `CircuitBreaker` import and a comment describing the old rebuild in `bot_service`. | Removed. |
| `tests/test_vol_target.py::test_binding_is_reported_accurately` was already failing before this work: its "flat" fixture has log-returns whose variance underflows to 0, which the code reports as insufficient data. | Test split into a genuinely quiet series (ceiling) and a dead-flat one (insufficient data, size unchanged). |

Checks that came back clean:

* All 47 fields on Settings > Risk reach the live engine.
* Every risk key live uses is reachable from the Backtester, so a backtest can
  match live on all of them.
* A slot edited in the UI resolves to exactly that slot's engine: 0.35% and 2
  trades a day on one slot while the others stayed at the account's 1% and 7.
* Through the real API: a single IVW_v1 run sized at the slot's 0.5% (a stop-out
  cost $51.61 on $10k, not $100), and a two-row portfolio ran both rows with
  their own risk, their own records, and the per-strategy min R:R floor letting a
  1:2 strategy trade under an account gate of 3.
* Every page that changed renders: Settings (Strategy, Risk, Broker), Backtester
  (single and portfolio), Dashboard.
* Test suite: 588 pass, 0 fail.


## 12. The screens, rebuilt (2026-09-20)

§5 described the UI; this is what was actually built, after the first attempt
left the portfolio backtest and the strategy parameters untouched.

**The problem with the old screens.** Strategy parameters were GLOBAL blocks:
one `orb` block, one `vwap` block, and so on. Every slot running that strategy
shared them, so ORB on gold could not use a different range or timeframe from
ORB on GBPJPY — changing one changed both. Each screen then stacked all nine
strategies' parameter cards whether or not they were in use, and the risk that
applied to them lived on a different page again. The portfolio backtest sent
those same global blocks for every row.

**One editor, three places** (`components/SlotEditor.jsx`, fields generated from
`/config/parameter_schema` so they can never drift from the dataclasses):

* a slot's header is symbol + strategy + a one-line summary of what it will do;
* **Strategy** tab: only that strategy's parameters, applying to that symbol
  only, with the measured-settings switch;
* **Risk & exits** tab: sizing, entry limits, targets, break-even, trailing,
  grouped, each field showing the account default it falls back to and marked
  when the slot overrides it;
* rows collapse to one line, so a book of slots is a list, not a wall.

It is used by **Settings > Trading Book** (what the bot trades), the
**single backtest** (the one slot under test), and **every portfolio row**.

**Removed as redundant**, not hidden:

* the ~300-chip "Active Symbols" grid (adding a slot now picks the symbol);
* nine global strategy parameter cards in Settings (916 lines → 428);
* the Backtester's hand-written per-strategy panels, its stacked "Per-Strategy
  Exits" and "Strategy Engine Parameters" sections, and its duplicate Risk %,
  Hard Cap, Min R:R and TP Count inputs — the slot owns those now;
* `buildPortfolioStrategyParams`, `StrategyDefaultsPanel`, `SLOT_RISK_LABELS`,
  the global strategy blocks in `DEFAULT_FORM`, and the "Slot's live risk"
  toggle (the editor already shows what will run).

**Backend to match**: `PortfolioSymbolConfig` carries `strategy_params`, `risk`
and `use_measured_params` per row; `BacktestRequest` carries `slot_risk` and
`use_measured_params`; `apply_strategy_params` honours that switch on both
backtest paths (live already did); `BoomDriftJumpParams` was missing from the
schema, so Boom slots had no editable parameters at all.

**Proved by running it**: a two-row portfolio with ORB on both rows, a 30-minute
range on XAUUSD and 60-minute on GBPJPY, with 0.2% and 1% risk — one run, 29
trades, each row on its own engine and its own record. The single tab sends its
slot the same way, and switching symbol or strategy re-seeds from the slot saved
for the new pairing instead of carrying the old parameters over.

**Layout**: the slot row wraps instead of overflowing (its buttons used to sit
outside the card once the window narrowed), and at 375px the page has no
horizontal scroll.

## 13. Second pass on the screens (2026-09-20)

Three things the owner found after the first pass, and what each one really was.

### 13.1 "What is the Advanced Parameters accordion for?"

Nothing, and worse than nothing. It still held a global copy of risk %, entry
caps, TP ladder, break-even, trailing, pyramiding and the profit halts — every
one of which is now resolved per slot. On a portfolio row the slot's value wins
(`resolve_slot_risk_config`), so the panel looked live while changing nothing.
Deleted (≈15.7 KB of JSX). The run still sends the account base from the saved
Settings values; it is just no longer editable in two places.

### 13.2 "Why are there duplicate strategy parameters for ORB and CRT in Defaults?"

Same class of bug, one step worse. `Settings → Defaults` carried a "Strategy
Parameters" card with VWAP, APA and **CRT** blocks. VWAP/APA duplicated what
each slot now owns; CRT configured a strategy the backend does not have at all —
`list_strategies()` has no CRT and `UserConfig` has no `crt` field, so those
three inputs wrote a key nothing has ever read back.

The page was rewritten (850 → 410 lines) as what it actually is:

| Card | Holds | Source |
|---|---|---|
| Defaults every slot starts from | the slot's own sections, rendered from `SLOT_RISK_SECTIONS` | same schema rows the slot editor uses |
| Account & broker | `max_account_leverage`, `max_margin_utilisation_pct` | `ACCOUNT_ONLY_KEYS` |
| Prop firm | unchanged | `PropFirmParams` |

Because both the page and the slot editor build their fields from the same
list, a field cannot appear in one and be missing from the other.

It also saves less: `{risk, prop_firm}` only. Verified against the running API —
a save changes the one edited field and leaves `instrument_slots`, `orb`, `apa`,
`vwap` and every other block byte-identical.

### 13.3 Fields moved to where they are enforced

`max_concurrent_positions`, `target_profit_enabled`, `max_daily_profit`,
`max_weekly_profit` are read by the **slot's** `CircuitBreaker`, and
`min_sl_pips` by the **slot's** `RiskEngine` — so they are slot fields now
(`SLOT_RISK_SECTIONS`, new "Profit halts" section). `max_margin_utilisation_pct`
went the other way, into `ACCOUNT_ONLY_KEYS`: `position_sizer` clamps each trade
to that share of account equity, so a slot changing it would misdescribe the
broker.

### 13.4 Symbols: suggest, never restrict

The slot header used a `<select>` built from a hardcoded list of canonical names
and aliases. Deriv lists the Nasdaq as **"US Tech 100"**, which is in neither —
so the symbol could not be chosen at all. Every symbol field (single backtest,
each portfolio row, each live slot, and the add-slot row) is now the same
free-text `SymbolPicker`, fed by `useSymbolOptions()`:

1. the connected broker's own symbols (`/broker/instruments`),
2. the symbols this config already uses,
3. the static fallback list (now including broker names).

Typing a symbol none of them knows is accepted; the list only helps you find one.

### 13.5 The app had no navigation on a phone

Below 768px the sidebar is a drawer, and the only control that opened it was the
collapse chevron — which is pinned to the panel and slides off-screen with it.
Every page rendered at 375px and none of them could be left. Added a fixed
opener, a scrim, and close-on-navigate derived from the route (no effect, so no
cascading render). Guarded by two tests.

## 14. Why a DriftJumpAlpha run found 0 signals (2026-09-21)

A live run: DJA on Crash 1000, 2026-01-01 → 2026-09-21, $900, 5% risk.
**0 trades from 77,107 bars**, funnel: `daily_risk_cap (77107 candidates)`.

### 14.1 The strategy ran parameters the user never chose

DJA's first guardrail is
`daily_risk_used_pct + config.risk.risk_per_trade_pct <= params.max_daily_risk_pct`,
checked before any signal is formed. Settings had `max_daily_risk_pct = 20`
(and `max_trades_per_day = 20`, `trade_jumps_enabled = on`), which passes at 5%.
The engine ran the **dataclass defaults** — 4.0 / 6 / off — so `0 + 5 <= 4` was
false on every bar of the run.

Cause: both backtest routes build a fresh `UserConfigV2()` and write only the
slot's explicit overrides onto it. Anything set in Settings but not overridden
on the slot reverted to a default. `bot_service` builds a live engine from
`UserConfigV2.from_dict(saved)`, so **live and backtest ran different
parameters for the same slot**. Measured on the audit copy:

| | max_daily_risk_pct | max_trades_per_day | trade_jumps_enabled |
|---|---|---|---|
| Saved in Settings | 20 | 20 | on |
| Engine, before | **4.0** | **6** | **off** |
| Engine, after | 20 | 20 | on |

Fixed with `load_saved_strategy_blocks()` + `seed_strategy_blocks()`, called by
both routes before `apply_strategy_params`, so the order matches live exactly:
saved block → measured per-symbol table → slot override.

Until 2026-09-19 the frontend hid this by posting whole strategy blocks in the
payload. Moving parameters onto the slot removed that accident, and the gap
became visible — the P1.12 failure mode in a new form.

### 14.2 The same gate read the wrong risk number

`config.risk.risk_per_trade_pct` was the REQUEST's value, while the sizer used
the slot's resolved config, so a portfolio row that overrode risk was gated on a
number it never traded with. `apply_resolved_risk_to_strategy_config()` now
mirrors the resolved slot risk onto the config the strategy reads, on both
routes.

### 14.3 A failed run claimed to still be running

Reproducing it surfaced a worse bug. The data fetch failed in under a second
("MT5 returned no data"), and the handler broadcast over the websocket and
returned **without persisting anything** — so `/api/backtest_status` kept
answering `{"status": "running", "stage": "Fetching historical data...",
"pct": 5}` indefinitely. The page showed a run that never finished, and every
later run was refused with *"A backtest is already running for this user."*

Two fixes: failures persist an error state with the reason, and each task
stamps a `heartbeat` so a run that stops reporting for `STALE_RUN_SECONDS`
(15 min) no longer blocks a new one.

### 14.4 What a real DJA run costs

Measured, 500-bar window, this machine:

| Strategy | per bar | 77,107 bars |
|---|---|---|
| ORB_v1 | 0.17 ms | ~12 s |
| DriftJumpAlpha_v1 | 16.0 ms | **~21 min** |

Profile of DJA's `on_bar`: `MarketStructureDetector.update` 38% (it rebuilds
every swing over the whole window on each bar), four `DataFrame.__setitem__`
column assignments 20%.

The 58-second run in the report above is not a baseline: it was blocked at the
first gate, before any indicator work. Now that the gate passes, the same window
does the real simulation. Making DJA's per-bar work incremental is the open
follow-up.
