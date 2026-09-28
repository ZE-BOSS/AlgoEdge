# Implementation plan — options, the spike-resumption strategy, and unified profit targets

**2026-09-27** · awaiting your go-ahead before any of Parts 2–5 is built

---

## Part 0 — Closing out the last batch before you push

You asked me to confirm the three strategies shipped on 2026-09-25 are clean. They are, and
**verifying them turned up three real bugs**, all now fixed with regression tests. Two were
pre-existing and affect strategies you already run.

| | What | Status |
|---|---|---|
| 1 | **The broker clock was read wrong whenever the market was shut.** `detect_server_utc_offset_hours` compared a live tick to real UTC; a tick read after the close is in the past, so it can only read LOW. Same terminal, same evening: **+2.0h, then +1.0h, then +0.5h — the truth is +3.0h.** Every session-anchored strategy (VWAP, ORB, APA session modes, IVW's day-end, and both new session strategies) traded up to 2.5 hours early, and *which* hours depended on when the backend happened to start. | Fixed: the offset is now read from where the New York cash open actually sits in the bars, and a tick is trusted only when it agrees. `tests/test_server_offset.py`, 12 tests. |
| 2 | **Any M5 backtest longer than ~11 months failed outright.** `copy_rates_range` measures a request against the number of *periods* the span covers, not the bars in it, so an 11-month M5 range (68,617 real bars) was refused against a `maxbars` of 100,000 — and the fallback asked `copy_rates_from` for up to 200,000, which is above every real `maxbars`, so it could only fail too. | Fixed: range requests are sliced to the terminal's own limit. `tests/test_data_fetch_maxbars.py`, 7 tests. |
| 3 | **Backtests were not reproducible in EMPIRICAL fill mode.** The fill model's "deterministic" key was `pos["id"]` — a fresh `uuid4()` per position per *run* — so the same backtest drew a different overshoot every time. This is your "same settings, different results" complaint, in the one mode that matters most for Crash/Boom. | Fixed: the key is now the trade's own identity. 3 tests in `tests/test_fill_model.py`. |

How much bug 1 mattered, same config before and after:

| | wrong clock (+1h) | correct clock (+3h) |
|---|---|---|
| OvernightSession_v1 US30 | −6.25% | **+2.28%** |
| OvernightSession_v1 SPX500 | −13.10% | −2.73% |
| OpeningDrive_v1 SPX500 | −3.59% | −1.14% |
| TrendBreakout_v1 XAUUSD | +16.66% | +12.49% |
| TrendBreakout_v1 EURUSD | +0.58% | +0.51% |

The two session strategies moved a lot and the non-session one barely moved — exactly the
signature that says the fix is real and not noise.

**Two things I have NOT changed, because they are your decisions, not mine:**

1. `.env` points the app's primary MT5 at the **FundedNext** terminal
   (`MT5_PATH=C:\Program Files\MetaTrader 5`); Deriv is at
   `DERIV_MT5_PATH=C:\Program Files\MetaTrader 5 Terminal`. A backtest on Boom/Crash
   therefore finds no data right now. Everything in Part 2 below needs Deriv.
2. I would set `ALGOEDGE_MT5_SERVER_UTC_OFFSET=3` in `.env` to pin the FundedNext clock
   rather than re-derive it every restart. Say the word and I will.

- [x] 0.1 Three strategies wired backend + frontend, 22 tests
- [x] 0.2 Clock bug found, fixed, 12 tests
- [x] 0.3 maxbars bug found, fixed, 7 tests
- [x] 0.4 Fill-model reproducibility bug found, fixed, 3 tests
- [x] 0.5 `npx eslint src` at the 61-problem baseline (no new problems); `npx vite build` clean
- [x] 0.6 Full suite green end to end — **671 passed, 0 failed** (18m21s), with all three fixes in
- [ ] 0.7 *(your call)* pin `ALGOEDGE_MT5_SERVER_UTC_OFFSET=3`; *(your call)* repoint MT5 to Deriv

---

## Part 1 — What the five videos are actually worth

I went through all five. They are not equal, and two of them cannot be done at all with the
account size you named. Straight answers first.

### Video 1 — triple EMA (34/55/200), crypto futures, 4h

**Verdict: testable, and the honest version of the claim is smaller than it sounds.**

The rule is a standard trend filter plus a crossover trigger and an ATR stop. Nothing in it is
novel — it is the same family as `TrendBreakout_v1`, which already measures **+0.063R, t +2.17**
on real CFDs. The interesting claim is not the strategy, it is the **timeframe sweep**: 1h worst,
4h best, 8h/12h falling off. That is exactly the shape I measured independently on the Donchian
system two days ago (M15 +0.063R → D1 +0.026R), for the opposite reason — there, longer was worse
because of entry lag; here, shorter is worse because of crossover noise. Both effects are real
and they fight each other, which is why a sweep is the right way to pick a timeframe and a single
backtest is not.

Three things about the video's numbers you should not take at face value:

- **+3,597% on longs vs +116% on shorts, May 2020 → Sep 2026.** Buying and holding BTC over that
  window returned several thousand percent. A long-biased crypto trend system over the biggest
  bull run in the asset's history is mostly measuring the bull run. The short side — the part
  that would prove the *strategy* works rather than the *market* — contributed 116%.
- **30% win rate with PF 1.52** is a fine trend profile, but 959 trades over 6 years on 8 pairs
  is ~20 trades per pair per year. Per-pair significance will be weak.
- **No cost detail given.** On Binance futures, funding is paid every 8 hours on a held position.
  A 4h trend system holds through many funding windows, and the video does not say whether that
  is in the numbers. On our own overnight strategy this was the first thing I checked.

**What I will do:** implement it as `TripleEMA_v1` and run the same five-timeframe sweep on *our*
markets — FX, metals, indices, crypto CFDs — with our cost model, plus the long/short split so we
can see whether the short side pays. If it beats `TrendBreakout_v1` on the same markets it ships;
if it does not, that is a useful negative and it does not ship.

### Video 2 — box spreads

**Verdict: correct, genuinely near-risk-free, and impossible at $1,000.**

The mechanic is right: a box spread's payoff at expiry is fixed at the strike difference, so
buying one is lending money at the implied rate and selling one is borrowing. The video's own
numbers: the SPX 7000/8000 March box costs $97,750 and pays $100,000, which is 2.3% over six
months, ~4.6% annualised. **You need $97,750 to earn it.** The XSP mini is one tenth, so ~$9,775.

At $1,000 you cannot put on a single box on either. This is not a risk preference, it is the
contract size. I am including it in the plan only so it is written down as *ruled out with the
arithmetic shown*, not quietly dropped.

One correction to the video worth knowing: box spreads are near-risk-free, not risk-free. On
American-style options early assignment breaks the box; SPX/XSP are European so that particular
risk is absent, which is why everyone does them there. The residual risks are broker margin
treatment and the fact that you are locked in until expiry.

### Video 3 — at-the-money straddle plus a call spread

**Verdict: the risk description in the video is wrong, and it is also out of reach at $1,000.**

"If the stock moons, I still make money. If the stock comes down, I still make money. And if the
stock stays exactly where it's at, I still make money." That is not what a short straddle does.
A short straddle makes money inside a band around the strike and loses outside it, without limit
on the downside. Capping the upside with a call spread removes the upside tail; **it does nothing
about the downside tail.** "You get to be synthetically long, you wanted to own the stock anyway"
is a real answer to that — but it is an answer that says *accept the loss and hold the shares*,
which is a different claim from "I still make money".

The capital: a short at-the-money put obligates you to buy 100 shares at the strike. On a $30
stock that is $3,000 of collateral per contract. At $1,000 you cannot sell a single at-the-money
put on anything you would actually want to own.

The claimed $50k–$100k a month in premium is consistent with an account in the low seven figures
running this. It is not a small-account strategy and it is not presented as one.

### Video 4 — CFTC Commitments of Traders

**Verdict: real, free, already tested here, and it did not work.**

Everything factual in the video is correct — published Fridays 15:30 ET, positions as of Tuesday,
free, and there is a proper JSON API (below). What the video does not say is whether following it
makes money. **I tested this on 2026-09-17: COT following failed out of sample.** It is in the
notes as a dead end.

That does not make the data useless — it makes it a *conditioning variable* rather than a signal.
Two things I have not tested and would: COT extremes as a **regime filter** on the trend book
(only take trend trades when large speculators are not already at a positioning extreme), and the
**Tuesday-to-Friday reporting lag** as a tradable staleness effect. Both are cheap because the
data is free.

### Video 5 — exchange matching algorithms (FIFO, FIFO with lead market maker)

**Verdict: factually correct and strategically irrelevant to us.**

FIFO and pro-rata allocation are real and publicly documented. But the video's own framing gives
the game away: this is about **queue position on a central limit order book**. It matters if you
are a market maker deciding where to rest a passive order. We trade **CFDs against a dealer** —
Deriv and FundedNext are not exchanges, there is no book, there is no queue, and every fill is a
dealer quote. There is no version of "get better queue position" that applies.

You said this one "is a pure strategy on its own". I do not think it is, and I would rather say
so than build something that cannot work. What it gestures at — that expiry and contract
roll create mechanical, schedulable flow — **is** real and testable, and that is Part 3.

---

## Part 2 — `SpikeResumption_v1`: the Boom/Crash pattern you found

### What you described, restated as a rule

From your two Boom 900 M15 screenshots and your description, for **Boom** (spikes UP, drifts DOWN):

1. **Context** — an established downward drift. Price has been grinding down for a while.
2. **The spike** — a single M15 bar that breaks the drift with a sharp buy. Usually one bar,
   occasionally two. The spike bar count is part of the rule: *"the candlestick for spike should
   just be one."*
3. **Confirmation** — the next bar closes bearish. *"When it has completed one sell candlestick,
   then you now enter your position for sell."*
4. **Entry** — SELL at the close of that first bearish bar.
5. **Target** — below the spike. *"It must come below the last buy spike."*
6. **Expected hold** — typically 3 M15 bars, sometimes 4 or 5.

**Crash** is the exact mirror: upward drift, one sharp SELL spike, one completed bullish bar,
BUY, target above the spike.

### Why this is a genuinely new strategy and not a tweak to `BoomDriftJump_v1`

I checked. `BoomDriftJump_v1`'s Setup A enters on a pullback to the fast EMA during an active
drift regime, and it contains this line:

> `# Do not sell into a fresh up-spike; the mirror of DJA's block.`

**The existing strategy explicitly refuses to trade the setup you are describing.** Yours uses the
spike as the *trigger*; the existing one treats a fresh spike as a reason to stand aside. That is
why you feel DJA's entries are "a dice flip" — it enters on EMA pullbacks anywhere in the drift,
with no reference to where the last spike was. So this is a separate strategy with a separate
name, not a parameter.

**Name: `SpikeResumption_v1`** (confirmed 2026-09-27) — the drift resumes after the spike is spent. (Alternatives if you
prefer: `PostSpikeDrift_v1`, `SpikeExhaustion_v1`. Say which and I will use it.)

### What has to be defined before it can be tested

Your description leaves five things open. I will sweep each rather than guess, and report the
grid so you can see which choices the result depends on:

| Open question | What I will sweep |
|---|---|
| "a clear downward trend" | EMA slope over N bars; N-bar return below a threshold; bars since the last opposite spike. 3 definitions × 3 lookbacks. |
| "a spike" | size in ATR (2×, 3×, 5×), and the Deriv tick-spike detector already in `strategy_boom` |
| "one completed sell candlestick" | 1 bar (your rule) vs 2 bars; and close-below-spike-low as an alternative confirmation |
| "target below the last spike" | the spike bar's low, its open, and the pre-spike swing low — plus a fixed R:R and a trailing version for comparison |
| hold | your observed 3 bars, plus 4, 5, and "hold until the target or an opposite spike" |

### The measurement, and the trap I will avoid

The **λ spike-fill model is mandatory here** and this is the whole ballgame. On jump markets a
stop on the spike side does not fill at the stop — it fills part-way to the bar extreme. I
measured λ = 0.403 on Crash 1000 and 0.353 on Boom 1000. **A naive bar model faked +0.46R per
trade on random entries on these instruments, and turned a +1,065% result into +361% once
corrected.** For this strategy that risk is at its maximum, because the whole setup lives next to
a spike. Every number I report will be with `stop_fill_model=EMPIRICAL`.

Data on hand, no fetching needed:

| Instrument | M5 bars | Range |
|---|---|---|
| **Boom 900 / Crash 900** (your screenshots) | 217,739 each | 2024-08-30 → 2026-09-25 |
| Boom 1000 / Crash 1000 | ~521,000 each | 2021-10-01 → 2026-09-25 |

I will also pull Boom/Crash 300 and 500 so the pattern is tested on six instruments, not two —
if it is a real property of how Deriv generates these series, it must show up across the family.
If it only works on Boom 900, it is a fit to one series.

**Reported for every variant:** $10,000 start, returns, max drawdown in $ and %, expectancy in R
and dollars, profit factor, Sharpe, t-statistic, win rate, the R:R actually used, trade count,
monthly and weekly green/red counts and streaks, and the longest losing streak. Plus the
surrogate p95 test — you have been burned by overlap-inflated expectancy before, so
`significance.verdict` gets read before `profit_factor`.

You said you want returns, not necessarily an edge. Understood — but I will report the t-statistic
anyway, because a good return with t < 1 means the next two years can easily look nothing like
the last two, and you should get to see that number before you size it.

### Tasks

- [ ] 2.1 Cache Boom/Crash 300, 500, 900, 1000 M5 from the Deriv terminal
- [ ] 2.2 `scripts/run_spike_resumption.py` — research harness, λ fills on, one position at a time
- [ ] 2.3 Sweep the five open definitions; report the full grid, not the best cell
- [ ] 2.4 Walk-forward: choose on 2024-08 → 2025-12, report unchanged on 2026
- [ ] 2.5 Surrogate/placebo control — the same rule on shuffled spike locations
- [ ] 2.6 **Decision gate: if it does not clear the control, I report that and do not ship it**
- [ ] 2.7 `backend/strategies/strategy_spike_resumption/` (params + engine), app-form measured
- [ ] 2.8 Wire: registry, `config_schema`, `schema_introspection`, `STRATEGY_PARAM_SECTION`, `strategy_defaults`, `slotSpec.js`, `StrategyLab.jsx`
- [ ] 2.9 Parity tests: engine vs research reference bar for bar; single vs portfolio engine identical
- [ ] 2.10 End-to-end app backtest on Boom 900 and Crash 900

---

## Part 3 — Options: what is real, what $1,000 can do, and what I would actually build

### Scope change, 2026-09-27

You said: *"test and research them regardless of the capital constraint... so when the capital is
available, they can be implemented and tradable. Or even implemented before the capital is
available, but traded when capital is available."*

So the capital arithmetic below stays as a **sizing fact**, not an exclusion. Box spreads,
straddle-plus-call-spread and cash-secured puts all get researched, backtested and built, with a
capital-adequacy check that reports the minimum account each one needs and refuses to place a
trade below it — the same shape as the existing min-lot rejection, which names the constraint
instead of silently doing nothing.

### The headline

**Trading options with $1,000 is not where the near-term value is. Using options *data* to trade
the CFDs you already trade is.** That decides the ORDER, not the scope: the free study runs first
because it can pay off immediately, and the options strategies get built alongside it for when
the account is bigger.

At $1,000 the only options you can actually put on are defined-risk debit spreads a few dollars
wide, or crypto options on Deribit. Every strategy in the videos you sent needs $10k–$100k+.
Meanwhile the *information* in the options market — where dealers are hedged, where the big open
interest sits, when contracts expire — moves the underlying indices, and **you already trade those
indices as CFDs on both terminals.** US Tech 100, US SP 500, US30, SPX500 and Germany 40 are in
the book right now. That channel needs no options capital at all.

### 3a. The part that costs nothing and gets tested first

Before spending a cent on options data I can test the whole premise on bars we already have:

**Hypothesis: options expiry dates produce a schedulable, tradable effect on index CFDs.**

The dates are deterministic and need no data feed — monthly OPEX is the third Friday, quarterly
triple witching is the third Friday of Mar/Jun/Sep/Dec, and 0DTE expiries are every session.
So this is a pure event study on data in hand:

- index CFD returns and realised volatility on OPEX days vs matched control days
- the week *of* OPEX vs the week after (the classic "gamma unclenches after expiry" claim)
- triple-witching weeks specifically
- intraday: is the OPEX-day open→close range different, and is the direction predictable
- the same tests on FX majors as a **control** — FX has no equity-options expiry, so if the
  effect shows up there too, it is a day-of-week artefact and not gamma

**This is the single highest-value experiment in the whole options question**, because it is free,
it uses the markets we trade, and if the effect is not there then no amount of paid options data
will make it appear. If it *is* there, the case for paying for data is made with evidence.

### 3b. Where the data comes from, and what it costs

If 3a is positive, these are the real options:

| Source | What you get | Cost | Key needed |
|---|---|---|---|
| **[CFTC COT](https://publicreporting.cftc.gov/)** | weekly positioning, every futures market incl. index and FX | **free** | none (a Socrata app token raises the rate limit) |
| **[Alpaca](https://alpaca.markets/options)** | US equity + index options chains, Greeks, commission-free trading, paper account | **free** tier; real-time OPRA is paid | yes — free to create |
| **[Polygon.io](https://polygon.io/)** | options chains, trades, quotes, OI, history | from ~$29/mo stocks + ~$79/mo options | yes |
| **[ThetaData](https://www.thetadata.net/)** | tick-level options history, built for backtesting | from ~$80/mo | yes |
| **[ORATS](https://orats.com/data-api)** | 25 years of history back to 2007, implied vols, Greeks | from ~$99/mo | yes |
| **[Databento](https://databento.com/)** | tick precision, pay-per-use, $125 free credit | pay-as-you-go | yes |
| **[Deribit](https://www.deribit.com/)** | BTC/ETH options, full chain + order book, **testnet** | **free** | free, incl. testnet |
| **[CBOE DataShop](https://datashop.cboe.com/)** | official SPX/VIX history | per dataset | account |

**What I need from you, and when:**

- **Nothing yet.** 3a needs no keys.
- **If 3a is positive:** an **Alpaca** key first (free, and enough to prototype chains + Greeks +
  paper execution), and a **Deribit testnet** key if you want the crypto-options route. I would
  only ask for a paid key (Polygon or ThetaData) once there is a measured reason.
- **CFTC** needs nothing — I can start on that today.

### 3c. Gamma exposure, restated accurately

The claim in the search results, which matches the literature: when dealers are net **long**
gamma they hedge by selling rallies and buying dips, which **suppresses** volatility and favours
mean reversion; when they are net **short** gamma they hedge in the direction of the move, which
**amplifies** it. 0DTE concentrates this into hours instead of spreading it over days.

Computing dealer GEX properly needs the full chain with open interest per strike, plus an
assumption about who is long and who is short — which is the weak link, because the sign of dealer
positioning is **inferred, not observed.** Every public GEX number rests on a heuristic
(customers buy calls and puts, dealers take the other side). That assumption is where most of the
error lives, and any result I produce will state which heuristic was used and how sensitive the
answer is to it.

**The testable version for us:** does a GEX regime computed on SPX predict anything about the next
session on **US SP 500 / SPX500 / US Tech 100 CFDs** — realised range, mean reversion vs trend,
or the profitability of the strategies already in the book? That is a filter question, and a
filter that improves an existing strategy is worth far more to a $1,000 account than any options
position it could afford to open.

### 3d. What "options trading" would actually look like at $1,000, if you still want it

For completeness, ruled in and ruled out:

| Strategy | Minimum account | Tradable at $1,000? | Build it? |
|---|---|---|---|
| SPX box spread (video 2) | ~$97,750 | No | **Yes** |
| XSP box spread | ~$9,775 | No | **Yes** |
| ATM straddle + call spread (video 3) | ~$3,000+ per contract on a $30 stock | No | **Yes** |
| Cash-secured put | strike x 100 | No on anything liquid | **Yes** |
| **Defined-risk vertical debit spread** | a $1-wide spread is ~$40-60 | **Yes** | **Yes** |
| **Deribit BTC/ETH options** | fractional contracts | **Yes** | **Yes** |

Every one gets a `min_account_equity` check that rejects with the number, so an under-funded
account gets told *"this needs $9,775 and you have $1,000"* rather than nothing happening.

So: vertical spreads on liquid US underlyings via Alpaca, or crypto options on Deribit. Both are
API-native and both have free paper/testnet environments, so they can be built and proven without
risking the $1,000 at all. I would not put real money into either until a backtest exists.

### 3e. Backtesting options — the part that is harder than it looks

Options backtesting is not bar backtesting with more columns, and I want to be clear about why
before you approve the effort:

1. **The spread is the strategy's biggest cost and it is not in most datasets.** Mid-price fills
   overstate every options backtest. We need quotes, not just trades — which is exactly what
   separates the $29 tier from the $80 tier.
2. **Multi-leg fills.** A four-leg box or a straddle-plus-spread fills as a package at a net
   price; modelling it as four independent fills is wrong in the optimistic direction.
3. **Assignment and expiry** have to be simulated, including early assignment on American-style
   contracts.
4. **Survivorship in the chain.** Strikes get listed and delisted; a naive chain reconstruction
   quietly assumes strikes existed when they did not.

Our existing backtester models one instrument with one price series. An options engine is a new
component, not a parameter — I would build it as a separate research harness first
(`scripts/run_options_*.py`), exactly the way the CFD research was done, and only consider an app
engine if the research finds something.

### Tasks

- [ ] 3.1 **Expiry event study on data in hand** — OPEX, triple witching, 0DTE, with FX as the control (no API, no cost)
- [ ] 3.2 CFTC COT client + cache (free); COT extremes as a *regime filter* on the existing trend book, not as a signal
- [ ] 3.3 **Decision gate: report 3.1 and 3.2 before anything is PAID for** (building continues either way)
- [ ] 3.4 Alpaca chain + Greeks client; reconstruct SPX GEX; test as a filter on index CFDs
- [ ] 3.5 Options research harness with a real quote-based fill model (multi-leg, assignment, expiry)
- [ ] 3.6 Strategy set: vertical spreads, box spread, straddle+call-spread, cash-secured put — each with a capital-adequacy gate
- [ ] 3.7 Deribit testnet client — the one route actually tradable at $1,000 today
- [ ] 3.8 Paper/testnet execution proof for every strategy built, before any real money

---

## Part 4 — Unified profit targets, per symbol × strategy

### What exists today

`target_profit_enabled`, `max_daily_profit`, `max_weekly_profit` are already per-slot fields and
already reach the per-slot circuit breaker. Four things are missing, and one of them is the one
you actually care about:

| Gap | Detail |
|---|---|
| **Basis** | Only **realised** (closed) P&L. There is no floating-equity option at all. Floating equity is used in exactly one place in the whole codebase — the prop-firm drawdown check — and there it is **account-wide**. |
| **Scope** | Only daily and weekly. No per-trade, no monthly. |
| **Action** | A target only **pauses new entries**. It never closes an open position. What you described — "set a profit target for that trade and leave it to run" — does not exist. |
| **Attribution** | The thing you warned about. Nothing today attributes floating equity to a slot, so a naive implementation would do exactly the wrong thing. |

### What I will build

**A slot's profit target is `(scope, basis, amount)`, resolved per symbol × strategy.**

- **Scope:** `TRADE` · `DAY` · `WEEK` · `MONTH` — selectable, and more than one can be armed at once
  (a $50 per-trade target and a $200 weekly target coexist).
- **Basis:** `BALANCE` (realised, closed trades only) or `FLOATING` (realised + unrealised on this
  slot's open positions).
- **Amount:** in account currency, or as a percentage — same treatment as the risk fields.
- **Action:** `PAUSE` (stop taking new entries, the current behaviour) or `CLOSE_AND_PAUSE` (flatten
  this slot's open positions, then pause). `TRADE` scope implies closing that trade.

**The attribution rule, stated explicitly because it is the whole point:**

> A slot's floating P&L is the sum of the unrealised P&L of **the positions that slot opened**, and
> nothing else. It never reads another slot's positions and it never reads account equity.

Your example works correctly under that rule: Boom is +$20 against a $50 target and stays open;
Crash at +$40 is invisible to Boom's target and cannot close it. We already track position
ownership per slot (`_cache_key` in the portfolio engine, slot resolution in `bot_service`), so
the accounting has a home — it is the reading of *account* equity that has to be kept out.

**Where it has to work identically:** single backtest, portfolio backtest, and live. Same three
paths as the per-slot risk work, and it gets the same treatment — a parity test that runs the same
slot through all three and requires the same closes on the same bars.

**Why this matters for Boom/Crash specifically**, which is what prompted you: these strategies
stack positions and the losses compound because entries are near spikes. A per-trade floating
target lets a position that is in profit but facing adverse order flow be banked instead of round-
tripped, and it does so per slot so one instrument's good day cannot bank another's open trade.

### Tasks

- [ ] 4.1 Schema: `profit_target_scope`, `profit_target_basis`, `profit_target_action`, `profit_target_amount`, `profit_target_is_pct`, plus monthly/per-trade amounts; keep `max_daily_profit` / `max_weekly_profit` working so saved configs do not change meaning
- [ ] 4.2 `SlotLedger` — realised and floating P&L **per slot**, fed by position open/close events, never by account equity
- [ ] 4.3 Circuit-breaker rewrite of check 6 to read the ledger, all four scopes, both bases
- [ ] 4.4 `CLOSE_AND_PAUSE` in the single backtester, the portfolio backtester and `position_manager`
- [ ] 4.5 Period rollover for month scope (day and week already roll)
- [ ] 4.6 Frontend: the 'Profit halts' section in `slotSpec.js` becomes scope + basis + action + amount, schema-driven so the docstrings carry the explanation
- [ ] 4.7 **The attribution test** — two slots open at once, one in profit, prove the other's target does not fire
- [ ] 4.8 Three-way parity test: single vs portfolio vs live replay, same closes on the same bars
- [ ] 4.9 End-to-end: a Boom slot and a Crash slot in one portfolio run, per-trade floating targets, verified per slot

---

## Part 5 — `TripleEMA_v1` (video 1) on our markets

- [ ] 5.1 `scripts/run_triple_ema.py` — 34/55/200 with an ATR stop, long and short
- [ ] 5.2 Sweep M15 / M30 / H1 / H4 / D1 on FX, metals, indices and crypto CFDs, both feeds
- [ ] 5.3 Long/short split reported separately — the short side is the honest test
- [ ] 5.4 Head-to-head against `TrendBreakout_v1` on identical markets and dates
- [ ] 5.5 **Decision gate: ship only if it beats what we already have**
- [ ] 5.6 If it ships: strategy package, wiring, parity tests, app-form measurement

---

## Order of work, and why

1. **Part 0.6** — finish the suite, report, you push. Nothing else starts until the current work is
   confirmed clean.
2. **Part 4 (profit targets)** — it is the only item that improves money you are trading *today*,
   it is fully specified, and it needs no research.
3. **Part 2 (`SpikeResumption_v1`)** — your own observation, data already cached, and it answers a
   real complaint about DJA's entries.
4. **Part 3a + 3.2 (expiry study + COT)** — free, uses markets we trade, and decides whether any
   options spending is justified.
5. **Part 5 (`TripleEMA_v1`)** — worth knowing, but it competes with a strategy we already have.
6. **Part 3.4+ (paid options data)** — only if 3a earns it.

## Two things I will not do without you saying so

- Spend money on a data subscription.
- Change `.env` (the MT5 terminal binding, or pinning the clock offset).

## Standing rules for every backtest in this plan

$10,000 starting capital · costs from the broker model, never mid-price · `stop_fill_model=EMPIRICAL`
on jump markets · one position at a time unless the rule says otherwise · walk-forward with the
choice made on the early window and reported unchanged on the late one · surrogate control before
any result is believed · and every report carries returns, max drawdown in $ and %, expectancy in
R and dollars, profit factor, Sharpe, t-statistic, win rate, R:R, trade count, and monthly and
weekly green/red counts and streaks.

---

**Sources for Part 3:**
[CFTC Public Reporting](https://publicreporting.cftc.gov/) ·
[CFTC COT](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm) ·
[Alpaca Options](https://alpaca.markets/options) ·
[ORATS Data API](https://orats.com/data-api) ·
[Best Options Data APIs 2026 comparison](https://flashalpha.com/articles/best-options-data-apis-2026) ·
[Historical options data sources](https://www.quantvps.com/blog/download-historical-options-data) ·
[SpotGamma — Gamma Exposure](https://spotgamma.com/gamma-exposure-gex/) ·
[0DTE SPX and intraday gamma structure](https://gex-levels.com/blog/0dte-spx-options-strategy) ·
[Gamma exposure explained](https://flashalpha.com/articles/what-is-gamma-exposure-gex-explained)
