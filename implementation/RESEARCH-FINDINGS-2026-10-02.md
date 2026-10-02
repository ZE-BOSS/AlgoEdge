# Findings — new synthetics, backtest validation, and the three new strategies

**2026-10-02** · everything established so far against your 2026-10-01 request,
and an honest account of what is not done.

---

## 0. Status of every item you asked for

| # | Request | Status |
|---|---|---|
| 1 | Backtest `Vol over Crash/Boom` × DriftJump, TrendDrift, TrendBreakout | **Running** — far slower than estimated, see §7 |
| 2 | Validate the saved backtest data; find any calculation error | **Done** — §1. Not a calculation error |
| 3 | DEX: research, and build a strategy if possible | **Done** — §3. No timing edge exists; well-powered negative |
| 4 | A scalping strategy for the majors | **Scoped** — §4b. Only 4 markets can carry one |
| 5 | Remove IVW / OvernightSession / OpeningDrive / HTFFVGFlip | **Done** — deleted 2026-10-02, 12 strategies remain |
| 6 | Time-series momentum: research + test | **Researched** (§4). Not measured — needs a different data export |
| 7 | FLOD / LLOD: research + test | **Done** — §5a, §5c. Testable core is negative on real markets |
| 8 | ICC: research + test | **Done** — §5b, §5d. HTF bias is worse than a coin flip |
| 9 | Per-asset table: balance, DD, PF, P&L $, return %, WR | **Blocked on #1** |

Two bugs were found along the way that you did not ask about and that matter
more than most of the above: §2.

---

## 1. Can the saved backtest numbers be trusted?

**Yes, the arithmetic. No, the headline.**

| Saved run | Trades | WR | P&L |
|---|---:|---:|---:|
| Vol over Crash 750 | 402 | 37% | $306,891 |
| Vol over Crash 550 | 429 | 31% | $215,138 |
| Vol over Boom 550 | 299 | 31% | $9,398 |
| Vol over Boom 400 | 301 | 33% | $8,083 |

### It is compounding, not a pip error

$316,891 from $10,000 over 402 trades is 31.7x, needing a compounded gain of
**0.863% per trade** — which at 1:3 with a 37% win rate is **1.80% risk**, an
ordinary setting. Run it forwards instead:

```
2% risk, 1:3, 37% win rate:   +2.22% - 1.26% = +0.96% per trade
compounded over 402 trades:   46.6x  ->  $465,624
```

Your simulation returned **less** than the naive model predicts. No pip, tick or
contract-size error is needed to explain the figure — and none exists: all six
`Vol over` symbols verify at **$1.00 per unit of price per lot** against the
terminal's own `order_calc_profit`.

### Why it is still not a number to plan with

- It assumes the edge survives **402 consecutive trades** with no regime change.
- Final position size is ~32x the first. Max lot, margin and liquidity bite long
  before that and none are modelled.
- The result is dominated by its **last few trades** — largest size, least
  evidence.
- 37% at 1:3 means long losing runs. The **percentage drawdown** decides whether
  the account survives; a dollar headline hides it.

**Read these at flat risk.** `run_synthetic_study.py` reports every run twice —
compounded (what the app shows) and flat (fixed dollar risk off the opening
balance). Flat compares strategies; compounded compares exponents.

### What I could not do

Audit those four runs **trade by trade** — they are in the VPS database and the
local copy stops at 2026-08-30. `scripts/audit_saved_backtest.py` already does
the work (ledger consistency, dollars-per-point stability across trades, realised
vs intended risk, flat re-pricing). **A copy of the VPS `algoedge.db` is the only
thing missing.**

---

## 1b. The VPS audit — the four runs, trade by trade

With the VPS database the runs could finally be opened up. **Settings were 5.0%
risk per trade**, sizing on BALANCE (compounding); the Crash runs opened at
$10,000 and the Boom runs at $2,000.

### The arithmetic is clean

On all four: `$ per 1.0 price move per lot` is **1.0000 median, and MT5 agrees**.
Ledger sums match the saved totals exactly. **No pip, tick or contract error
exists in any of them.** That question is closed.

### What the runs actually rest on

| Run | n | WR | Net | **less top 5** | **less top 10** | median R | mean R |
|---|---:|---:|---:|---:|---:|---:|---:|
| Vol over Crash 750 | 402 | 37% | $306,891 | $214,367 | $140,821 | −1.02 | +0.44 |
| Vol over Crash 550 | 429 | 31% | $215,138 | $127,345 | $51,769 | −1.02 | +0.23 |
| **Vol over Boom 400** | 301 | 33% | $8,083 | **−$2,395** | **−$9,646** | −1.02 | +0.28 |
| **Vol over Boom 550** | 299 | 31% | $9,398 | $1,402 | **−$5,651** | −1.02 | +0.21 |

**Both Boom runs are a handful of trades away from losing money.** Remove the
five best of Boom 400's 301 trades and it is −$2,395. That is not an edge; it is
a lottery ticket that happened to pay, and it will not repeat on demand.

The Crash runs survive dropping the top ten and are the only two worth a second
look — though even there the top five are 30–41% of the result.

**The median trade on all four runs is −1.02R.** More than half of every run is a
full loss, and the mean is carried entirely by a minority of 3R winners (best
trade is +3.0R on all four — the target caps it).

### Flat risk, which is the honest comparison

| Run | compounded | at fixed risk |
|---|---:|---:|
| Vol over Crash 750 | $306,891 | **$87,517 (+875%)** |
| Vol over Crash 550 | $215,138 | **$48,672 (+487%)** |
| Vol over Boom 400 | $8,083 | **$8,553 (+428%)** |
| Vol over Boom 550 | $9,398 | **$6,149 (+307%)** |

### Two things the audit turned up that need explaining

- **Position size saturated.** Crash 750 used lots with median 20.0 and max 20.0;
  Crash 550 median 50.0, max 50.0. Most trades sat on a ceiling, which is why
  *"risk taken / risk asked"* has a median of **0.25x** — the runs were not
  really risking 5%, they were risking about 1.2% once the cap bound.
- **Balance-chain breaks**: 175 of 402 and 184 of 429 trades on the Crash runs
  where `balance_after != balance_before + pnl`, against 1 and 2 on the Boom
  runs. Most likely concurrent positions interleaving, but the asymmetry is
  large and unexplained. Worth confirming before trusting the equity curve.
- The audit script prints the win rate as "0.4%" — it reads a stored fraction as
  a percent. Cosmetic, in the script not the data.

---

## 2c. All password hashing was broken — FIXED

The 41 red tests had **one** cause, and it was not 41 bugs.

`passlib` 1.7.4 (last released 2020) is broken against `bcrypt` 5.0.0. It reads
`bcrypt.__about__.__version__` for backend detection, that attribute no longer
exists, detection fails, and it falls through to a path bcrypt 5 rejects:

```
CryptContext(schemes=["bcrypt"]).hash("short-pass-123")
ValueError: password cannot be longer than 72 bytes
```

That is a **14-character** password. Every hash and every verify raised, so
**registration and login were broken anywhere bcrypt 5 is installed** — not just
in tests.

Fixed by dropping passlib for `bcrypt` directly (`backend/core/passwords.py`).
The hash format is identical (`$2b$`), so **existing stored hashes still verify
and nobody resets a password.** Pinning bcrypt backwards would have hidden a live
production fault behind a green suite.

---

## 4b. Scalping: a cost problem before it is a strategy problem

Round-trip cost as a share of R, at a stop of one M5 bar range:

| Viable | | Marginal | | Uneconomic | |
|---|---:|---|---:|---|---:|
| US Tech 100 | 5.8% | Vol over Crash 750 | 16.6% | Crash 1000 | 35.1% |
| Step Index | 7.6% | Volatility 75 | 19.5% | GBPJPY | 39.8% |
| XAUUSD | 8.1% | BTCUSD | 26.5% | Boom 1000 | 41.8% |
| Vol over Boom 400 | 9.3% | DEX 600 DOWN | 29.7% | Vol over Crash 550 | 45.0% |
| | | | | EURUSD | 59.7% |

A Crash 1000 scalp hands **35% of its risk to the spread** before the idea has a
chance. EURUSD hands over 60%. No entry signal recovers that, which is why a
scalper should only ever be built on **US Tech 100, Step Index, XAUUSD or Vol
over Boom 400** — and why several strategies in this project's history looked
profitable measured on price and lost money in cash.

---

## 2. Two bugs found on the way

### 2a. Volatility 75 Index was 100x wrong — FIXED

MT5 reports `trade_tick_value` 0.0001 against `trade_tick_size` 0.01, implying
**$0.01** per unit of price. The terminal's own calculator disagrees:

```
lots 1.0  move   0.01  ->  $0.01
lots 1.0  move   1.00  ->  $1.00     <- tick_value should be 0.01, not 0.0001
lots 1.0  move 100.00  ->  $100.00
```

The app trusted the spec field, so **every V75 P&L computed with MT5 connected
was 100x too small.**

It only bit where MT5 is live. Offline research falls through to
`InstrumentProfile`, which is correct — which is why the whole historical corpus
looks sane and **only the VPS was wrong.** That asymmetry is why it survived, and
why an offline-only test would never have caught it.

Fixed in `d946583`: `_verified_tick_value()` checks the spec against
`order_calc_profit` once per symbol and trusts the calculator on a >1%
disagreement. **19 symbols checked; V75 was the only liar.** If the calculator is
unreachable the spec is returned untouched — this must never break sizing.

### 2b. An unresolvable symbol produces *no trades*, silently

Any symbol MT5 cannot resolve falls to `source=DEFAULT`, carrying
`tick_value 1.0 / tick_size 1e-05` — **$100,000 per unit of price**. It does not
inflate results: the sizer refuses to size on DEFAULT and returns zero lots. It
returns **an empty backtest with no error.** If a run ever comes back empty for
no visible reason, check this first.

---

## 3. DEX: the "every 10 minutes" is an average, not a schedule

Deriv labels these *"Small spikes and major drops every 10 minutes on average"*,
which reads like a timetable. It is not one.

Gaps between large moves, Jan–Oct 2026, **78,895 M5 bars per symbol**, at four
definitions of "large":

| Symbol | 1.5σ | 2σ | 3σ | 5σ |
|---|---:|---:|---:|---:|
| DEX 1500 DOWN | 1.03 | 1.00 | — | 0.98 |
| DEX 1500 UP | 0.97 | 1.00 | 0.99 | 0.99 |
| DEX 600 DOWN | 1.00 | 0.96 | — | 0.97 |
| DEX 600 UP | 0.96 | 0.96 | 1.00 | 0.95 |
| DEX 900 DOWN | 1.03 | 0.97 | — | 1.02 |
| DEX 900 UP | 0.98 | 0.99 | 0.97 | 1.01 |

The figure is the **coefficient of variation of the gaps**. A scheduled event has
CV near **0** — you can set a clock by it. A memoryless (Poisson) process has CV
of exactly **1.00**.

Every cell is 0.95–1.03, at every threshold, on all six symbols. **The process is
memoryless: time since the last drop tells you nothing about the next one.**

**So there is no timing edge on DEX, and a scalper built around the clock would
be trading noise.** That is a well-powered negative — six symbols, four
thresholds, ~79k bars each — and worth more than the strategy would have been.

It does **not** rule out a directional edge (the DOWN indices may drift up and
drop like Crash). That is in the queued study, not assumed here.

---

## 4. Time-series momentum

Anchor: **Moskowitz, Ooi & Pedersen, "Time Series Momentum", *Journal of
Financial Economics* 104(2), 2012, 228–250.** Read from the source, because most
retail write-ups get the sizing wrong.

### The rule, exactly

> "we consider whether the excess return over the past k months is positive or
> negative and go long the contract if positive and short if negative, holding
> the position for h months. We set the position size to be inversely
> proportional to the instrument's ex ante volatility."

- **Signal**: sign of the instrument's own excess return over the past *k*
  months (k = 12 headline). Nothing else — no filter, no confirmation.
- **Hold**: *h* months (h = 1), rebalanced monthly.
- **Size**: scaled to constant **40% annualised ex-ante volatility**
  (`40%/σ_{t-1}`). The paper calls 40% "inconsequential" — but the *scaling* is
  not optional; it is what lets 58 instruments be summed.

### Sample and result

24 commodities, 12 cross-currency pairs, 9 equity indexes, 13 government bond
futures — **58 instruments, Jan 1965 – Dec 2009.** Significant momentum in
**every one**. Predictability runs **1–12 months then partially reverses**. The
diversified portfolio sits near **12% annualised volatility**.

### Why it will not transfer as written

1. **The alpha is largely a diversification result.** The headline averages 58
   weakly correlated bets. A handful of CFD symbols is not that portfolio, and
   the paper's own per-instrument Sharpes are far below the diversified figure.
2. **It is monthly, over 45 years.** January-to-date is **nine monthly
   observations** — nowhere near enough to confirm or reject it.
3. **The reversal past 12 months is part of the finding.** Longer lookbacks are
   worse, not safer. Optimising the lookback upward on a short sample finds the
   reversal and mistakes it for a parameter.

**The honest test** is the rule as specified across every market the account can
reach *at once*, scored as one portfolio. It needs daily bars going back years —
a different export from the M5 set used here.

---

## 5. The two ICT systems

### 5a. FLOD / LLOD — not testable as stated

FLOD = **First Low Of Day**, LLOD = **Lowest Low Of Day** (mirrored by FHOD /
HHOD). They are not a strategy; they are a **choice of which PD array to trade
from**, inside the wider ICT framework in your transcript (mitigation block,
breaker block, order block, fair value gap, each requiring an overlapping FVG to
be "valid").

The problem is the selection rule. From the ICT material itself:

> "When to trade from the FLOD → When the Draw on Liquidity is closer to the
> current price and when there is minimal resistance (opposing PD arrays) on its
> way to DOL."

**"Draw on Liquidity"** and **"minimal resistance"** are discretionary
judgements, not computable predicates. Two traders applying this to the same
chart will disagree, which means a backtest of it measures whoever wrote the
code, not the method. The same source also says *"We don't use swing high/low to
trade from"* — so even the anchor is contested within the methodology.

**On the $24,000-in-a-month claim**: a single month, one account, no trade log,
no starting equity verified, no drawdown. That is not evidence of anything, in
either direction. I can neither confirm nor refute it and would not try.

**What I would test instead** — the one falsifiable fragment: *after a liquidity
sweep of the prior day's high or low, does price revert to the nearest fair value
gap more often than chance?* PDH/PDL and FVGs are both computable. That is a
real question with a yes/no answer, and the FVG machinery already exists in the
codebase (`HTFFVGFlip_v1`, `BiasIFVG_v1`). Say the word and I will run it.

### 5b. ICC — testable, with one rewrite

**ICC = Institutional Cash Cycle.** Three phases:

1. **Indication** — a move breaks a major swing high/low on 1H/4H, setting bias.
2. **Correction** — a pullback against that bias, gathering liquidity.
3. **Continuation** — price resumes the HTF direction.

Entry: wait for the pullback to **sweep liquidity**, then a **CHoCH** (change of
character — a break of the correction's internal structure) on 5M/15M.

This is **more testable than FLOD/LLOD** because every term has a mechanical
definition available: a swing break is computable, a liquidity sweep is a wick
through a prior extreme, and CHoCH is a break of the most recent internal swing.
The discretionary part is "major" swing, which becomes a lookback parameter.

Structurally it is **the same family as `HTFFVGFlip_v1` and `BiasIFVG_v1`** —
HTF bias, LTF confirmation entry — both of which are already in the codebase and
neither of which has been profitable here. That is not a reason to skip it, but
it is a reason to expect the result and to make the test cheap.

---

## 5c. FLOD / LLOD — the fragment, tested. No edge on real markets.

The selection rule is discretionary and untestable (§5a), so what was tested is
the mechanical claim underneath it: **price sweeps a prior day's extreme, then
reverses.** Sweep = the bar trades beyond PDH/PDL and closes back inside; entry
next bar's open (the app's fill); stop beyond the sweep extreme; target 2R.
M15, flat 1% of $10,000, 12,662 trades.

| Market | n | win | exp R | t | P&L $ | return |
|---|---:|---:|---:|---:|---:|---:|
| XAUUSD | 1,046 | 32% | −0.031 | −0.70 | −3,200 | −32% |
| EURUSD | 1,157 | 34% | +0.014 | +0.33 | +1,600 | +16% |
| GBPJPY | 1,355 | 31% | −0.063 | −1.68 | −8,600 | −86% |
| US Tech 100 | 1,268 | 31% | −0.077 | **−1.99** | −9,800 | −98% |
| BTCUSD | 961 | 33% | −0.020 | −0.44 | −1,900 | −19% |
| Step Index | 1,508 | 33% | −0.007 | −0.20 | −1,100 | −11% |
| Crash 1000 | 1,332 | 39% | +0.167 | **+4.16** | +22,200 | +222% |
| Vol over Crash 750 | 1,279 | 38% | +0.133 | **+3.27** | +17,000 | +170% |
| Boom 1000 | 1,365 | 36% | +0.090 | +2.31 | +12,300 | +123% |
| Volatility 75 | 1,391 | 36% | +0.078 | +2.03 | +10,900 | +109% |

**The split is the finding.** On every market ICT is actually taught on — FX,
gold, indices, crypto — the rule **loses money**, and US Tech 100 is nearly
significantly negative at t −1.99. It is positive **only on Deriv's engineered
synthetics**, where price is constructed to spike and revert.

So what the "liquidity sweep" is picking up is not institutions taking stops. It
is the synthetics' built-in mean reversion — which `SpikeFade_v1` and
`RangeRevert_v1` already target directly, without the ICT story on top.

**Control**: the same rule against shuffled days' levels pooled +0.017R against
the real +0.031R, a difference of only +0.014R. The control is weak — shuffled
levels sit far from price so they are rarely swept, giving 2,427 trades against
12,662 — so it bounds the effect rather than refuting it cleanly. The per-market
split above is the stronger evidence, and it does not need the control.

**Verdict: nothing here worth building on.** The testable core of FLOD/LLOD is
negative on real markets, and where it pays, a simpler strategy already in the
book pays for the same reason.

---

## 5d. ICC — tested on $10,000. The bias is worse than a coin flip.

Built mechanically: HTF bias from a confirmed swing break (confirmed = known
only k bars later, so it cannot read the future), a pullback that SWEEPS a
recent opposite-side swing, then a CHoCH back through the sweep bar's extreme.
Entry next bar's open, stop beyond the sweep, 2R target. M15, flat 1% of
$10,000.

| Market | real exp R | t | real return | **control exp R** |
|---|---:|---:|---:|---:|
| GBPJPY | −0.112 | −2.35 | −93% | +0.020 |
| US Tech 100 | −0.071 | −1.37 | −51% | +0.027 |
| BTCUSD | −0.047 | −0.94 | −37% | −0.014 |
| XAUUSD | — | — | — | −0.012 |
| Step Index | +0.024 | +0.50 | +21% | −0.039 |
| Volatility 75 | +0.027 | +0.54 | +22% | −0.094 |
| Vol over Crash 750 | +0.077 | +1.52 | +62% | **+0.094** |
| Boom 1000 | +0.103 | +2.02 | +83% | +0.082 |
| Crash 1000 | +0.129 | +2.54 | +106% | **+0.172** |

**The control is the result.** Replacing the Institutional Cash Cycle's
higher-timeframe bias with a **coin flip** and changing nothing else:

```
pooled real    : 8,098 trades   +0.0065R
pooled control : 9,331 trades   +0.0195R
the bias is worth: -0.0130R per trade
```

The real bias is **worse than random**. On Crash 1000 — the best market for it —
a coin flip returns +0.172R against ICC's +0.129R.

So the three-phase cycle contributes nothing. What little edge the rule has is
the sweep-and-reverse, and it appears in exactly the same places as FLOD/LLOD
(§5c): Deriv's engineered synthetics, and nowhere else.

**Both ICT systems reduce to the same mechanism, and that mechanism is already
in the book as `SpikeFade_v1` / `RangeRevert_v1`.** Neither is worth
implementing.

---

## 6. Strategy removals — plan, not yet done

To remove: **IVW_v1, OvernightSession_v1, OpeningDrive_v1, HTFFVGFlip_v1.**
Keeping SpikeResumption_v1 and APA_v1 as you said.

Each strategy touches seven places, and `tests/test_shipped_strategies_2026_09_25.py`
enforces that they all agree — so a partial removal fails loudly rather than
leaving a half-wired strategy:

1. `backend/strategies/strategy_*/` — the engine package
2. `STRATEGY_PARAM_SECTION` in `backend/api/routes/backtest.py`
3. The config block on `UserConfigV2`
4. The schema group in `schema_introspection.py`
5. `strategy_defaults.py` — defaults and evidence
6. `frontend/src/components/slotSpec.js` — `STRATEGY_OPTIONS`
7. `frontend/src/pages/StrategyLab.jsx`

**One thing to decide first**: saved slots and saved backtests referencing a
removed strategy. Deleting the code leaves rows pointing at a strategy id the
registry no longer knows. The safe form is a **retired list** — the strategy
stops appearing in the UI and cannot be selected for a new slot, while old
results stay readable. Deleting outright breaks history. Tell me which you want.

---

## 7. The study that is still running

Six `Vol over` symbols × DriftJumpAlpha / BoomDriftJump / TrendDrift, Jan→Oct,
$10k at 1% risk, through the **real engine**.

It is far slower than I estimated — **~26 minutes of CPU and still on the first
symbol.** Projected several hours. It is running in the background and I will
report the table when it lands.

Two notes:

- **Only M5 bars were exported, no M15.** TrendBreakout is an M15 strategy, so it
  cannot run on these symbols until a re-export. The three in flight are all M5.
- The run's console output is drowned in loguru file-rotation errors (concurrent
  processes fighting over `logs/backend.log`). Cosmetic, but it means progress is
  being read from CPU time rather than from the log.

### Method, and two traps avoided

The harness drives the **real strategy classes** through `BarFeed` — the same
feeder the live scan loop uses, so the bar sequence is identical — then the
**real `BacktestEngine`**. It is not a numpy reimplementation; `run_app_form_check.py`
is right for a sweep and wrong here, because a rewrite does not carry the app's
fills, costs, sizing floors or exits, and that is where the edge goes.

Two things that would have faked a result, both caught before any number existed:

1. **The spread column.** The exporter writes `spread_points`; the engine reads
   `spread` and treats it as points. My first loader defaulted a missing
   `spread` to `0.0` — charging **no spread at all**, which on a synthetic index
   is most of the round trip. Now a rename, and it raises rather than defaults.
2. **R per trade.** Trade records carry no risk-dollars field. R is recomputed
   from entry, the **initial** stop and filled volume via the app's own
   `calculate_risk_dollars`. Using the trailed stop would shrink R as a trade went
   well and flatter every winner.

---

## 8. What is left

**Blocked on you:**

- A copy of the **VPS `algoedge.db`** → unlocks the trade-by-trade audit of your
  four saved runs.
- **Retire vs delete** for the four strategies (§6).
- Whether to run the FLOD/LLOD fragment test (§5a) or drop it.
- `dev` is **red**: 41 pre-existing failures in the investor portal/email/
  notices/split/releases/admin-route suites, from other sessions. Verified not
  mine — identical 41 fail with my changes stashed. Someone should fix those
  before more lands on top.
- Another session reverted your **WAT** accounting day back to UTC (`9783ca4`),
  reasoning that WAT was for display, not for the bot's limits. You asked for WAT
  explicitly. Your call; I have not re-reverted it.

**Queued, no decision needed:**

- The running study's per-asset table (§7).
- DEX through the same three strategies — the directional question §3 leaves open.
- M15 re-export so TrendBreakout can run on the new symbols.
- TSMOM as specified, on daily bars across all reachable markets as one portfolio.
- ICC, built as a mechanical rule (§5b).
- A scalping strategy for the majors — **not started, and I would want to do it
  last**: it is the least specified of the requests and the most likely to
  produce a plausible-looking result that does not survive costs.

---

## Sources

- Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*, JFE 104(2) —
  https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf
- ICT PD-array selection (FLOD / OD / LLOD) —
  https://en.rattibha.com/thread/1792539404214677666
- ICC (Institutional Cash Cycle) framework —
  https://oboe.com/learn/icc-forex-trading-strategy-1vmj2yh
