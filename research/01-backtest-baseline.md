# 01 — Backtest baseline

**Stage A** · produced 2026-08-30 · source: `algoedge.db`, 116 runs / 7,463 trades
Scripts: `research/data/tp_sweep.py`, `walk_forward.py`

---

## What was actually run

The sweep is **complete across strategies and assets** and the configuration is
consistent across all 116 runs — verified by reading `params_snapshot` on every
row, not by assuming:

| setting | §0.1 target | actually run | |
|---|---|---|---|
| Starting balance | $10,000 | $10,000 | ✅ |
| `risk_per_trade_pct` | 1.0% | 1.0% | ✅ |
| `max_risk_hard_cap_pct` | 2.0% | 2.0% | ✅ |
| `max_daily_drawdown_pct` | 3.0% | 3.0% | ✅ |
| `max_weekly_drawdown_pct` | 6.0% | 6.0% | ✅ |
| `max_daily_trades` | 7 | 7 | ✅ |
| `tp_count` / `tp1_rr` | 1 / 1:5 | 1 / 1:5 | ✅ |
| Trailing | on **and** off | **off only** (115/116) | ❌ |
| Bars per run | 50,000 | **5,000** | ❌ (see bug B6) |
| Break-even | — | **BE at 1R, +5 pip buffer** | ⚠️ undeclared in §0.1 |

Window: **2026-01-01 → 2026-08-27** for every run — one shared calendar period,
which makes cross-strategy comparison cleaner than the 50,000-bar rule would
have (that rule gives each strategy a different window). Worth keeping.

**Coverage.** 19 symbols × 6 strategies + DriftJumpAlpha × 2 Crash symbols.
Missing versus §0.2: **AUDUSD** (never run), **Crash 500 Index** and
**Jump 100 Index** (never run — both confirmed present on the terminal).

---

## Headline

| | |
|---|---|
| Net P&L, all 116 runs | **−$92,102** |
| Profitable runs | **23 of 116** (20%) |
| Expectancy | **−0.180 R/trade** |
| Win rate | 35.8% |
| Profit factor | 0.72 |
| Average max drawdown | 15.9% (worst: 86.0%) |

## Per strategy

| strategy | n | exp R | total R | net $ | WR | PF | P(1R) | P(5R) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DriftJumpAlpha** | 696 | **+0.249** | **+173.4** | **+$12,207** | 52.7% | **1.56** | 55.9% | 9.2% |
| HTFFVGFlip | 183 | −0.085 | −15.6 | −$1,357 | 37.7% | 0.86 | 52.5% | 9.8% |
| VWAP | 2,186 | −0.159 | −347.5 | −$18,289 | 38.2% | 0.72 | 40.1% | 3.2% |
| APA | 1,236 | −0.177 | −219.4 | −$15,781 | 28.3% | 0.74 | 50.0% | 10.5% |
| BiasIFVG | 1,108 | −0.193 | −214.4 | −$15,487 | 31.8% | 0.71 | 50.5% | 9.3% |
| NYOpenRetest | 1,164 | −0.331 | −385.7 | −$29,704 | 33.2% | 0.56 | 47.2% | 7.8% |
| CRT | 890 | −0.376 | −334.5 | −$23,691 | 34.8% | 0.50 | 45.5% | 6.4% |

**P(1R) is the number to read.** It is the stop-aware probability that a trade
reaches +1R before its stop. A coin flip with no cost is 50%. Five of seven
strategies sit at 40–52% — statistically indistinguishable from no directional
edge at all. DriftJumpAlpha at 55.9% is the only clear separation.

## Per asset class (net R / trades)

| strategy | crypto | index | fx | metal | synth | total |
|---|---:|---:|---:|---:|---:|---:|
| APA | −15.9 / 528 | −11.0 / 165 | −61.6 / 306 | −22.0 / 127 | −108.8 / 110 | **−219.4** |
| BiasIFVG | −60.5 / 320 | −48.1 / 253 | −92.4 / 325 | **+46.2 / 133** | −59.5 / 77 | **−214.4** |
| CRT | −75.5 / 143 | −61.7 / 224 | −183.5 / 315 | −24.1 / 81 | +10.3 / 127 | **−334.5** |
| DriftJumpAlpha | — | — | — | — | **+173.4 / 696** | **+173.4** |
| HTFFVGFlip | −11.8 / 50 | −9.1 / 42 | +1.5 / 64 | +2.0 / 18 | +1.8 / 9 | −15.6 |
| NYOpenRetest | −128.2 / 243 | −69.7 / 324 | −136.8 / 342 | −32.4 / 174 | −18.6 / 81 | **−385.7** |
| VWAP | −222.9 / 795 | −59.4 / 522 | −25.0 / 246 | −65.7 / 434 | +25.4 / 189 | **−347.5** |
| **total** | **−514.8** | **−259.0** | **−497.8** | **−96.1** | **+24.1** | |

Crypto and FX are the two big sinks. Note VWAP on crypto: −222.9R on 795
trades, the single worst cell group in the book — and §0.2 already flagged VWAP
as unsuitable for instruments without institutional order flow.

---

## Where the money actually goes

Replacing every SL exit with exactly −1.00R and every TP with exactly +5.00R —
i.e. removing all spread, slippage and gap cost, changing nothing else:

| | R/trade | total R |
|---|---:|---:|
| actual | −0.180 | −1,343.5 |
| frictionless | −0.064 | −479.3 |
| **friction** | **−0.116** | **−864.2** |

**64% of the loss is friction, not strategy.** Stops fill at an average of
**−1.199R** rather than −1.000R, and 5R targets fill at **+4.732R**.

The overrun tracks the cost model closely where a cost model resolved:

| symbol | modelled round-trip / median stop | observed stop overrun |
|---|---:|---:|
| XRPUSD | 0.299 R | **0.350 R** |
| SOLUSD | 0.341 R | 0.324 R |
| EURGBP | 0.266 R | 0.315 R |
| ETHUSD | 0.251 R | 0.308 R |
| BTCUSD | 0.180 R | 0.276 R |
| XAUUSD | 0.048 R | 0.118 R |
| Crash 300 | 0.026 R | **0.012 R** |

Two distinct sources:

1. **Spread relative to stop distance.** Crypto stops are small relative to the
   quoted spread, so 25–35% of every 1R is paid to the broker. This is the
   dominant term and it is the reason crypto is the worst class in the book.
2. **Gap-through on stops.** Germany 40 (0.217R), Hong Kong 50 (0.195R) and
   Netherlands 25 (0.158R) overrun by more than their spread alone explains —
   cash indices gapping across the session break.

> **Correction (2026-08-30).** This paragraph originally said those three had a
> *zero* modelled spread and that their cost model was "suspect". That was my
> error, not a defect: the engine keys `resolved_cost_model` by `symbol.upper()`
> and my lookup used the exact symbol string, so it missed every symbol with a
> space in its name. **All 21 symbols have a resolved cost model.** See
> `debug/backtest-bugs.md` B8/B9 (both retracted). The overrun figures in the
> table above are unaffected — they come from `pnl_r` alone.

`min_stop_spread_multiple` exists as a parameter and is **`null`** in all 116
runs — nothing enforces a minimum stop-to-spread ratio.

## Exit behaviour

| exit reason | n | share | avg R | total $ |
|---|---:|---:|---:|---:|
| SL | 3,631 | 48.7% | −1.199 | −$302,523 |
| BE_SL | 2,285 | **30.6%** | +0.004 | −$587 |
| SESSION_END | 819 | 11.0% | +0.471 | +$21,088 |
| TP1 (5R) | 532 | 7.1% | +4.732 | +$180,604 |
| APA_HEAD_INVALIDATION | 84 | 1.1% | −0.930 | −$6,091 |
| TIME_LIMIT | 80 | 1.1% | +1.855 | +$13,766 |

**42.7% of trades reach +1R.** Of those, 71.7% end at break-even for +0.004R.
Those 2,285 trades reached a **median 1.76R** (p75 2.49R, p90 3.55R) before
returning to entry — 24.9% of them got past 2.5R and still finished at zero.

That is the most conspicuous single leak in the book, and Stage G takes it up.
Note it cannot be measured away from this data: once BE closed the trade, the
path after it is unobservable, so "what if BE were off" needs a re-run.

---

## Status against the Stage A checklist

| item | status |
|---|---|
| A.1 clean slate | ✅ single consistent config, one window |
| A.2 watchable on the UI | ❌ broken — bugs **B4**, **B5** |
| A.3 full grid, trailing **off** | ✅ 116 runs |
| A.3 same grid, trailing **on** | ❌ never run |
| A.3 record calendar window | ✅ 2026-01-01 → 2026-08-27 |
| A.3 persist gate vectors | ❌ recorder disabled — bug **B7** |
| A.3 persist MFE/MAE in R, exit reasons | ✅ complete, zero nulls on 7,463 rows |
| A.4 log bugs, don't fix | ✅ B1–B10 (B8, B9 later retracted) |
| A.5 report | ✅ this file |

The MFE/MAE and exit-reason data is the reason Stages B–G could be answered at
all. It is complete and clean on every one of the 7,463 trades.
