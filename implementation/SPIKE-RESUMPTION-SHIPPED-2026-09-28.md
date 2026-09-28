# SpikeResumption_v1 — built into the system, and what the optimisation found

**2026-09-28** · `backend/strategies/strategy_spike_resumption/` ·
`scripts/run_spike_optimise.py`, `run_spike_per_asset.py` · `tests/test_spike_resumption.py`

---

## 0. Built as asked. Here is what it does and what the evidence says.

It is wired through every layer, parity-tested across single backtest, portfolio backtest and
live, and **enabled on nothing by default.** The optimisation did not find a version that holds
up, and the reason is specific enough to be worth reading before you allocate to it.

---

## 1. Per asset, January 2026 to date — the breakdown you asked for

**Settings:** 0.5% risk per trade · **compounding ON** (risk is % of the *current* balance) ·
**no pyramiding** (one position per symbol; the next cannot open until the previous closes) ·
stop 1×ATR at the spike bar · target back past the spike · max hold 5 bars · λ spike-fills on.

| Asset | Trades | $10,000 → | Return | Max DD | Win% | PF | Exp. R | Exp. $ | Realised R:R | t |
|---|---|---|---|---|---|---|---|---|---|---|
| **Crash 1000** | 21 | $11,192 | **+11.92%** | $213 (2.0%) | 71.4% | 3.51 | +1.084 | +$56.74 | 1.39 | **+2.68** |
| Crash 900 | 12 | $10,098 | +0.98% | $229 (2.2%) | 58.3% | 1.23 | +0.174 | +$8.19 | 0.89 | +0.31 |
| Boom 500 | 4 | $10,098 | +0.98% | $57 (0.6%) | 75.0% | 2.72 | +0.494 | +$24.56 | 0.92 | +0.65 |
| Crash 500 | 2 | $10,043 | +0.43% | $65 (0.7%) | 50.0% | 1.66 | +0.433 | +$21.30 | 1.67 | — |
| Boom 300 | 1 | $9,949 | −0.51% | $51 (0.5%) | 0% | 0.00 | −1.022 | −$51.09 | — | — |
| Boom 1000 | 8 | $9,922 | −0.78% | $304 (3.0%) | 37.5% | 0.79 | −0.188 | −$9.72 | 1.33 | −0.29 |
| **Boom 900** | 17 | $9,465 | **−5.35%** | $542 (5.4%) | **23.5%** | **0.38** | −0.641 | −$31.45 | 1.24 | −1.75 |
| Crash 300 | 0 | — | no trades | | | | | | | |

**Boom 900 — the instrument in your screenshots — is the worst of the eight.** The positive
pooled figure I reported earlier was Crash 1000 carrying seven other symbols, and pooling hid
exactly the fact you needed.

**On the looser baseline settings** (2×ATR stop), which produce far more trades:

| Asset | Trades | Return | Win% | PF | t |
|---|---|---|---|---|---|
| Crash 1000 | 58 | **+4.12%** | 60.3% | 1.37 | +1.08 |
| Boom 500 | 10 | +0.72% | 70.0% | 1.76 | +0.68 |
| Crash 500 | 12 | −0.01% | 58.3% | 1.00 | — |
| Crash 300 | 3 | −0.09% | 33.3% | 0.77 | −0.15 |
| Boom 300 | 3 | −0.22% | 33.3% | 0.63 | −0.27 |
| Crash 900 | 34 | −0.42% | 52.9% | 0.94 | −0.13 |
| **Boom 900** | 43 | **−2.34%** | 51.2% | 0.77 | −0.73 |
| **Boom 1000** | 41 | **−6.91%** | 43.9% | **0.46** | **−2.20** |

Boom 1000 is *significantly negative*.

### Your specific questions

- **Risk per trade:** 0.5% of the current balance. Scaling is linear because there is no
  pyramiding — Crash 1000 / Boom 900 at 0.5% → +11.92% / −5.35%; at 1% → +24.98% / −10.51%;
  at 2% → +54.81% / −20.27%.
- **Compounding:** ON. It barely matters over nine months at this size — Crash 1000 gives +4.12%
  compounded against +4.11% flat.
- **Pyramiding:** none. One position per symbol at a time.
- **Reward-to-risk:** there is no fixed R:R — the target is a price level (back past the spike),
  so each trade's ratio is whatever the distance happens to be. The **realised** ratio (average
  win ÷ average loss) is in the table: 1.39 on Crash 1000, 1.24 on Boom 900. Boom 900 loses
  *despite* a 1.24 payoff because it only wins 23.5% of the time.

---

## 2. The optimisation, and the result that settles it

`scripts/run_spike_optimise.py`. Ten knobs swept one at a time, then eight combinations, chosen
on 2024-09 → 2026-01 and reported unchanged on 2026-01 → today. New knobs over the first pass:
spike measured by **body or range**, a requirement that the confirming bar **close back past
where the spike started**, a **minimum gap between spikes**, and an optional **chandelier trail**.

**Every variant flips sign between the two windows, and the two families flip opposite ways:**

| Variant | Boom IN | Crash IN | Boom OUT | Crash OUT |
|---|---|---|---|---|
| base | **+0.335** | −0.094 | **−0.382** | **+0.735** |
| range spike | +0.227 | −0.171 | −0.335 | +0.320 |
| trail 1×ATR | +0.178 | −0.245 | −0.385 | +0.324 |
| gap 20 bars | +0.289 | −0.105 | −0.382 | +0.735 |
| tight stop 0.5 | +0.434 | −0.424 | −0.489 | +1.063 |
| hold 3 | +0.370 | −0.095 | −0.216 | +0.398 |

**Eight for eight.** Whichever family worked in one window failed in the next, under every
parameterisation tried. That is not an asymmetry between Boom and Crash — it is noise, and the
"Crash works, Boom does not" conclusion I drew from the per-asset table above is itself an
artefact of looking at one window.

The shifted-entry control is negative in every out-of-sample cell (−0.107 to −0.603), so the
setup does something; it just does not do it reliably enough to allocate to.

And on the longest history available — Boom/Crash 1000, 2021-10 → 2024-09, three years the
parameters were never fitted to — the rule loses **0.098R per trade, −6.1%**.

### Two encoding choices tested, both making no difference

You flagged these as places I might have mis-read your description. Both were swept:

- **Spike as range rather than body** — more trades (94 vs 64 on Boom in-sample), same sign flip.
- **Requiring the confirming bar to close back past the spike's open** — too few trades to
  evaluate at 3×ATR, on either family.

A third, **entering mid-candle rather than at the close**, cannot be tested the same way: the app
fills at the next bar's open and has no intrabar entry, so a mid-candle rule would be measuring a
fill the system cannot get.

---

## 3. What was built

| Layer | What |
|---|---|
| Strategy | `backend/strategies/strategy_spike_resumption/` — `params.py` (10 documented parameters), `engine.py` |
| Direction | Read from `fill_model.SPIKE_FILLS`, not guessed from the symbol name. A market with no measured spike side, or one that spikes both ways (Jump, Range Break), gets **no signal at all** |
| Exit | `MaxHoldExit` — the bar-count exit every path counts for itself |
| Registry | `strategies/registry.py` |
| Config | `core/config_schema.py` — `spike_resumption` block, `from_dict`, `None` guards |
| Schema | `core/schema_introspection.py` — the slot editor renders all 10 parameters from their own docstrings |
| Route | `api/routes/backtest.py::STRATEGY_PARAM_SECTION` |
| Defaults | `strategies/strategy_defaults.py` — one target, no break-even, no generic trailing, and an evidence string that opens **"NO RELIABLE EDGE"** |
| Frontend | `slotSpec.js`, `StrategyLab.jsx` |
| Tests | `tests/test_spike_resumption.py` — **24 tests** |

**Deliberately absent:** no entry in `SLOT_TP1_RR` and none in `SYNTH_SLOT_PARAMS`. Recommending
a symbol would imply a measurement supporting one, and there is not one. A test asserts both stay
empty.

### What the tests cover

- **Direction.** Boom sells after an up-spike, Crash buys after a down-spike. Inverting this
  turns a strategy that does not work into one that loses fast, and no backtest shape would
  announce it.
- **Which bar's ATR sets the stop** — the **spike** bar's, not the entry bar's. They differ most
  when the spike was large, which is every trade this strategy takes.
- Every gate: spike too small, two spikes in a row, a green confirming bar, an upward drift
  blocking a Boom sell, `require_below_spike`, `min_bars_since_spike`, `confirm_bars=2`.
- All three trend modes agree on a clean fixture.
- The bar-count exit fires at the right bar and is live-enabled.
- **Single vs portfolio engine produce identical trades** — same exit reason, same entry, exit and
  stop prices to 1e-12.
- Wiring: registry, schema group, config block, `STRATEGY_PARAM_SECTION`, `slotSpec.js`.

### One behaviour pinned deliberately

The trend test is evaluated **at the spike bar**, so a spike large enough to undo the whole
lookback flips the trend reading and the setup is refused. The research harness does exactly
this, so the engine reproduces it — "fixing" it would make the app trade something that was never
measured. `test_the_trend_test_sees_the_spike_bar_itself` holds it there.

---

## 4. How to run it, if you want to

1. Point MT5 at Deriv, or the Boom/Crash symbols will not resolve.
2. Create a slot: symbol + `SpikeResumption_v1`.
3. **Set `stop_fill_model` to `EMPIRICAL`.** Non-negotiable on these instruments — every entry
   sits one bar from a spike, which is exactly where a bar backtest lies most. With the naive
   model the numbers are fiction: random Boom shorts booked +0.76R a trade against −0.03R on real
   ticks.
4. Note Boom 900 and Crash 900 were **not** in the tick study. Their λ is set to the family's
   most adverse measured value (0.80) and marked assumed. Before this work they were missing from
   the table entirely, so the app was filling their spike-side stops *at* the stop.

On the evidence, if you run it at all: **Crash before Boom, and small.**

---

## 5. Reproduction

```bash
py -3.12 scripts/run_spike_per_asset.py --since 2026-01-01
py -3.12 scripts/run_spike_optimise.py
py -3.12 scripts/run_spike_resumption.py --combo --placebo --markets "Boom 1000 Index" "Crash 1000 Index" --since 2021-10-01 --until 2024-09-01
py -3.12 -m pytest tests/test_spike_resumption.py -q
```
