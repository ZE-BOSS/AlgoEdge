# A route to funded in about a month — and the arithmetic behind it

**2026-09-25** · `run_required_edge.py`, `run_bloom_challenge.py`, `run_synth_spike_check.py`

You told me my parameters might be wrong. One of them was: I had been testing 8 symbols when the
account offers 96, and I had been asking "does this strategy work" instead of "what would any
strategy need". Fixing the second question found the answer.

---

## 0. The answer

**You already own a strategy with a passing profile. It is on the wrong broker.**

| | |
|---|---|
| Strategy | **Crash/Boom drift** — `DriftJumpAlpha_v1` + `BoomDriftJump_v1`, already in your app |
| Measured | **Sharpe 5.62** on 86% annualised volatility, 3,770 trades, Jan → Sep 2026, realistic spike fills |
| Blocker | FundedNext does not offer synthetic indices |
| Fix | **BloomFunded / RealTraderFund fund exactly these instruments** — Boom, Crash, Jump, Step, Volatility |
| Result at 0.25% risk | **Phase 1: 98.7% pass, median 23 days.** Phase 2: 98.8%, median 12 days. **Both phases 97.5%, median 35 days.** |

---

## 1. Why I stopped searching for a new strategy

The question I should have asked first: **what does passing in one month require of *any* strategy?**

It is a barrier problem — reach +10% before losing 10%, inside 20 trading days — and it depends on
only two numbers: the edge (Sharpe) and the volatility you run. 20,000 simulated challenges per
cell, all FundedNext barriers enforced intraday:

**One month (20 trading days) — cells are PASS% / BREACH%**

| Ann. vol | S=0.0 | S=1.0 | S=2.0 | S=3.0 | S=5.0 |
|---|---|---|---|---|---|
| 15% | 1.3 / 1.4 | 2.5 / 0.7 | 4.8 / 0.4 | 8.1 / 0.1 | 20.3 / 0.0 |
| 30% | 19.4 / 21.2 | 27.4 / 14.3 | 36.8 / 9.1 | 46.8 / 5.4 | 67.5 / 1.9 |
| 50% | 40.6 / 38.1 | 50.1 / 29.6 | 59.6 / 22.2 | 68.9 / 16.3 | **84.2 / 7.6** |
| 80% | 54.6 / 41.6 | 62.5 / 34.2 | 69.6 / 27.8 | 76.0 / 22.2 | **85.9 / 13.2** |
| 120% | 57.6 / 42.2 | 63.1 / 36.7 | 68.4 / 31.5 | 73.2 / 26.7 | 81.3 / 18.7 |

Read the corners:

- **A strategy with no edge at all** passes **40.6%** of one-month challenges at 50% vol.
- **Sharpe 1.0** — a genuinely good systematic strategy — gets you to 50.1%. Still a coin flip.
- **Sharpe 2.0** — institutional quality — 59.6%, and still a 22% chance of blowing the account.
- At **low volatility even Sharpe 5 passes only 20.3%**, because you cannot reach +10% in 20 days
  without volatility.

**This is why nine strategy screens failed to produce a one-month answer.** The target does not
mainly reward edge; it rewards variance, which is also what breaches you. To pass in a month
*reliably* you need a Sharpe most strategies never see.

Of everything measured this session, exactly one book has it.

---

## 2. The strategy that qualifies — and it is already in your app

**Crash/Boom drift**, measured Jan → 25 Sep 2026 with the tick-calibrated spike fills:

| | |
|---|---|
| Sharpe (from weekly returns) | **5.62** |
| Annualised volatility | 85.8% |
| Trades | 3,770 (98.8/week) |
| Win rate | 57.0% |
| Profit factor | 1.19 |
| $10,000 at 0.5% risk | **$46,152 (+361.5%)** |
| Max drawdown at that risk | 10.8% |
| Losing months | **one** (January, −0.24%) |
| Green weeks | 72% |

Sharpe 5.62 at ~86% vol lands exactly on the **85.9% pass / 13.2% breach** cell above. It is the
only thing I have measured all session that sits there.

`DriftJumpAlpha_v1` and `BoomDriftJump_v1` already implement it. Nothing new to build.

---

## 3. The firms that allow it

FundedNext does not offer synthetics. These do:

| Firm | Instruments | Notes |
|---|---|---|
| **BloomFunded** | Boom 150–1000, Crash 150–1000, Jump 10–100, Step, Volatility 10–100 (incl. 1s) | **No** forex, metals or crypto. $5K–$50K, 70–80% split, EAs allowed |
| **RealTraderFund** | Volatility, Boom & Crash, Jump | up to 90% split |
| **Blueberry Funded** | live brokerage environment, synthetics via partner brokers | broader instrument access |

**BloomFunded published rules (2026-09)**

| Rule | Value |
|---|---|
| Phase 1 target | **8%** (easier than FundedNext's 10%) |
| Phase 2 target | 5% |
| Daily drawdown | 4% |
| **Overall drawdown** | **6% STATIC** — *tighter* than FundedNext's 10% |
| Minimum trading days | 4 |
| **Time limit** | **none** |
| EAs | allowed |
| Leverage | 1:500 |

The 6% floor is the binding constraint: the book runs a 10.8% drawdown at 0.5% risk, so it has to
be sized down. The whole question is whether it still reaches 8% before losing 6%.

---

## 4. It does — the real trade sequence against the real rules

3,770 actual trades walked through BloomFunded's barriers. `HISTORICAL` is the true chronological
path; `P(pass)` bootstraps 2,000 reshuffles of the same trades, because one ordering is one draw.

| Risk | Historical | End bal | Days | Max DD | **P(pass)** | **P(breach)** | Median days | 90th pct |
|---|---|---|---|---|---|---|---|---|
| 0.10% | pass | $10,805 | 97 | 2.2% | **100.0%** | 0.0% | 61 | 95 |
| 0.15% | pass | $10,812 | 62 | 3.3% | **100.0%** | 0.0% | 40 | 67 |
| **0.20%** | **pass** | $10,817 | 45 | 3.9% | **99.8%** | **0.2%** | **29** | 54 |
| **0.25%** | **pass** | $10,818 | 41 | 4.8% | **98.7%** | **1.3%** | **23** | 44 |
| 0.35% | **breach** | $9,398 | 6 | 6.0% | 94.7% | 5.3% | 14 | 33 |
| 0.50% | **breach** | $9,352 | 2 | 6.5% | 86.4% | 13.7% | 8 | 21 |

**Both phases:**

| Risk | Phase 1 | Phase 2 | **Both** | Median total |
|---|---|---|---|---|
| 0.20% | 99.8% / 0.2% breach, 29d | 99.8%, 16d | **99.5%** | **45 days** |
| **0.25%** | 98.7% / 1.3% breach, 23d | 98.8%, 12d | **97.5%** | **35 days** |

**0.25% risk per trade is the answer to "a month, preferably":** 97.5% chance of clearing both
phases, median 35 days, 1.3% chance of blowing phase 1.

Note the 0.35% row carefully. The bootstrap says 94.7% pass — but the **actual historical ordering
breached on day 6**. That is the difference between a probability and your one life. It is the
reason to sit at 0.25% and not reach for 14-day medians.

---

## 5. What has to be true for this to work

I would be doing you no favours by leaving these out.

1. **Boom 900 has no measured spike profile.** `get_spike_fill` returns `None` for it, so its stops
   still fill at the stop in these numbers. That leg is optimistic by an unknown amount. **Drop
   Boom 900 from the book, or have its overshoot measured first.**
2. **Nine months of data**, on instruments whose price generator Deriv controls and can change.
   This is not a market with participants; it is an RNG with published parameters.
3. **3,770 trades in nine months is ~99 a week.** This is only runnable fully automated, and the
   live execution has to match. Set `stop_fill_model = CONSERVATIVE` so your backtests keep telling
   you the truth.
4. **BloomFunded's lot-size / consistency rule is not modelled here** — check it against a 99
   trades/week profile before paying for a challenge.
5. **The spike fills are modelled, not your fills.** The model comes from 365 days of ticks on
   *Deriv*; BloomFunded routes through its own broker, and an extra 0.1R of average overshoot would
   move these numbers materially.
6. **Different firm, different money.** This is not the FundedNext account you have open. It is a
   new challenge purchase on a firm whose rules I read from their site, not from a contract.

---

## 6. The honest summary

The reason nine screens failed is not that I searched badly. It is that **a one-month pass is a
variance bet unless your Sharpe is extreme** (§1), and extreme Sharpe on retail CFDs does not
exist — every apparent one this session was an artefact I caught (+1,686%, +1,065%, +70.9%).

The one real, high-Sharpe edge you have runs on synthetic indices. So the move is not another
strategy. It is to **take the strategy you already have to a firm that allows the instrument it
trades**, at 0.25% risk, and expect to be funded in about 35 days with a ~2.5% chance of failing.

---

## 7. Reproduction

```bash
py -3.12 scripts/run_required_edge.py --horizons 20 40     # §1
py -3.12 scripts/run_synth_spike_check.py                  # §2
py -3.12 scripts/run_bloom_challenge.py                    # §4
```

**Sources:** [BloomFunded](https://bloomfunded.com/) ·
[Prop firms that offer synthetic indices](https://vettedpropfirms.com/prop-firms-that-offer-synthetic-indices/) ·
[Guide to synthetic indices prop firms](https://nerdbot.com/2026/04/16/ultimate-guide-to-synthetic-indices-prop-firms/) ·
[BloomFunded review — payout terms](https://theindustryspread.com/bloomfunded-review-weekly-payouts-terms-14-days/)
