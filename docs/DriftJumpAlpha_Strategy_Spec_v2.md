# Drift and Jump Alpha — Strategy Specification (Crash-Only)

## v2.0 — Implementation Spec for AI Coding Agents

**Supersedes:** `CrashBoom_Strategy_Spec.md` v1.0 **Instruments:** Crash 300/500/600/900/1000 and future Crash variants. Boom removed entirely — config is per Crash instrument only. **Version:** 2.0

> ⚠️ **Disclaimer:** This is a technical specification for trading synthetic indices where the counterparty (Deriv) generates the price feed. It is not financial advice. Backtest and paper-trade extensively — including the validation protocol in §8 — before risking live or challenge capital. Past performance does not guarantee future results, and synthetic index parameters can change without notice.

---

## Changelog: v1.0 → v2.0

| Change | Reason |
| :---- | :---- |
| Boom logic removed entirely; Crash-only | Product decision |
| Renamed `CrashBoom_v1` → `DriftJumpAlpha_v1`; `CrashBoomEngine` → `DriftJumpAlphaEngine` | Product decision |
| Strategy 1 → **Setup A** (Drift Continuation, buy-only); `direct_jump_entry` → **Setup B** (Jump Entry, sell) | Naming alignment |
| Setup B stays opt-in **and** hard-gated behind a passing control test — the gate is not replaced by the `trade_jumps_enabled` toggle alone | Independent tick-level research on Crash 1000 found no evidence for the timing edge Setup B assumes (§9) — the gate needs to stay strict, not become a simple default-off flag |
| New hard guardrail: aggregate open-lot ceiling per instrument/account tier, enforced independently of the risk-% sizing formula | The existing formula (risk$ / stop distance) can exceed challenge-provider lot limits, especially with Setup B's tighter stops — see §6 |
| New: spread/transaction-cost modeling required in every backtest report | Independent research found round-trip spread costs large enough to erase typical drift-capture edges on Crash 1000 — see §9 |
| New §9: Research Basis | Traceability for the above decisions |

---

## 1\. Master Configuration Object

{

  "strategy\_suite": "DriftJumpAlpha",

  "version": "2.0",

  "instrument": {

    "symbol": "CRASH1000",

    "product\_type": "crash",

    "documented\_avg\_ticks\_between\_jumps": 1000,

    "tick\_or\_bar\_mode": "bar",

    "execution\_timeframe": "1m",

    "drift\_direction": "up",

    "jump\_direction": "down",

    "pip\_size": 0.001,

    "point\_value": 1.0

  },

  "setup\_a\_drift\_continuation": {

    "enabled": true,

    "regime\_filter": {

      "fast\_ema\_period": 20,

      "slow\_ema\_period": 50,

      "require\_fast\_above\_slow": true,

      "min\_ema\_separation\_atr\_multiple": 0.2,

      "secondary\_trend\_strength\_filter": {

        "enabled": true,

        "method": "adx",

        "adx\_period": 14,

        "min\_adx\_to\_trade": 20,

        "note": "Reduces whipsaw entries in choppy stretches. Filters the EMA regime check; does not replace it."

      }

    },

    "entry\_trigger": {

      "method": "pullback\_higher\_low",

      "pullback\_reference": "fast\_ema",

      "pullback\_max\_distance\_atr\_multiple": 1.0,

      "confirmation\_candles\_required": 1,

      "require\_close\_beyond\_prior\_swing": true,

      "swing\_lookback\_bars": 5,

      "block\_entries\_above\_jump\_entry\_percentile\_threshold": true

    },

    "exit": {

      "method": "atr\_trailing\_stop",

      "atr\_period": 14,

      "atr\_multiple": 2.0,

      "atr\_multiple\_adaptive": true,

      "atr\_multiple\_low\_vol": 1.5,

      "atr\_multiple\_high\_vol": 2.5,

      "min\_hold\_bars\_before\_trailing": 3,

      "hard\_take\_profit\_enabled": false,

      "hard\_take\_profit\_rr": 4.0

    }

  },

  "setup\_b\_jump\_entry": {

    "enabled": false,

    "status": "experimental — opt-in only, hard-gated by validation\_protocol.control\_test\_passed",

    "jump\_entry\_percentile\_threshold": 95.0,

    "trigger": {

      "method": "choch\_break\_of\_swing\_low",

      "require\_bearish\_confirmation\_close": true,

      "swing\_lookback\_bars": 5

    },

    "stop\_loss": {

      "placement": "tight\_above\_recent\_swing\_high",

      "buffer\_atr\_multiple": 0.2,

      "note": "Tighter than Setup A's stop by design — see §6 for the lot-size consequence."

    },

    "role\_if\_validation\_fails": "gap\_percentile reverts to sizing/exposure input only (§3) — no directional entries",

    "note": "Do not enable on live or challenge capital without a passing control test (§8) run against current tick data for this exact symbol. See §9 for why the default assumption should be 'no edge', not 'edge until proven otherwise'."

  },

  "gap\_distribution\_engine": {

    "lookback\_bars\_for\_fit": 20000,

    "recompute\_every\_n\_bars": 1000,

    "candidate\_distributions": \["exponential", "geometric", "erlang", "empirical\_histogram"\],

    "min\_bars\_before\_trusting\_fit": 5000,

    "store\_full\_history": true

  },

  "risk\_management": {

    "risk\_per\_trade\_pct": 1.0,

    "risk\_per\_trade\_pct\_mode": "fractional\_kelly",

    "kelly\_fraction": 0.25,

    "kelly\_recalc\_window\_trades": 100,

    "max\_risk\_per\_trade\_pct": 2.0,

    "min\_risk\_per\_trade\_pct": 0.25,

    "max\_trades\_per\_day": 6,

    "max\_concurrent\_positions": 1,

    "max\_daily\_risk\_pct": 4.0,

    "max\_weekly\_drawdown\_pct": 8.0,

    "max\_consecutive\_losses": 4,

    "cooldown\_after\_max\_losses\_hours": 12,

    "min\_rrr\_to\_accept\_trade": 1.5,

    "challenge\_account\_lot\_ceiling": {

      "enabled": true,

      "aggregate\_max\_lots\_per\_symbol": 6.0,

      "applies\_to": "sum of all open lots on the symbol across Setup A and Setup B, checked at every entry or scale-in",

      "enforcement": "hard clamp applied AFTER the risk-% sizing formula, before order submission — never a soft warning",

      "note": "6.0 matches the $25k Crash 1000 BloomFunded tier used in current backtests. Re-check this value whenever account size or challenge provider changes."

    }

  },

  "stop\_loss": {

    "placement": "structure\_or\_atr",

    "structure\_reference": "last\_swing",

    "atr\_multiple": 1.5,

    "buffer\_atr\_multiple": 0.2,

    "widen\_stop\_near\_high\_gap\_percentile": true,

    "widen\_multiple\_at\_hard\_threshold": 1.5

  },

  "take\_profit": {

    "number\_of\_tps": 2,

    "tp\_rr\_levels": \[1.5, 3.0\],

    "tp\_close\_pct": \[60, 40\],

    "move\_sl\_to\_breakeven\_after\_tp\_index": 0

  },

  "trailing\_stop": {

    "enabled": true,

    "method": "atr",

    "activate\_after\_rr": 1.0,

    "atr\_multiple": 2.0

  },

  "backtest\_controls": {

    "run\_random\_parameter\_control": true,

    "control\_runs": 200,

    "control\_significance\_threshold": 0.05,

    "report\_setup\_a\_and\_b\_separately": true,

    "walk\_forward\_enabled": true,

    "walk\_forward\_window\_bars": 10000,

    "walk\_forward\_step\_bars": 2000,

    "model\_transaction\_costs": {

      "enabled": true,

      "round\_trip\_spread\_points": "fetch\_from\_broker\_feed\_or\_default\_1430",

      "note": "Never report gross P\&L as the headline number. Independent tick studies found round-trip spread on Crash 1000 large enough to erase typical drift-capture windows — see §9."

    }

  },

  "validation\_protocol": {

    "control\_test\_passed": false,

    "control\_test\_last\_run": null,

    "control\_test\_required\_for": \["setup\_b\_jump\_entry.enabled"\],

    "protocol\_reference": "Adapt the pre-registered kill-gate design cited in §9: KS test vs. exponential, hourly dispersion index, lag-1 autocorrelation, empirical hazard curve, post-spike-drift-vs-random-window test — run against current live/historical tick data for the exact symbol and account being traded, not assumed from an in-sample backtest."

  },

  "guardrails": {

    "block\_if\_gap\_fit\_untrusted": true,

    "block\_if\_daily\_loss\_limit\_hit": true,

    "block\_if\_max\_trades\_hit": true,

    "block\_if\_max\_concurrent\_hit": true,

    "block\_if\_lot\_ceiling\_would\_be\_exceeded": true,

    "require\_journal\_entry\_per\_trade": true,

    "require\_control\_test\_pass\_before\_live": true

  }

}

---

## 2\. Design Premise

Crash indices exhibit a continuous upward drift interrupted by discrete downward spikes, injected at a documented average tick frequency (e.g. one every 1,000 ticks for Crash 1000). Two setups follow directly:

| Setup | Targets | Status |
| :---- | :---- | :---- |
| **A — Drift Continuation (buy)** | The majority-of-time drift regime | Primary, always-on |
| **B — Jump Entry (sell)** | The discrete spike event | Opt-in, experimental, hard-gated |

Setup A is *reactive* — it trades the regime currently in force. Setup B is *predictive* — it bets on an approaching spike before it happens. That distinction matters: the research in §9 falsifies the predictive claim on Crash 1000 but says nothing against the reactive one.

---

## 3\. Instrument Configuration

| Parameter | Default | Description |
| :---- | :---- | :---- |
| `symbol` | `CRASH1000` | Set per Crash instrument traded |
| `documented_avg_ticks_between_jumps` | 1000 | Platform-disclosed prior; never a timing signal on its own — only an input to the gap-distribution fit (§4) |
| `execution_timeframe` | `1m` | Bar size for signal generation and execution |
| `drift_direction` | `up` | Structural direction of continuous drift — Setup A is long-only |
| `jump_direction` | `down` | Structural direction of the discrete spike |

---

## 4\. Setup A — Drift Continuation Engine

**Regime filter:**

regime\_active \= EMA(fast) \> EMA(slow)

                AND (EMA\_fast \- EMA\_slow) \> min\_ema\_separation\_atr\_multiple × ATR

                AND ADX(adx\_period) \>= min\_adx\_to\_trade

The ADX term is new in v2.0 — a well-worn way to reduce whipsaw entries during choppy stretches without touching the core pullback logic.

**Entry (pullback method):** unchanged from v1.0 — price pulls back to the fast EMA within `pullback_max_distance_atr_multiple × ATR`; a confirmation candle closes back in the drift direction and breaks the prior swing (`swing_lookback_bars`). New: entries block outright once `gap_percentile` exceeds `jump_entry_percentile_threshold`, regardless of whether Setup B is enabled.

**Exit — ATR trailing stop:** unchanged mechanically, with `atr_multiple` now adaptive (tighter in low-vol, wider in high-vol) rather than fixed at 2.0. `hard_take_profit_enabled: false` stays the default — let drift runs run.

---

## 5\. Gap Distribution Engine (shared)

Unchanged from v1.0. Fits candidate distributions (exponential, geometric, erlang, empirical) to observed inter-jump gaps and re-fits every `recompute_every_n_bars`, since Deriv can adjust generator parameters over time.

**Interpretation rule (unchanged, and now empirically supported for Crash 1000 — see §9):** if the best-fit distribution is exponential/geometric, ticks-since-last-jump carries no predictive information for *timing*. Gap-percentile sizing still functions as legitimate risk management (reducing exposure as time-in-trade grows) but must not be logged or treated as a directional signal unless a deviation from memorylessness is validated per §8.

---

## 6\. Setup B — Jump Entry (Sell)

**Status: experimental, opt-in, hard-gated.** This setup bets that gap percentile predicts an approaching spike. Current independent research on Crash 1000 does not support that bet (§9) — treat the default assumption as "no edge" rather than "edge until disproven."

**Trigger:** activates only once `gap_percentile > jump_entry_percentile_threshold` (default 95%). Entry requires a bearish confirmation close breaking the recent swing low (ChoCH). Stop-loss sits tight above the recent swing high.

**Lot-size consequence of the tight stop:** `size = (equity × risk_pct) / stop_distance`. A tighter stop mechanically produces a *larger* lot count for the same risk percentage than Setup A's wider structural/ATR stop. This interacts directly with `challenge_account_lot_ceiling` (§1) — the ceiling must be a hard clamp applied after sizing, not a soft check, or Setup B will reliably push aggregate lots past challenge-provider limits.

**If Setup B is enabled without a passing control test:** don't. `gap_percentile` should instead continue doing what §5 already scopes it for — widening stops, trimming size, and flattening Setup-A positions as the percentile climbs toward `flatten_all_at_percentile`. That's a materially weaker, more defensible claim than a directional trigger: it only requires that a spike costs more the longer a position has run, which holds regardless of whether the spike-arrival process is memoryless.

---

## 7\. Stop Loss / Take Profit / Trailing Stop

Unchanged from v1.0 §8 — structure-or-ATR stop placement with a gap-aware widening multiplier, two scaled take-profits (60%/40% at 1.5R/3.0R), breakeven after TP1, ATR-based trailing activated after 1:1 RR.

---

## 8\. Backtest Controls — Validation Protocol

Unchanged core design (200-run random-parameter control, walk-forward with 10,000-bar windows / 2,000-bar steps, Setup A and B reported separately), plus two additions:

1. **Transaction-cost modeling is now mandatory**, not optional — every backtest report must show net-of-spread P\&L as the headline number, with round-trip spread pulled from the live broker feed where possible (§9 found \~1,430 points round-trip on Crash 1000 as a working default).  
2. **Setup B's control test should follow a pre-registered, kill-gated protocol**, not an ad hoc check: fix the kill thresholds *before* looking at results, test primary hypotheses (KS test vs. exponential, hazard-rate flatness, lag-1 autocorrelation, post-spike-drift-vs-random-window) on your own current tick data, and don't let an exploratory variant rescue a primary failure. A working reference implementation is open-sourced at [github.com/Orphy123/deriv-research](https://github.com/Orphy123/deriv-research) — adapt rather than re-derive.

---

## 9\. Research Basis

Findings that informed the v1.0 → v2.0 changes above:

- **Deriv discloses that Crash indices are generated by a secure/pseudo-random algorithm** producing a continuous drift with periodic discrete spikes at a documented average tick interval per symbol (e.g. \~1,000 ticks for Crash 1000); no dependence on real market news or liquidity. [deriv.com/markets/derived-indices/crash-boom](https://deriv.com/markets/derived-indices/crash-boom), [traders-academy.deriv.com](https://traders-academy.deriv.com/trading-guides/crash-boom-150-derived-indices)  
- **A pre-registered, cost-aware study on 90 days / \~15M ticks of Boom 1000 and Crash 1000** (Oheneba Berko, Apr 2026\) found the spike-arrival process consistent with memoryless (Poisson) behavior on both symbols (KS test could not reject exponential; flat hazard rate; near-zero lag-1 autocorrelation), found no statistically significant difference between post-spike drift windows and random windows across 16 tested configurations, found no persistent hour-to-hour drift-regime structure, and measured round-trip spread (\~1,430 points) large enough to dominate any theoretical drift-capture edge. Open data and code: [github.com/Orphy123/deriv-research](https://github.com/Orphy123/deriv-research). **This is the direct basis for keeping Setup B hard-gated rather than simply toggle-controlled.**  
- **Time-series momentum / trend continuation has a substantial academic base** (Moskowitz, Ooi & Pedersen 2012, and follow-on literature) supporting Setup A's reactive design, though some analyses of that literature note very long sample requirements to reach conventional statistical power — a caution against over-trusting any single short backtest window.  
- **Backtest overfitting is well studied** (Bailey & López de Prado's Deflated Sharpe Ratio / Probability of Backtest Overfitting; Pardo's walk-forward methodology) and supports the walk-forward \+ control-test design already in §8.  
- **Fractional Kelly (¼–½ of full Kelly, recalculated from realized stats) is standard practice** for translating a strategy's realized edge into position size, informing the new `risk_per_trade_pct_mode` in §1.

---

## 10\. Trade Journal / Logging Schema

{

  "timestamp": "ISO8601",

  "symbol": "string",

  "setup": "setup\_a\_drift | setup\_b\_jump",

  "regime\_at\_entry": "drift\_active | ranging",

  "adx\_at\_entry": 0.0,

  "gap\_percentile\_at\_entry": 0.0,

  "gap\_distribution\_fit": "exponential | geometric | erlang | empirical",

  "entry\_price": 0.0,

  "stop\_loss": 0.0,

  "tp\_levels": \[0.0, 0.0\],

  "risk\_pct": 0.0,

  "lot\_size": 0.0,

  "lot\_ceiling\_clamp\_applied": false,

  "spread\_cost\_at\_entry": 0.0,

  "size\_adjustment\_applied": "none | reduced | floored | flattened",

  "rrr\_planned": 0.0,

  "result": "win | loss | breakeven | open",

  "rrr\_realized": 0.0,

  "jump\_event\_during\_trade": true,

  "rejection\_reason": "string | null",

  "notes": "string"

}

---

## 11\. End-to-End Algorithm Flow (Pseudocode)

ON new\_bar(symbol):

    IF NOT "CRASH" in symbol\_upper: RETURN     \# hard Crash-only filter

    update\_atr(symbol); update\_ema\_regime(symbol); update\_adx(symbol)

    update\_gap\_distribution\_if\_due(symbol)

    check\_jump\_event(symbol)

    IF trades\_today \>= max\_trades\_per\_day: RETURN

    IF daily\_risk\_used \>= max\_daily\_risk\_pct: RETURN

    IF in\_cooldown(symbol): RETURN

    gap\_pct \= current\_gap\_percentile(symbol)

    IF setup\_a\_drift\_continuation.enabled AND regime\_active(symbol) AND gap\_pct \< jump\_entry\_percentile\_threshold:

        IF pullback\_and\_confirmation\_triggered(symbol):

            size \= compute\_gap\_aware\_size()

            size \= clamp\_to\_lot\_ceiling(size, symbol)          \# NEW — hard clamp, always applied

            trade \= build\_trade(symbol, "setup\_a\_drift", size)

            IF trade.rrr \>= min\_rrr\_to\_accept\_trade:

                execute\_entry(trade); log\_journal(trade)

    IF setup\_b\_jump\_entry.enabled AND validation\_protocol.control\_test\_passed:

        IF gap\_pct \>= jump\_entry\_percentile\_threshold AND choch\_break\_of\_swing\_low(symbol):

            size \= compute\_tight\_stop\_size()

            size \= clamp\_to\_lot\_ceiling(size, symbol)          \# NEW — same clamp, same hard rule

            trade \= build\_trade(symbol, "setup\_b\_jump", size)

            execute\_entry(trade); log\_journal(trade)

ON open\_position\_update(symbol):

    manage\_multi\_tp(position); manage\_trailing\_stop(position)

    gap\_pct \= current\_gap\_percentile(symbol)

    adjust\_stop\_and\_size\_for\_gap(position, gap\_pct)

    IF gap\_pct \>= flatten\_all\_at\_percentile: close(position)

    IF position.closed:

        update\_consecutive\_loss\_counter(position.result)

        update\_daily\_risk\_used(position)

        log\_journal(position, final=true)

ON scheduled\_interval (weekly):

    run\_control\_test(symbol)                                   \# pre-registered protocol, §8

    validation\_protocol.control\_test\_passed \= result.passed

    validation\_protocol.control\_test\_last\_run \= now()

---

## 12\. Configuration Overrides

| Parameter | Safe Min | Safe Max |
| :---- | :---- | :---- |
| `risk_per_trade_pct` | 0.25 | 2.0 |
| `max_trades_per_day` | 1 | 10 |
| `max_consecutive_losses` | 2 | 6 |
| `min_rrr_to_accept_trade` | 1.2 | 4.0 |
| `atr_multiple` (trailing) | 1.0 | 3.0 |
| `gap_percentile_hard_reduce` | 70 | 95 |
| `flatten_all_at_percentile` | 95 | 99.5 |
| `challenge_account_lot_ceiling.aggregate_max_lots_per_symbol` | — | set to the challenge provider's actual rule; never inferred |

Hard ceilings, unchanged in spirit from v1.0: `risk_per_trade_pct` cannot exceed `max_risk_per_trade_pct`; `setup_b_jump_entry.enabled` cannot be `true` while `require_control_test_pass_before_live` is `true` and no passing control test is on record; computed lot size can never exceed `challenge_account_lot_ceiling.aggregate_max_lots_per_symbol`.

---

## 13\. Glossary

| Term | Meaning |
| :---- | :---- |
| Drift regime | The continuous, structurally-guaranteed directional movement between jump events |
| Jump / spike event | The discrete, large, roughly-periodic price move opposite to the drift direction |
| Gap distribution | Statistical distribution of ticks/bars between jump events, fitted from historical data |
| Gap percentile | Where current time-since-last-jump sits within the fitted gap distribution |
| Memoryless | A distribution (e.g. exponential) where elapsed time carries no predictive information about time-to-next-event |
| Control test | Backtest/statistical comparison used to isolate any real edge beyond known drift+gap mechanics before trusting a signal |
| Consistency rule | Prop-firm payout gate capping a single day's profit as a share of total accumulated profit for the period |
| ChoCH | Change of character — a break of the most recent opposing swing point, used here as a structural confirmation, not a discretionary "smart money" concept |
| RRR | Risk-to-Reward Ratio |

---

*End of specification. Setup A and Setup B remain independently testable modules sharing the gap-distribution engine (§5) as common state. Only the Risk Management Engine (§1) and Guardrails are permitted to block or resize order execution. `setup_b_jump_entry` remains disabled until §8's control test passes on current data for the exact symbol and account in use.*  
