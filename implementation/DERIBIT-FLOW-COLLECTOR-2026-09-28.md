# Real options order flow, for free — the collector and what it can eventually answer

**2026-09-28** · `scripts/collect_deribit_flow.py`, `run_deribit_flow_study.py`
· `tests/test_deribit_flow.py` · `data/deribit/`

---

## 0. Why this exists

Two things were established this week and they point in one direction:

- **Option strikes do nothing to the underlying intraday.** Five markets, three spacings each,
  2,500–5,900 approaches per cell, controls throughout, not one significant result
  (`OPTIONS-INTRADAY-2026-09-28.md`).
- **The expiry calendar does something, but it is small and free** — no options data needed
  (`OPTIONS-STRATEGY-2026-09-28.md`).

What remains untested is the question you actually asked: **does an individual call or put being
bought, right now, move the market?** That needs trade-by-trade options data. For US equities it
costs money. For crypto, **Deribit gives it away.**

---

## 1. The data, and why it is better than it looks

Every Deribit option trade carries everything needed:

```
BTC-27NOV26-99000-C   buy   1.0   iv 38.11   index 83045.03
└ strike, expiry, C/P  └ aggressor  └ size     └ implied vol  └ SPOT AT THAT INSTANT
```

Free, no API key, no account.

### The thing this can do that public gamma exposure cannot

Every public GEX number rests on a heuristic. Open interest is observable; **who is long it is
not.** The convention is "customers buy, dealers sell", and the sign of dealer positioning is
*assumed*. That assumption is where most of the error in every public GEX figure lives.

Deribit stamps the **aggressor's side on every print**. When a trade says `buy`, the taker bought
and the maker — the dealer — is short it. **The sign is observed, not assumed.** So dealer
exposure can be built from flow directly:

| Taker does | Dealer holds | Dealer hedges by | Dealer gamma |
|---|---|---|---|
| buys a call | short call | **buying** spot | short (amplifying) |
| buys a put | short put | **selling** spot | short (amplifying) |
| sells a call | long call | selling spot | long (dampening) |
| sells a put | long put | buying spot | long (dampening) |

```
dealer hedge demand = taker_sign × delta × size
dealer gamma        = −taker_sign × gamma × size
```

Deltas and gammas are Black-Scholes at zero rates, from the strike and expiry in the instrument
name, the implied volatility on the trade, and the spot stamped on the trade itself. Nothing is
assumed except zero carry, which over a few days of crypto is immaterial.

---

## 2. The one real limitation

**Deribit's public trade history reaches back about 36 hours.** Probed at 12h, 24h, 48h, 7d, 30d,
90d, 1y and 2y, the first empty window is 48 hours.

So this cannot be backtested today. It can be **collected**, and in a few weeks there is a sample.
That is the whole point of the collector: the only thing standing between here and a real answer
is elapsed time, not money.

---

## 3. What was built

### `collect_deribit_flow.py`

- Pulls every option trade for BTC and ETH, paged forward by timestamp (not by offset — the feed
  is append-only and an offset silently skips trades that arrive mid-page).
- **Append-only, deduplicated on `trade_id`**, one file per UTC day. Re-running fills gaps rather
  than duplicating, so a missed window is recoverable.
- Also snapshots the **full chain with open interest** each pass: trades are a flow, open interest
  is a level, and the gamma picture needs both.
- Backs off and retries on rate limits rather than dropping data silently.

```bash
py -3.12 scripts/collect_deribit_flow.py --seed              # take whatever history is left
py -3.12 scripts/collect_deribit_flow.py --watch --every 900 # keep it current
```

### `run_deribit_flow_study.py`

Prices every trade, aggregates dealer hedge demand and dealer gamma into time buckets, and tests
whether either predicts the next N buckets' return. Prints the sample size and a loud banner when
the span is too short to mean anything.

---

## 4. Seeded, and what it says (nothing yet, by design)

```
DERIBIT OPTION FLOW -> BTC
4,835 trades priced, 2026-09-27 12:50 -> 2026-09-28 12:51 UTC (24.0h)
283 buckets of 300s
```

| Signal | Horizon | n | corr | t | bps/bucket | hit% |
|---|---|---|---|---|---|---|
| dealer hedge demand | 1 | 282 | −0.0544 | −0.91 | +0.15 | 49.6% |
| dealer hedge demand | 3 | 280 | −0.0009 | −0.01 | −0.74 | 52.9% |
| dealer hedge demand | 6 | 277 | +0.0069 | +0.11 | −1.49 | 46.2% |
| dealer hedge demand | 12 | 271 | +0.0117 | +0.19 | −3.11 | 47.2% |
| dealer gamma | 1 | 282 | −0.0323 | −0.54 | +1.07 | 56.7% |
| dealer gamma | 6 | 277 | −0.0263 | −0.44 | +2.18 | 54.9% |
| dealer gamma | 12 | 271 | −0.0435 | −0.71 | +4.16 | 55.0% |
| raw option volume | 1 | 282 | +0.1178 | +1.99 | −0.63 | 48.2% |

Dealer gamma split 49% long / 51% short across buckets — plausible, and a sanity check that the
signs are not inverted.

**Read none of this as a finding.** 24 hours is 283 buckets of one day's tape. The single
borderline cell (raw volume at t +1.99) is one of twelve on one day. The script prints a banner
saying so and will keep printing it until the span reaches weeks.

**What it does prove** is that the pipeline runs end to end on real data: 4,835 trades parsed,
priced, signed and bucketed without a single unhandled case.

---

## 5. The tests, and the one bug they exist to prevent

`tests/test_deribit_flow.py`, **24 tests**. A sign error here would not crash or look odd — it
would **invert every conclusion the study ever reaches, silently.** So the convention is pinned
directly:

- taker buys a call → dealer hedge **positive**, dealer gamma **negative**
- taker buys a put → dealer hedge **negative**, dealer gamma **negative**
- selling mirrors buying exactly
- a bought straddle nets near-zero delta and clearly negative gamma — the case that separates the
  two measures

Plus: instrument names parse (including single-digit days and puts) or are **refused rather than
guessed**; put-call parity holds on delta to 1e-9; gamma is identical for a call and put at the
same strike; deltas saturate at ±1 and 0; degenerate inputs return `None`; and **a trade that
cannot be priced is dropped, not counted as zero flow** — the quiet way a study gets diluted.

---

## 6. What happens next

The collector has to actually run. Three options, in order of how much I would trust them:

1. **A Windows scheduled task** every 15 minutes running `--watch` or a single pass. Persistent
   across reboots. I have not created one — that is a change to your machine and it is your call.
2. **A terminal left open** with `--watch --every 900`. Simplest, dies with the window.
3. **Manual** `--seed` every day or two. The 36-hour window means anything longer than that loses
   data permanently.

Each pass is two HTTP requests per currency and a few hundred KB a day. There is no rate-limit
risk at fifteen-minute intervals.

**When there are four weeks of data**, `run_deribit_flow_study.py` becomes a real test, the banner
disappears, and the question is answerable: does dealer hedge demand lead BTC, on what horizon,
and by enough to trade after costs. If it does on BTC — where the data is free — that is the first
evidence that would justify paying for the equity version.

---

## 7. Reproduction

```bash
py -3.12 scripts/collect_deribit_flow.py --seed
py -3.12 scripts/run_deribit_flow_study.py
py -3.12 scripts/run_deribit_flow_study.py --max-dte 2    # 0DTE/near-expiry only
py -3.12 -m pytest tests/test_deribit_flow.py -q
```
