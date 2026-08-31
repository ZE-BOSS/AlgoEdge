15 — Full dual-broker backtest: 274 cells, market open
=======================================================

**2026-08-31, London/NY session** · scripts `full_backtest.py`,
`run_full_backtest.py`, `portfolio_report.py`, `rr_comparison.py`

Own engine, not the app's database or UI. Real strategy classes driven with
correctly-sliced multi-timeframe data, executed on the real bar path, live
spreads, **$10,000 per cell, 1% risk**, RR held at 1:2 / 1:3 / 1:4 / 1:5.

**274 cells. 0 errors. 4,017 signals, 3,692 trades.**

---

## THE HEADLINE — and the trap in it

| view | Deriv | FundedNext |
|---|---:|---:|
| **Best RR chosen per cell** | **+$10,725** | **+$10,395** |
| **RR fixed at 1:2 for the whole book** | **−$20,207** | **−$18,519** |
| RR fixed at 1:3 | −$22,018 | −$18,951 |
| RR fixed at 1:4 | −$21,609 | −$20,434 |
| RR fixed at 1:5 | −$22,548 | −$19,366 |

**The +$10.7k is not a result. It is selection bias.** Picking each cell's best
of four RRs after the fact turns −$22k into +$10.7k — a **$33,000 swing from
pure in-sample fitting**. You could not have known which RR to use in advance.

**Every honest, tradeable configuration loses money on both brokers.**

I am leading with this because the per-cell table looks superb and would be very
easy to act on. It should not be acted on.

---

## 1. Risk-to-reward — higher is better, on both brokers

| RR | Deriv exp R | FundedNext exp R | Deriv WR | FN WR |
|---|---:|---:|---:|---:|
| 1:2 | −0.1338 | −0.1542 | 34.6% | 34.5% |
| 1:3 | −0.1285 | −0.1398 | 28.3% | 28.6% |
| 1:4 | −0.1250 | −0.1238 | 25.3% | 25.7% |
| **1:5** | **−0.1087** | **−0.0901** | 23.4% | 24.1% |

Expectancy improves monotonically with RR on both brokers — **1:5 is the best
setting**, confirming reports 07 and 11 on fresh data and a second venue.

Drawdown is the trade-off: mean maxDD rises 5.37% → 6.74% (Deriv) and 5.15% →
6.24% (FundedNext) going from 1:2 to 1:5. Worst single-cell drawdown reaches
**30.17%** on Deriv at 1:5.

Mean **max daily drawdown is 1.37–1.49%** across every configuration — well
inside a 3% daily limit, and inside FundedNext's typical 5%.

---

## 2. VWAP is the best strategy in the book — which reverses the old verdict

At fixed 1:3, filtering to cells with a **defensible sample (n ≥ 25 trades)** and
positive P&L, **14 of the 16 survivors are VWAP**:

| broker | symbol | strategy | P&L | n | WR | PF | maxDD |
|---|---|---|---:|---:|---:|---:|---:|
| FundedNext | XAGUSD | **VWAP** | **+$2,222** | 28 | 50.0% | 2.25 | 4.65% |
| Deriv | XAGUSD | **VWAP** | **+$1,961** | 28 | 46.4% | 2.11 | 4.35% |
| FundedNext | UKOUSD | **VWAP** | **+$1,779** | 31 | 38.7% | 1.89 | 3.94% |
| Deriv | Crash 1000 | DriftJumpAlpha | +$1,708 | 62 | 35.5% | 1.41 | 12.58% |
| FundedNext | GER30 | **VWAP** | +$1,181 | 46 | 32.6% | 1.37 | 8.03% |
| FundedNext | ETHUSD | **VWAP** | +$1,161 | 27 | 40.7% | 1.69 | 5.14% |
| Deriv | XPTUSD | **VWAP** | +$852 | 29 | 48.3% | 1.36 | 11.52% |
| FundedNext | SPX500 | **VWAP** | +$661 | 28 | 35.7% | 1.36 | 5.67% |
| Deriv | ETHUSD | **VWAP** | +$583 | 27 | 40.7% | 1.33 | 5.30% |
| Deriv | US SP 500 | **VWAP** | +$438 | 27 | 33.3% | 1.25 | 7.18% |
| FundedNext | HK50 | **VWAP** | +$424 | 39 | 33.3% | 1.16 | 7.29% |
| Deriv | Hong Kong 50 | **VWAP** | +$328 | 27 | 33.3% | 1.18 | 4.34% |
| Deriv | Jump 100 | **VWAP** | +$325 | 27 | 29.6% | 1.18 | 9.10% |
| Deriv | Volatility 75 | CRT | +$319 | 26 | 30.8% | 1.18 | 5.40% |
| FundedNext | USOUSD | **VWAP** | +$155 | 30 | 26.7% | 1.09 | 8.70% |
| FundedNext | JP225 | **VWAP** | +$110 | 28 | 28.6% | 1.08 | 11.36% |

**The database sweep had VWAP as the second-worst strategy at −$18,289.** Here it
is the only one that works repeatedly, across two brokers and nine instruments.

**The difference is cost.** VWAP trades frequently, so cost per trade dominates
its result. The old sweep charged 0.15–0.4 R; live trading-hours spreads are
0.017–0.09 R on these instruments. Roughly a 0.1 R saving per trade, on a
strategy averaging −0.16 R, is most of the gap.

**This is the most consequential finding in the whole programme:** VWAP was not
a bad strategy. It was a cost-sensitive strategy being measured against inflated
costs.

*Caveat:* VWAP still loses in aggregate at every fixed RR (−$6,058 to −$2,608
Deriv; −$2,681 to −$1,294 FundedNext) because it also runs on expensive
instruments. The finding is that **VWAP on cheap instruments works**, not that
VWAP works everywhere.

---

## 3. DriftJumpAlpha works on exactly one instrument

| symbol | 1:2 | 1:3 | 1:4 | 1:5 | trades |
|---|---:|---:|---:|---:|---:|
| **Crash 1000** | +$1,139 | +$1,708 | +$2,563 | **+$3,802** | 62 |
| Crash 300 | −$1,076 | −$694 | −$541 | −$1,233 | 62 |
| Crash 500 | −$339 | −$1,067 | −$1,760 | −$2,467 | 64 |

Crash 1000 improves monotonically with RR and returns **+38.0% with 14.5%
drawdown, PF 1.82, Sharpe 3.72** at 1:5 — the single best cell in 274.

**Crash 300 and Crash 500 lose at every RR, and get worse as RR rises.** This
confirms report 10 (Crash 500 loses) and extends it to Crash 300. The earlier
"add Crash 500" recommendation is now doubly refuted.

**Trade Crash 1000 only.**

---

## 4. Broker comparison, same instruments, fixed 1:3

| instrument | Deriv | FundedNext | winner |
|---|---:|---:|---|
| ETHUSD | −$748 | **+$1,145** | FundedNext |
| GER30 | −$60 | **+$950** | FundedNext |
| XAGUSD | +$266 | **+$600** | FundedNext |
| HK50 | +$477 | +$494 | FundedNext |
| SPX500 | +$146 | +$353 | FundedNext |
| AUDUSD | −$688 | −$144 | FundedNext |
| NDX100 | **+$1,958** | +$950 | Deriv |
| XPTUSD | −$836 | −$4,617 | Deriv |
| NTH25 | −$1,755 | −$4,378 | Deriv |
| BTCUSD | −$272 | −$1,061 | Deriv |
| **TOTAL (18 shared)** | **−$14,875** | **−$20,190** | **Deriv** |

Split 10 Deriv / 8 FundedNext. **Deriv wins overall by ~$5,300**, driven by
NDX100, XPTUSD and NTH25. FundedNext is better on ETHUSD, GER30 and silver.

Combined with the cost table in report 14 (median ratio 1.01×), the two venues
are closer than the weekend data suggested — but Deriv still edges it on the
shared book.

---

## 5. Portfolio correlation — Deriv is genuinely diversified, FundedNext is not

**Deriv winner universe — mean |r| = 0.21**

The synthetics are the reason: Crash 1000 correlates **0.00 to −0.07** with
everything, Jump 100 **−0.08 to +0.01**, Volatility 75 **−0.10 to +0.05**. They
are independently generated and provide real diversification. Only XAGUSD/XPTUSD
(+0.88) is redundant.

**FundedNext winner universe — mean |r| = 0.46**, with seven pairs above 0.6:

| pair | r | meaning |
|---|---:|---|
| **UKOUSD / USOUSD** | **+0.96** | Brent and WTI are one trade, not two |
| **NDX100 / SPX500** | **+0.93** | one trade |
| JP225 / NDX100 | +0.83 | |
| JP225 / SPX500 | +0.77 | |
| GER30 / SPX500 | +0.71 | |

**On FundedNext you cannot diversify with indices** — they all move together.
Running VWAP on NDX100, SPX500, JP225 and GER30 looks like four positions and is
close to one, with four times the risk. The only genuine diversifiers there are
oil (−0.4 to −0.6 against indices) and silver (+0.09 to −0.10 against oil).

**Practical consequence:** a FundedNext book should pick **one** index, **one**
oil, and silver — not the whole list. A Deriv book can hold synthetics alongside
real markets and get true independence.

---

## 6. Signal accounting

| | Deriv | FundedNext |
|---|---:|---:|
| signals generated | 2,269 | 1,768 |
| accepted | 1,943 (85.6%) | 1,749 (98.9%) |
| rejected — `min_bars_between_entries` | 325 | 19 |
| rejected — daily trade cap | **0** | **0** |
| rejected — invalid stop | **0** | **0** |

**The daily trade cap never fired once in 274 cells.** Report 10 found it
blocking 77% of DriftJumpAlpha candidates on a 20,000-bar M5 run; on this
8,000-bar M15 window no strategy comes close to 7 trades in a day. The cap is
only binding on high-frequency M5 configurations.

Nine cells produced zero signals — mostly HTFFVGFlip, which remains extremely
selective (1–2 signals on several symbols).

---

## 7. Per-strategy, fixed RR

**Deriv**

| strategy | 1:2 | 1:3 | 1:4 | 1:5 | best |
|---|---:|---:|---:|---:|---|
| **APA** | +$165 | **+$1,329** | −$34 | −$178 | 1:3 |
| DriftJumpAlpha | −$276 | −$53 | +$263 | +$101 | 1:4 |
| HTFFVGFlip | −$889 | −$593 | −$152 | **+$246** | 1:5 |
| BiasIFVG | −$520 | −$1,462 | −$3,409 | −$3,223 | 1:2 |
| NYOpenRetest | −$3,807 | −$4,645 | −$5,843 | −$7,231 | 1:2 |
| VWAP | −$6,058 | −$6,625 | −$2,608 | −$4,582 | 1:4 |
| **CRT** | −$8,823 | −$9,971 | −$9,826 | −$7,682 | 1:5 |

**FundedNext**

| strategy | 1:2 | 1:3 | 1:4 | 1:5 | best |
|---|---:|---:|---:|---:|---|
| **BiasIFVG** | +$138 | **+$1,651** | +$443 | −$421 | 1:3 |
| HTFFVGFlip | −$652 | −$605 | −$120 | **+$377** | 1:5 |
| VWAP | −$1,752 | −$1,423 | −$1,294 | −$2,681 | 1:4 |
| APA | −$4,796 | −$4,891 | −$5,111 | −$4,329 | 1:5 |
| NYOpenRetest | −$5,159 | −$6,734 | −$7,890 | −$5,851 | 1:2 |
| CRT | −$6,298 | −$6,947 | −$6,462 | −$6,462 | 1:2 |

**CRT is the worst strategy on both brokers** (−$9,971 / −$6,947 at 1:3). It has
now failed in the database sweep, the atom analysis, and both live brokers.
**Retire it.**

NYOpenRetest is second-worst on both. Its only good cells are US Tech 100 /
NDX100 — which is what it was designed for. **Restrict it to those two.**

APA and BiasIFVG flip sign between brokers, which on ~20 cells each is noise
rather than a broker effect.

---

## 8. What I would actually trade

Only cells with **n ≥ 25** and positive at a **fixed** RR:

| # | broker | instrument | strategy | RR | P&L | DD |
|---|---|---|---|---|---:|---:|
| 1 | Deriv | **Crash 1000** | DriftJumpAlpha | 1:5 | +$3,802 | 14.5% |
| 2 | either | **XAGUSD** | VWAP | 1:3 | +$2,222 / +$1,961 | ~4.5% |
| 3 | FundedNext | **UKOUSD** *(or USOUSD, not both)* | VWAP | 1:3 | +$1,779 | 3.9% |
| 4 | FundedNext | **GER30** | VWAP | 1:3 | +$1,181 | 8.0% |
| 5 | either | **ETHUSD** | VWAP | 1:3 | +$1,161 / +$583 | ~5% |

Five positions, correlations mostly below 0.5, mean drawdown under 8%.

**Everything else in the 274 should be off.**

---

## 9. Honest limits

- **Window is ~8,000 M15 bars (≈3 months)**, not the full Jan–Aug. Signal counts
  are 1–46 per cell, far thinner than the database sweep's hundreds.
- **No out-of-sample split.** §0.5-6 requires one; these numbers are all
  in-sample. The VWAP finding in particular needs a walk-forward before sizing up.
- **The harness omits session-end exits, time limits and concurrent-position
  caps** that the live engine applies. Cell-to-cell comparison is valid; the
  absolute P&L is not directly comparable to the app's own backtester.
- **Sortino is unreliable** at these sample sizes and should be ignored; Sharpe
  is reported only where ≥5 trading days exist.
- Spreads are a single snapshot taken during one session, applied to the whole
  window.
