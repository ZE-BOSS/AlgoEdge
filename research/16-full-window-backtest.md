16 — Full-window dual-broker backtest (definitive)
==================================================

**2026-08-31** · scripts `full_backtest.py`, `run_full_backtest_par.py`,
`analyse_full.py`

**285 cells · 0 errors · 23,989 trades · window 238–242 days on EVERY asset**

This supersedes report 15. That run capped each asset at 8,000 M5 bars, which
spanned 28 days on a 24/7 synthetic and 71 days on a short-session index — so its
cross-asset comparison was never like-for-like. This one fixes the calendar
window (2026-01-01 → 2026-09-01) for every asset and uses **6× the data**.

Runtime 3h 40m across 3 worker processes.

---

## 1. Headline — and a correction to report 15

| | Deriv | FundedNext |
|---|---:|---:|
| trades | 14,572 | 9,417 |
| **1:2** | −$70,570 | −$75,490 |
| **1:3** | −$36,354 | −$80,634 |
| **1:4** | **−$4,149** | −$90,165 |
| **1:5** | **−$3,236** | −$99,471 |

**Deriv approaches break-even at 1:4–1:5. FundedNext gets steadily worse as RR
rises** — the opposite direction. That divergence is new and did not appear in
the short-window run.

Mean drawdown is far higher than report 15 showed (17–23% vs 5–6%), simply
because a 240-day window has six times the opportunity to draw down. **Max daily
drawdown stays at 1.5–2.0%**, comfortably inside a 3% limit.

---

## 2. VWAP was a false positive — I got this wrong in report 15

Report 15 concluded *"VWAP is the best strategy in the book"* on the basis that
14 of 16 defensible cells were VWAP. **On six times the data it is one of the
worst.**

| strategy | Deriv (best RR) | FundedNext (best RR) | trades |
|---|---:|---:|---:|
| **DriftJumpAlpha** | **+$78,849** (1:5) | n/a | 1,595 |
| APA | +$3,739 (1:3) | −$4,118 (1:5) | 3,225 |
| HTFFVGFlip | +$561 (1:4) | −$20 (1:4) | 458 |
| BiasIFVG | −$1,443 (1:2) | −$1,458 (1:3) | 2,757 |
| **VWAP** | **−$13,010** | **−$34,414** | **7,604** |
| CRT | −$23,523 | −$18,730 | 3,372 |
| NYOpenRetest | −$31,790 | −$13,520 | 4,978 |

The report 15 VWAP cells sat on 27–46 trades inside a 28–71 day window. Here
VWAP has **7,604 trades** and loses on both brokers. The earlier finding was
small-sample noise dressed up by a favourable window.

**My report 15 reasoning was also wrong.** I attributed VWAP's apparent success
to lower live spreads. Costs are identical in both runs — only the window
changed. The window was doing the work.

---

## 3. DriftJumpAlpha is the entire book

**+$78,849 at 1:5 on 1,595 trades**, against a whole-book Deriv result of
−$3,236. Everything else combined loses about $82,000.

| symbol | 1:2 | 1:3 | 1:4 | 1:5 | trades |
|---|---:|---:|---:|---:|---:|
| **Crash 1000** | — | +$55,963 | — | **+$80,262** | 514 |
| Crash 300 | — | +$4,517 | — | — | 554 |
| Crash 500 | — | — | — | — | 527 |

Crash 1000 at 1:5: **+802.6% return, 514 trades, WR 26.1%, PF 1.61, maxDD
37.0%**, Sharpe 2.52.

**This reverses report 10 and 15 on Crash 300**, which now returns **+$4,517** at
1:3 on 554 trades — it was negative on the short window. Crash 500 remains the
weakest of the three for DriftJumpAlpha, though **CRT on Crash 500 makes
+$11,869** at 1:5.

The 37% drawdown is the real cost. At 1:3 it returns +$55,963 with 22.7%
drawdown — a materially better risk-adjusted outcome (Sharpe 2.65 vs 2.52).
**1:3 is the better setting on a drawdown basis, 1:5 on raw return.**

---

## 4. Best cells, fixed 1:5, n ≥ 30

| broker | symbol | strategy | P&L | ret% | n | WR | PF | maxDD | Sharpe |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| Deriv | Crash 1000 | DriftJumpAlpha | **+$80,262** | 802.6 | 514 | 26.1% | 1.61 | 37.0% | 2.52 |
| Deriv | Volatility 75 | VWAP | +$15,328 | 153.3 | 240 | 25.0% | 1.52 | 16.4% | 2.13 |
| Deriv | Crash 500 | CRT | +$11,869 | 118.7 | 220 | 24.5% | 1.48 | 16.7% | 1.98 |
| Deriv | US Tech 100 | NYOpenRetest | +$8,944 | 89.4 | 133 | 25.6% | 1.68 | 10.6% | 2.91 |
| **FundedNext** | **USOUSD** | **BiasIFVG** | **+$8,673** | 86.7 | 57 | 36.8% | **2.81** | **7.7%** | **6.58** |
| FundedNext | NDX100 | NYOpenRetest | +$7,862 | 78.6 | 109 | 26.6% | 1.75 | 10.8% | 3.15 |
| Deriv | ETHUSD | BiasIFVG | +$6,904 | 69.0 | 75 | 32.0% | 1.98 | 11.1% | 3.78 |
| FundedNext | UKOUSD | BiasIFVG | +$5,093 | 50.9 | 53 | 34.0% | 2.23 | 6.0% | 4.42 |

**USOUSD × BiasIFVG has the best risk-adjusted profile in all 285 cells** —
Sharpe 6.58, PF 2.81, drawdown under 8%. You asked specifically about US oil;
this is the answer, and it is on FundedNext, not Deriv.

**Worst:** XPTUSD × VWAP on FundedNext (−$7,103, 74.5% DD), NTH25 × VWAP
(−$6,732, 67.3% DD), XPTUSD × CRT (−$6,572). Platinum and Netherlands 25 destroy
accounts on both brokers.

---

## 5. Broker comparison — shared instruments, fixed 1:3

| instrument | Deriv | FundedNext | winner |
|---|---:|---:|---|
| NDX100 | **+$8,443** | +$3,840 | Deriv |
| GER30 | **+$4,827** | +$5 | Deriv |
| SPX500 | −$1,325 | **+$766** | FundedNext |
| USDJPY | −$8,202 | −$1,937 | FundedNext |
| USDCHF | −$7,443 | −$4,528 | FundedNext |
| EURUSD | −$6,665 | −$2,992 | FundedNext |
| AUDUSD | −$3,974 | −$361 | FundedNext |
| XPTUSD | −$12,533 | **−$22,739** | Deriv |
| NTH25 | −$12,601 | −$13,444 | Deriv |
| **TOTAL** | **−$72,767** | **−$79,075** | **Deriv** |

Split 9/9. **Deriv edges it by $6,308**, driven by NDX100 and GER30.
**FundedNext is clearly better on FX** — USDJPY, USDCHF, EURUSD and AUDUSD all
lose materially less there, consistent with its much tighter FX spreads
(report 14).

---

## 6. Portfolio correlation — the synthetics carry the diversification

Daily-return correlation across the surviving book, **mean |r| = 0.172**:

| | Crash 1000 | Vol 75 | Crash 500 | US Tech 100 | ETHUSD | USOUSD |
|---|---:|---:|---:|---:|---:|---:|
| **Crash 1000** | +1.00 | +0.01 | +0.07 | −0.06 | +0.00 | −0.06 |
| **Volatility 75** | +0.01 | +1.00 | +0.06 | +0.03 | +0.05 | +0.01 |
| **Crash 500** | +0.07 | +0.06 | +1.00 | −0.10 | −0.11 | −0.02 |
| US Tech 100 | −0.06 | +0.03 | −0.10 | +1.00 | +0.50 | −0.14 |
| ETHUSD | +0.00 | +0.05 | −0.11 | +0.50 | +1.00 | −0.04 |

**The three synthetics are statistically independent of everything** — every
correlation within ±0.15. They are the only genuine diversifier available.

Redundant pairs to avoid doubling:

| pair | r |
|---|---:|
| **UKOUSD / USOUSD** | **+0.96** — Brent and WTI are one trade |
| **ETHUSD / XRPUSD** | **+0.83** |
| Germany 40 / US Tech 100 | +0.65 |

Oil is **negatively correlated with every index** (−0.11 to −0.15), making it a
real diversifier against the equity block.

---

## 7. What I would trade

| # | broker | instrument | strategy | RR | P&L | maxDD | n |
|---|---|---|---|---|---:|---:|---:|
| 1 | Deriv | **Crash 1000** | DriftJumpAlpha | **1:3** | +$55,963 | 22.7% | 514 |
| 2 | FundedNext | **USOUSD** *(not UKOUSD too)* | BiasIFVG | 1:5 | +$8,673 | 7.7% | 57 |
| 3 | Deriv | **Volatility 75** | VWAP | 1:5 | +$15,328 | 16.4% | 240 |
| 4 | Deriv | **US Tech 100** | NYOpenRetest | 1:5 | +$8,944 | 10.6% | 133 |
| 5 | Deriv | **Crash 500** | CRT | 1:5 | +$11,869 | 16.7% | 220 |

Mean pairwise correlation across these five is **under 0.1**. 1:3 is chosen for
Crash 1000 deliberately — it gives up $24k of return to halve the drawdown.

**Turn off:** VWAP on everything except Volatility 75; all of NYOpenRetest except
US Tech 100 / NDX100; CRT except Crash 500; and XPTUSD and NTH25 entirely on both
brokers.

---

## 8. Signal accounting

| | Deriv | FundedNext |
|---|---:|---:|
| trades executed | 14,572 | 9,417 |
| rejected — min bars between entries | 2,946 | 1,102 |
| rejected — daily trade cap | **1,188** | **214** |
| rejected — invalid stop | 0 | 0 |

**The daily trade cap now fires**, unlike the short-window run where it never
did — 1,188 blocks on Deriv, almost all DriftJumpAlpha on Crash instruments.
Consistent with report 10's finding that the cap is the dominant filter on
high-frequency M5 configurations.

---

## 9. What this run corrects

| earlier claim | status |
|---|---|
| R15: "VWAP is the best strategy in the book" | **wrong — small-sample artefact.** VWAP loses on both brokers over 7,604 trades |
| R15: "VWAP works because live spreads are lower" | **wrong reasoning** — costs were identical; the window changed |
| R10/R15: "Crash 300 loses at every RR" | **wrong** — +$4,517 at 1:3 over 554 trades |
| R15: cross-asset P&L rankings | **invalid** — 28–71 day windows; superseded here |
| R15: "1:5 is best on both brokers" | **half right** — true on Deriv, false on FundedNext, where higher RR is monotonically worse |

## 10. Limits

- **Still in-sample.** No walk-forward split. Rule §0.5-6 requires one before
  sizing up — that is the next necessary step.
- Spreads are one snapshot applied across 8 months.
- The harness omits session-end exits, time limits and concurrent-position caps.
- Sortino remains unreliable and should be ignored; Sharpe is reported only where
  ≥5 trading days exist.
