# Fibonacci retracement strategy (Fib_Strategy.zip): codified and backtested, 2026-10-25

Source: the 20 TradingView screenshots in Fib_Strategy.zip, taken on 22 Oct. The charts are Step Index 200 H4, Nov 2025 to Apr 2026, done by hand on Replay.
Code: `research/fib/fib_backtest.py` (rules plus 18 variants), `fib_report.py` (per-market tables), `pull_h4.py` (data from the Deriv MT5 demo).
Raw results: `research/fib/results/fib_results.json`.

## 1. The method as the screenshots show it
- **Fib:** 0, 0.382, 0.5, 0.618, 0.786 and 1, with no extension. It is drawn from the start to the end of a clear swing leg.
- **"Fib zone":** the 0.5–0.618 band. The entry is a limit order at about 0.5.
- **Confluence:**
  - a CHoCH line, where the leg broke the previous swing low;
  - an order block, the last opposite candle at the leg's start, sitting at 0.618–0.786;
  - a zone tested more than once.
- **Stop:** "above the last fib zone", so just beyond 0.786 (Stop 10,870 against a 0.786 of 10,853).
- **Target:** the end of the Fib (0). Realised reward-to-risk is 1.5–2.2.
- **Missed entry:** the price reached the zone before the leg was finished, so there is no trade.

## 2. Hindsight in the manual trades (checked against the MT5 bars)
| Trade | Fib point used | When that point printed | Entry |
|---|---|---|---|
| 1 (short) | 0 = 10,440 low | Wed 10 Dec 16:00 | 0.5 traded on **6 Dec**, 4 days earlier |
| 6, test 1 (long) | 0 = 10,523 high | Mon 16 Feb 20:00 | **13 Feb** |
| 6, test 2 (long) | 0 = 10,523 high | Mon 16 Feb 20:00 | **1 Feb** |

In all three, the Fib was anchored on a swing that had not happened yet at entry time. A replay that lets you draw the Fib after the fact overstates the method. The backtest only draws a Fib once its swing is **confirmed**: K bars must close without exceeding it, with K=3 (12 hours) by default.

## 3. Rules tested
- **Base:**
  - legs are confirmed fractal swings at least 3 ATR(14) long;
  - limit order at 0.5, stop 0.786, target 0;
  - the order lives 30 bars and is cancelled if the price goes beyond 0 first;
  - missed means the price reached 0.5 before the swing was confirmed;
  - one position per market.
- **Costs and fills:**
  - one bar spread per trade;
  - stops fill at the stop;
  - when the stop and target both fall inside one H4 bar, M15 decides which came first, otherwise the stop is assumed;
  - on the fill bar, a target only counts when M15 shows it came after the fill.
- **Window:** 2024-04-04 to 2026-10-24 for every market (when Step 200/500 history starts), plus full history where it exists.
- **Markets (20):**
  - Step Index, 200, 300, 400, 500;
  - Multi Step 2;
  - Volatility 10, 25, 50, 75, 100, 75 (1s);
  - XAUUSD, XAGUSD;
  - US Tech 100, US SP 500, Wall Street 30;
  - GBPJPY, EURUSD, BTCUSD.

## 4. Variants, all markets (2024-04 to 2026-10)
| Variant | Trades | Win % | Exp R | PF | t |
|---|---|---|---|---|---|
| base: 0.5 / 0.786 / 0 | 1685 | 35.7 | −0.024 | 0.96 | −0.75 |
| structure: break of prior swing | 1483 | 36.1 | −0.014 | 0.98 | −0.41 |
| structure: BOS | 855 | 35.3 | −0.034 | 0.95 | −0.76 |
| structure: CHoCH | 676 | 37.3 | +0.013 | 1.02 | +0.25 |
| order block | 1165 | 36.6 | −0.001 | 1.00 | −0.02 |
| zone tested ≥1× | 1326 | 35.6 | −0.027 | 0.96 | −0.73 |
| zone tested ≥2× | 767 | 36.8 | +0.003 | 1.00 | +0.05 |
| entry 0.618 | 2070 | 22.2 | +0.022 | 1.03 | +0.51 |
| confirmation-candle entry | 1215 | 49.1 | +0.003 | 1.01 | +0.10 |
| stop 0.705 | 1761 | 28.4 | −0.033 | 0.95 | −0.88 |
| stop 1.0 | 1503 | 48.2 | −0.037 | 0.93 | −1.44 |
| target −0.272 | 1561 | 27.4 | −0.009 | 0.99 | −0.22 |
| target 0.236 | 1763 | 51.5 | −0.012 | 0.97 | −0.53 |
| swings K=2 | 2001 | 36.4 | −0.007 | 0.99 | −0.23 |
| swings K=5 | 1153 | 38.1 | +0.036 | 1.06 | +0.91 |
| leg ≥ 5 ATR | 689 | 37.0 | +0.007 | 1.01 | +0.14 |
| **as shown: CHoCH + OB + 0.5/0.786/0** | 434 | 36.9 | +0.002 | 1.00 | +0.03 |
| CHoCH + zone ≥1 + confirmation | 429 | 49.0 | +0.009 | 1.02 | +0.18 |

**How to read this:**
- Each variant's win rate equals the break-even rate its own geometry implies on a driftless market, P(win) = 1/(1+RR).
  - The base needs 36.4% and gets 35.7%.
  - Entry at 0.618 needs 21.4% and gets 22.2%.
  - The pooled z of wins against break-even is −0.73.
- Of 360 market × variant cells, 4 reach t > +2 and 12 reach t < −2. Chance alone predicts about 8 on each side.
- **Missed entries:** about 33% of setups are missed and 30% run away before filling. Only around a third of the Fibs you would draw become trades.
- **Frequency:** about 2–3 trades per market per month for the base, and 0.6–0.9 for the "as shown" version.
- **Hold time:** the median trade lasts 6 H4 bars (one day).

## 5. Per market, base rules (2024-04 to 2026-10, 1% risk, compounded, no pyramiding)
| Market | Trades | Win % | Exp R | PF | Max DD | 1st half | 2nd half | Jan–Oct 2026 |
|---|---|---|---|---|---|---|---|---|
| Step Index 200 | 84 | 39.3 | +0.062 | 1.10 | 13.9% | +0.233 | −0.092 | 29 trades, −0.284R, −8.1% |
| Step Index 500 | 93 | 36.6 | +0.001 | 1.00 | 12.7% | −0.068 | +0.071 | 24, +0.141R, +3.2% |
| XAUUSD | 67 | 35.8 | −0.022 | 0.97 | 13.0% | +0.001 | −0.059 | 18, −0.097R, −1.9% |
| US Tech 100 | 74 | 31.1 | −0.137 | 0.80 | 17.4% | −0.164 | −0.112 | 22, +0.037R, +0.6% |
| US SP 500 | 65 | 32.3 | −0.127 | 0.82 | 16.1% | +0.154 | −0.315 | 28, −0.336R, −9.2% |
| Wall Street 30 | 87 | 41.4 | +0.134 | 1.23 | 8.7% | −0.039 | +0.262 | 35, +0.253R, +8.9% |
| Volatility 75 | 98 | 37.8 | +0.029 | 1.05 | 13.9% | +0.057 | +0.000 | 36, +0.212R, +7.6% |
| Volatility 75 (1s) | 103 | 29.1 | −0.199 | 0.72 | 21.2% | −0.207 | −0.191 | 30, −0.343R, −10.0% |
| BTCUSD | 113 | 30.1 | −0.189 | 0.73 | 21.1% | −0.048 | −0.297 | 38, −0.465R, −16.4% |

The remaining markets are in the JSON and in `fib_report.py` output. Ten of 20 are positive, and only 3 are positive in both halves.

## 6. Verdict
**No edge** on any of the 20 markets, in any of the 18 variants, including the exact CHoCH + OB setup from the screenshots.

- Step Index 200/500 and the Volatility indices are driftless random walks by construction. The chart header in the screenshots says it: "Equal probability of up/down with fixed step size of 0.2". No entry rule can have an edge there, and the results land exactly on that.
- A market's sign flips from variant to variant. In 2026, for example, Wall Street 30 is +0.25R on the base and −0.50R on "as shown". That is what noise looks like.
- **Watch list only:** Wall Street 30 with large legs (leg ≥ 5 ATR: 31 trades, t +3.21). It did not carry over to US SP 500 or US Tech 100, it comes from one of 360 cells, and it has only 2.5 years of history.
