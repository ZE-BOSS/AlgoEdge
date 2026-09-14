# Strategy confluence study — 2026-09-14

SpikeFade, RangeRevert, RangeBreakout, TrendDrift, HTF FVG Flip and Bias IFVG, with every confluence taken apart, on 14 synthetic indices and 11 real markets.

## Start here

**How it was tested.**
1. Each strategy × market × confluence setting was chosen on **2023–24 only**.
2. It had to stay profitable in **2025**, and was then checked on **unseen 2026** (1 Jan → 12 Sep).
3. Every setting that held up was re-run through the **app backtester**, the same code as the Backtester page, for 2026 at $10,000 and 1% risk.

**Result:** 53 of 61 held-up settings were profitable in the app in 2026.

### Strongest options, app-verified (2026)

Profitable in all three periods, with at least 60 trades in 2026 and most calendar quarters profitable:

| Market | Strategy | 2026 app net | Trades | Win rate | PF | Max DD | Quarters profitable |
|---|---|---|---|---|---|---|---|
| Volatility 25 Index | TrendDrift | **+$6,319** | 234 | 22% | 1.44 | 12.3% | 10/15 |
| Crash 300 Index | RangeBreakout | **+$5,119** | 302 | 23% | 1.26 | 15.6% | **14/15** |
| BTCUSD | RangeRevert | **+$3,914** | 69 | 23% | 2.09 | 5.3% | 11/15 |
| US Tech 100 | TrendDrift | **+$3,328** | 178 | 22% | 1.33 | 14.1% | 9/11 |
| AUDUSD | RangeBreakout | **+$2,881** | 310 | 46% | 1.25 | 7.8% | 12/15 |
| Jump 25 Index | RangeRevert | **+$2,764** | 150 | 23% | 1.30 | 11.2% | 12/15 |
| US Tech 100 | RangeBreakout | **+$2,675** | 176 | 34% | 1.36 | 9.7% | 9/11 |
| ETHUSD | TrendDrift | **+$2,497** | 228 | 33% | 1.23 | 15.6% | 11/15 |
| US SP 500 | TrendDrift | **+$2,326** | 167 | 35% | 1.28 | 8.0% | 10/11 |
| GBPJPY | RangeRevert | **+$2,206** | 136 | 21% | 1.28 | 15.2% | 12/15 |
| Crash 500 Index | RangeRevert | **+$2,123** | 164 | 27% | 1.15 | 20.3% | 12/15 |
| GBPUSD | TrendDrift | **+$2,008** | 376 | 48% | 1.15 | 7.9% | 12/15 |
| US Tech 100 | RangeRevert | **+$1,734** | 65 | 20% | 1.46 | 7.8% | 9/11 |
| XAUUSD | SpikeFade | **+$1,705** | 61 | 28% | 1.49 | 12.0% | 12/15 |
| Range Break 200 Index | SpikeFade | **+$1,628** | 265 | 34% | 1.08 | 20.0% | 11/15 |
| XAGUSD | RangeBreakout | **+$1,460** | 98 | **65%** | 1.76 | **1.9%** | 11/15 |
| GBPUSD | RangeRevert | **+$1,329** | 140 | 21% | 1.17 | 13.2% | 12/15 |
| Crash 1000 Index | HTF FVG Flip | **+$1,266** | 66 | 20% | 1.38 | 9.6% | 12/15 |
| Step Index | Bias IFVG | **+$1,065** | 189 | 24% | 1.10 | 16.0% | 11/15 |
| Crash 300 Index | HTF FVG Flip | **+$1,058** | 181 | 20% | 1.10 | 17.8% | 12/15 |

Exact settings and parameters for every row are in the tables further down.

**One setting that works on most real markets:** TrendDrift, long only, min ADX 0, 5 ATR stop, 1:8 target, ADX ranging + volatility high.
- **Profitable in the app on 8 of 11 markets, +$6,535 in total:** USDJPY, BTCUSD, GBPJPY, US SP 500, US Tech 100, ETHUSD, XAGUSD, EURUSD.
- **Losing:** AUDUSD, XAUUSD, GBPUSD.

### SpikeFade

**Profitable in the app in 2026:**
- **Step Index:** +$2,037 (44 trades, PF 1.90)
- **Range Break 100:** +$2,163, but only 19 trades
- **XAUUSD:** +$1,705 (61 trades)
- **Range Break 200:** +$1,628 (265 trades)
- **Volatility 100:** +$1,333 (26 trades)
- **Crash 500:** +$448 (83 trades)
- **AUDUSD:** +$379
- **Crash 300:** +$342 (29 trades)
- **Volatility 75:** +$342 (35 trades)
- **BTCUSD:** +$300
- **USDJPY:** +$153
- **Boom 500:** +$62

**Not profitable:**
- **Crash 1000:** the best 2023–24 setting lost −0.13R per trade over 949 trades in 2026.
- **Boom 1000:** −0.56R per trade.
- **Also failed:** Boom 300, Jump 25, Jump 100, Volatility 25, XAGUSD, ETHUSD, and US Tech 100 (−$52 in the app).

Why SpikeFade couldn't be reproduced before:
- Spike-side stops on Crash/Boom were filled at the stop price. Real ticks fill them well past it, and that alone made random entries look like +0.46R.
- Research closed trades after one day; the app didn't.

Both are fixed now. No single confluence improves SpikeFade across the synthetic indices, so each index needs its own setting.

### Win rate

Most profitable settings win **15–45%** of trades and make their money on 1:5–1:8 targets. In the 55–80% range you asked about:
- **XAGUSD RangeBreakout** short with a 1:1 target: 65% win rate, PF 1.76, 1.9% max drawdown.
- **GBPUSD HTF FVG Flip:** 54%, but only 39 trades.
- **GBPUSD TrendDrift** at 1:1.5 comes close at 48%.

### What changed in the app this session so your Backtester matches these numbers

1. **`max_hold_bars` (new):** closes a trade N M5 bars after entry. Every result here uses **288** (one day). Without it the app ran trades to stop or target, and EURUSD TrendDrift went from 175 trades to 64.
2. **Swap fix:** Volatility 75 overnight financing was charged 100× too much. The same 35 trades went from −$1,431 to +$342.
3. **Spike-stop fills** (Crash, Boom, Jump, Range Break) now use a rule measured from real MT5 ticks.
4. **Budget sync:** a signal counts against the daily limit when it fires, as in live trading.

### Limits to keep in mind

- **Minimum stop:** the app refuses stops closer than 2× the spread. Settings with a 1 ATR stop lose trades to this, so AUDUSD RangeRevert and TrendDrift and BTCUSD RangeBreakout are not tradable as set.
- **Short test period:** 2026 is 8½ months. Rows with fewer than 40 trades are provisional: Range Break 100 SpikeFade, Volatility 100 SpikeFade, Boom 300 HTF FVG, Crash 300 SpikeFade, and the one-setting HTF FVG run.
- **Searching many settings:** thousands of combinations were tried per market, so no pick is proven by 2023–24 alone. The evidence is that it kept working in 2025 and in unseen 2026, across most quarters.
- **Drawdown:** two profitable settings are high risk and left out of the table above:
  - **Boom 300 Bias IFVG:** +$2,466, but a 30.5% max drawdown and only +0.02R per trade in research 2026.
  - **XAUUSD RangeBreakout:** +$1,629, but a 27.9% max drawdown and +0.07R per trade.
- **Account size:** numbers assume $10,000 at 1% risk. On a $700 account the minimum lot refuses more trades. XAGUSD already refused 82 signals at $10,000.

## Profitable in the app backtester, 2026 (research held in all three periods)

| Market | Strategy | Setting | App trades | Win rate | PF | Net (2026, $10k @1%) | Max DD | Research 2023–24 / 2025 / 2026 | Quarters + |
|---|---|---|---|---|---|---|---|---|---|
| Volatility 25 Index | TrendDrift | long, min ADX 25, 2.5 ATR stop, 1:8, strong close, London | 234 | 22% | 1.44 | **+$6,319** | 12.3% | +0.20R (717) / +0.04R (341) / +0.36R (235) | 10/15 |
| Crash 300 Index | RangeBreakout | both, 20-bar channel, 2.5 ATR stop, 1:5, ADX ranging, New York | 302 | 23% | 1.26 | **+$5,119** | 15.6% | +0.21R (961) / +0.11R (460) / +0.27R (304) | 14/15 |
| BTCUSD | RangeRevert | both, stretch 3 ATR, 1 ATR stop, 1:8, EMA regime with | 69 | 23% | 2.09 | **+$3,914** | 5.3% | +0.67R (194) / +0.37R (137) / +0.74R (87) | 11/15 |
| US Tech 100 | TrendDrift | both, min ADX 0, 2.5 ATR stop, 1:8, H1 trend, New York | 178 | 22% | 1.33 | **+$3,328** | 14.1% | +0.42R (252) / +0.04R (272) / +0.25R (187) | 9/11 |
| AUDUSD | RangeBreakout | short, 10-bar channel, 2.5 ATR stop, 1:1.5, strong close, New York | 310 | 46% | 1.25 | **+$2,881** | 7.8% | +0.09R (920) / +0.07R (468) / +0.15R (316) | 12/15 |
| Jump 25 Index | RangeRevert | long, stretch 1.5 ATR, 2.5 ATR stop, 1:8, EMA regime with, New York | 150 | 23% | 1.30 | **+$2,764** | 11.2% | +0.44R (418) / +0.04R (213) / +0.23R (151) | 12/15 |
| US Tech 100 | RangeBreakout | both, 10-bar channel, 5 ATR stop, 1:8, strong close, New York | 176 | 34% | 1.36 | **+$2,675** | 9.7% | +0.51R (227) / +0.05R (257) / +0.22R (178) | 9/11 |
| ETHUSD | TrendDrift | both, min ADX 0, 5 ATR stop, 1:8, vol high, London | 228 | 33% | 1.23 | **+$2,497** | 15.6% | +0.15R (645) / +0.12R (330) / +0.17R (229) | 11/15 |
| Boom 300 Index | Bias IFVG | both, bias OFF, levels ALL, leg APPROACH, no session, swing stop, 1:5, ADX trending | 358 | 23% | 1.12 | **+$2,466** | 30.5% | +0.20R (972) / +0.12R (502) / +0.02R (361) | 13/15 |
| US SP 500 | TrendDrift | long, min ADX 0, 5 ATR stop, 1:8, candle confirms, strong close | 167 | 35% | 1.28 | **+$2,326** | 8.0% | +0.20R (246) / +0.31R (237) / +0.23R (171) | 10/11 |
| GBPJPY | RangeRevert | short, stretch 3 ATR, 1 ATR stop, 1:5, strong close, New York | 136 | 21% | 1.28 | **+$2,206** | 15.2% | +0.29R (538) / +0.09R (223) / +0.38R (149) | 12/15 |
| Range Break 100 Index | SpikeFade | long, spike 5 ATR, 1 ATR stop, 1:8, ADX ranging, vol high | 19 | 42% | 1.89 | **+$2,163** | 8.5% | +0.84R (69) / +0.12R (38) / +1.60R (19) | 10/15 |
| Crash 500 Index | RangeRevert | long, stretch 3 ATR, 1 ATR stop, 1:5, ADX ranging, strong close | 164 | 27% | 1.15 | **+$2,123** | 20.3% | +0.44R (453) / +0.39R (228) / +0.23R (163) | 12/15 |
| Step Index | SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:5, ADX trending, London | 44 | 32% | 1.90 | **+$2,037** | 8.2% | +0.35R (142) / +0.12R (65) / +0.61R (42) | 10/15 |
| GBPUSD | TrendDrift | short, min ADX 20, 1 ATR stop, 1:1.5, candle confirms, London | 376 | 48% | 1.15 | **+$2,008** | 7.9% | +0.07R (1132) / +0.08R (543) / +0.11R (377) | 12/15 |
| US Tech 100 | RangeRevert | short, stretch 3 ATR, 2.5 ATR stop, 1:8, H1 trend, candle confirms | 65 | 20% | 1.46 | **+$1,734** | 7.8% | +1.03R (86) / +0.22R (79) / +0.37R (65) | 9/11 |
| XAUUSD | SpikeFade | long, spike 2 ATR, 2.5 ATR stop, 1:8, H1 trend, New York | 61 | 28% | 1.49 | **+$1,705** | 12.0% | +0.75R (170) / +0.42R (105) / +0.55R (61) | 12/15 |
| Boom 300 Index | RangeRevert | short, stretch 3 ATR, 2.5 ATR stop, 1:8, EMA regime with, London | 36 | 25% | 1.71 | **+$1,630** | 5.0% | +0.99R (85) / +0.38R (38) / +0.65R (37) | 12/15 |
| XAUUSD | RangeBreakout | long, 10-bar channel, 2.5 ATR stop, 1:8, ADX trending | 276 | 20% | 1.11 | **+$1,629** | 27.9% | +0.30R (823) / +0.26R (403) / +0.07R (280) | 12/15 |
| Range Break 200 Index | SpikeFade | both, spike 2 ATR, 5 ATR stop, 1:8, ADX trending, New York | 265 | 34% | 1.08 | **+$1,628** | 20.0% | +0.13R (718) / +0.09R (386) / +0.08R (266) | 11/15 |
| XAGUSD | RangeBreakout | short, 20-bar channel, 5 ATR stop, 1:1, H1 trend, London | 98 | 65% | 1.76 | **+$1,460** | 1.9% | +0.04R (369) / +0.06R (141) / +0.22R (128) | 11/15 |
| Jump 100 Index | RangeBreakout | short, 10-bar channel, 1 ATR stop, 1:8, ADX ranging, New York | 69 | 16% | 1.29 | **+$1,431** | 17.7% | +0.16R (694) / +0.08R (350) / +0.04R (260) | 9/15 |
| Volatility 100 Index | SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:8, EMA regime with, New York | 26 | 31% | 1.97 | **+$1,333** | 4.1% | +0.40R (96) / +0.16R (39) / +0.72R (26) | 11/15 |
| GBPUSD | RangeRevert | short, stretch 3 ATR, 1 ATR stop, 1:5, candle confirms, New York | 140 | 21% | 1.17 | **+$1,329** | 13.2% | +0.23R (544) / +0.05R (254) / +0.26R (143) | 12/15 |
| Volatility 25 Index | RangeBreakout | long, 20-bar channel, 2.5 ATR stop, 1:8, ADX trending, vol high | 335 | 21% | 1.06 | **+$1,305** | 24.9% | +0.14R (978) / +0.05R (503) / +0.05R (336) | 10/15 |
| Crash 1000 Index | HTF FVG Flip | long, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, London | 66 | 20% | 1.38 | **+$1,266** | 9.6% | +0.43R (242) / +0.01R (147) / +0.36R (66) | 12/15 |
| Step Index | Bias IFVG | short, bias OFF, levels ALL, leg BOTH, no session, swing stop, 1:5, ADX trending, New York | 189 | 24% | 1.10 | **+$1,065** | 16.0% | +0.22R (474) / +0.02R (278) / +0.08R (186) | 11/15 |
| Crash 300 Index | HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, inversion ≥0.25 ATR | 181 | 20% | 1.10 | **+$1,058** | 17.8% | +0.20R (439) / +0.00R (234) / +0.14R (178) | 12/15 |
| Boom 300 Index | HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, inversion ≥0.5 ATR, RTH trigger window (09:30–16:00 ET) | 19 | 26% | 2.42 | **+$1,055** | 5.0% | +0.52R (66) / +0.08R (37) / +0.40R (19) | 10/15 |
| Volatility 25 Index | Bias IFVG | long, bias OFF, levels FVG, leg BOTH, no session, swing stop, 1:4, ≥2 overlapping levels, inversion ≥0.25 ATR | 71 | 27% | 1.23 | **+$1,039** | 7.6% | +0.29R (258) / +0.01R (110) / +0.13R (70) | 10/15 |
| GBPJPY | RangeBreakout | long, 10-bar channel, 2.5 ATR stop, 1:5, vol high, London | 231 | 26% | 1.07 | **+$918** | 17.4% | +0.26R (657) / +0.04R (347) / +0.07R (231) | 13/15 |
| Range Break 200 Index | TrendDrift | short, min ADX 20, 5 ATR stop, 1:8, H1 trend, London | 136 | 30% | 1.08 | **+$899** | 20.9% | +0.12R (390) / +0.13R (219) / +0.10R (136) | 10/15 |
| EURUSD | TrendDrift | both, min ADX 0, 5 ATR stop, 1:8, strong close, London | 175 | 37% | 1.09 | **+$718** | 8.0% | +0.16R (540) / +0.00R (267) / +0.08R (175) | 10/15 |
| BTCUSD | HTF FVG Flip | both, trend OFF, retest on, first tap only, displacement off, session filter on, swing stop, 1:3, H1 trend, vol high | 54 | 28% | 1.28 | **+$684** | 5.8% | +0.63R (97) / +0.00R (80) / +0.09R (54) | 11/15 |
| XAUUSD | RangeRevert | long, stretch 3 ATR, 1 ATR stop, 1:8, EMA regime with, ADX trending | 16 | 19% | 1.70 | **+$668** | 6.1% | +0.94R (61) / +0.59R (28) / +0.63R (16) | 12/15 |
| XAGUSD | TrendDrift | both, min ADX 20, 5 ATR stop, 1:8, candle confirms, New York | 119 | 31% | 1.15 | **+$644** | 11.8% | +0.08R (425) / +0.06R (207) / +0.10R (152) | 8/15 |
| XAGUSD | Bias IFVG | long, bias OFF, levels FVG, leg BOTH, no session, swing stop, 1:5, vol high, NY open trigger (09:30–11:00 ET) | 27 | 33% | 1.55 | **+$609** | 4.8% | +0.58R (83) / +0.10R (27) / +0.07R (35) | 10/15 |
| Jump 100 Index | Bias IFVG | short, bias OFF, levels FVG, leg BOTH, no session, swing stop, 1:5, ≥1 overlapping level, NY open trigger (09:30–11:00 ET) | 29 | 28% | 1.37 | **+$593** | 4.6% | +0.48R (72) / +0.08R (37) / +0.12R (30) | 10/15 |
| XAGUSD | RangeRevert | both, stretch 3 ATR, 1 ATR stop, 1:8, ADX ranging, London | 89 | 15% | 1.12 | **+$571** | 10.8% | +0.48R (253) / +0.09R (150) / +0.01R (96) | 9/15 |
| GBPUSD | Bias IFVG | both, bias OFF, levels CISD_REJ, leg BOTH, 09:30–11:00 ET, swing stop, 1:3, inversion ≥0.25 ATR, ADX trending | 63 | 43% | 1.15 | **+$484** | 5.9% | +0.39R (143) / +0.01R (81) / +0.04R (63) | 10/15 |
| ETHUSD | HTF FVG Flip | both, trend COUNTER, retest on, first tap only, displacement off, session filter off, swing stop, 1:4, RTH trigger window (09:30–16:00 ET) | 24 | 21% | 1.43 | **+$475** | 3.5% | +0.78R (60) / +0.06R (47) / +0.75R (25) | 10/15 |
| Crash 500 Index | SpikeFade | both, spike 4 ATR, 5 ATR stop, 1:8, EMA regime with, vol high | 83 | 31% | 1.09 | **+$448** | 12.4% | +0.30R (252) / +0.07R (134) / +0.16R (83) | 10/15 |
| XAUUSD | TrendDrift | long, min ADX 25, 5 ATR stop, 1:8, candle confirms | 153 | 30% | 1.06 | **+$396** | 19.5% | +0.25R (483) / +0.27R (240) / +0.06R (159) | 10/15 |
| AUDUSD | SpikeFade | long, spike 2 ATR, 1 ATR stop, 1:3, ADX ranging, vol high | 32 | 34% | 1.29 | **+$379** | 4.5% | +0.46R (102) / +0.26R (61) / +0.28R (38) | 10/15 |
| Crash 300 Index | SpikeFade | both, spike 3 ATR, 1 ATR stop, 1:3, ADX ranging, New York | 29 | 34% | 1.18 | **+$342** | 9.0% | +0.69R (76) / +0.25R (35) / +0.16R (29) | 11/15 |
| Volatility 75 Index | SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:8, vol high, London | 35 | 23% | 1.23 | **+$342** | 6.7% | +0.51R (100) / +0.09R (57) / +0.04R (35) | 8/15 |
| BTCUSD | SpikeFade | both, spike 4 ATR, 1 ATR stop, 1:8, EMA regime with, ADX trending | 30 | 13% | 1.17 | **+$300** | 7.0% | +0.86R (79) / +0.13R (23) / +0.27R (40) | 11/15 |
| Volatility 25 Index | RangeRevert | long, stretch 1.5 ATR, 2.5 ATR stop, 1:8, H1 trend, London | 115 | 17% | 1.03 | **+$236** | 11.0% | +0.33R (328) / +0.10R (167) / +0.03R (115) | 11/15 |
| Crash 1000 Index | RangeBreakout | long, 55-bar channel, 5 ATR stop, 1:8, vol high, London | 35 | 23% | 1.08 | **+$223** | 7.0% | +0.52R (110) / +0.00R (51) / +0.10R (35) | 11/15 |
| GBPUSD | HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, vol high, London | 39 | 54% | 1.16 | **+$222** | 6.2% | +0.32R (168) / +0.16R (79) / +0.23R (40) | 12/15 |
| USDJPY | SpikeFade | long, spike 3 ATR, 1 ATR stop, 1:8, EMA regime with, London | 47 | 13% | 1.06 | **+$153** | 10.8% | +0.61R (84) / +0.04R (41) / +0.21R (54) | 10/15 |
| Boom 500 Index | SpikeFade | both, spike 4 ATR, 1 ATR stop, 1:8, ADX ranging, vol high | 37 | 16% | 1.02 | **+$62** | 12.9% | +0.61R (86) / +0.45R (38) / +0.03R (37) | 12/15 |
| USDJPY | HTF FVG Flip | both, trend OFF, retest on, first tap only, displacement on, session filter off, swing stop, 1:5, stop not floored, ADX trending | 21 | 48% | 1.05 | **+$53** | 4.9% | +0.42R (94) / +0.05R (53) / +0.10R (22) | 11/15 |

## Held in research but flat or losing in the app

| Market | Strategy | Setting | App trades vs research | PF | Net | Why |
|---|---|---|---|---|---|---|
| AUDUSD | RangeRevert | short, stretch 3 ATR, 1 ATR stop, 1:5, ADX ranging, New York | 29 vs 51 | 0.88 | −$189 | 40 signals refused by the minimum-stop rule |
| AUDUSD | TrendDrift | short, min ADX 0, 1 ATR stop, 1:1.5, strong close, London | 292 vs 381 | 0.93 | −$693 | 127 signals refused by the minimum-stop rule |
| Boom 1000 Index | HTF FVG Flip | long, trend WITH, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, vol high | 80 vs 74 | 0.99 | −$47 | thin research edge absorbed by app slippage and commission |
| Boom 500 Index | TrendDrift | both, min ADX 25, 5 ATR stop, 1:8, vol high, candle confirms | 284 vs 282 | 0.95 | −$896 | thin research edge absorbed by app slippage and commission |
| BTCUSD | RangeBreakout | long, 10-bar channel, 1 ATR stop, 1:8, ADX ranging, New York | 192 vs 213 | 0.92 | −$951 | 36 signals refused by the minimum-stop rule |
| BTCUSD | TrendDrift | long, min ADX 20, 5 ATR stop, 1:8, H1 trend, London | 140 vs 141 | 0.99 | −$51 | thin research edge absorbed by app slippage and commission |
| Crash 300 Index | RangeRevert | both, stretch 3 ATR, 2.5 ATR stop, 1:8, ADX ranging, candle confirms | 251 vs 250 | 0.98 | −$367 | thin research edge absorbed by app slippage and commission |
| Crash 300 Index | Bias IFVG | long, bias OFF, levels ALL, leg APPROACH, no session, swing stop, 1:4, no confluence | 344 vs 325 | 0.96 | −$830 | thin research edge absorbed by app slippage and commission |
| ETHUSD | Bias IFVG | long, bias H4, levels FVG, leg REACTION, 08:00–16:00 ET, swing stop, 1:5, inversion ≥0.5 ATR, New York | 44 vs 43 | 0.68 | −$847 | thin research edge absorbed by app slippage and commission |
| US SP 500 | Bias IFVG | long, bias OFF, levels CISD_REJ, leg BOTH, 08:00–16:00 ET, swing stop, 1:5, no confluence | 114 vs 112 | 1.00 | −$9 | thin research edge absorbed by app slippage and commission |
| US Tech 100 | SpikeFade | long, spike 3 ATR, 2.5 ATR stop, 1:8, vol high | 63 vs 63 | 0.99 | −$52 | thin research edge absorbed by app slippage and commission |

## Synthetic indices: best setting per strategy


### Boom 1000 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 5 ATR, 1 ATR stop, 1:5, H1 trend, London | +0.55R (144) | +0.33R (99) | -0.56R (55) (22%) | 10/15 | no | — |
| RangeRevert | long, stretch 3 ATR, 2.5 ATR stop, 1:8, vol high, London | +0.34R (458) | +0.02R (211) | -0.23R (153) (9%) | 7/15 | no | — |
| RangeBreakout | long, 55-bar channel, 5 ATR stop, 1:8, H1 trend, ADX ranging | +0.36R (261) | +0.00R (110) | -0.03R (83) (20%) | 9/15 | no | — |
| TrendDrift | long, min ADX 0, 2.5 ATR stop, 1:8, ADX ranging, New York | +0.25R (375) | +0.15R (179) | -0.33R (138) (9%) | 8/15 | no | — |
| HTF FVG Flip | long, trend WITH, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, vol high | +0.20R (324) | +0.04R (120) | +0.12R (74) (28%) | 10/15 | **yes** | 80 trades, PF 0.99, −$47 |
| Bias IFVG | both, bias OFF, levels ALL, leg REACTION, no session, swing stop, 1:5, H4 aligned, NY open trigger (09:30–11:00 ET) | +0.44R (247) | +0.01R (124) | -0.03R (99) (26%) | 10/15 | no | — |

### Boom 300 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 3 ATR, 2.5 ATR stop, 1:8, H1 trend, ADX ranging | +0.57R (87) | +0.09R (36) | -0.09R (38) (16%) | 9/15 | no | — |
| RangeRevert | short, stretch 3 ATR, 2.5 ATR stop, 1:8, EMA regime with, London | +0.99R (85) | +0.38R (38) | +0.65R (37) (27%) | 12/15 | **yes** | 36 trades, PF 1.71, +$1,630 |
| RangeBreakout | long, 20-bar channel, 1 ATR stop, 1:8, ADX ranging, vol high | +0.14R (1277) | +0.05R (698) | -0.10R (426) (10%) | 7/15 | no | — |
| TrendDrift | long, min ADX 0, 2.5 ATR stop, 1:8, ADX ranging | +0.21R (702) | +0.00R (364) | -0.20R (251) (10%) | 11/15 | no | — |
| HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, inversion ≥0.5 ATR, RTH trigger window (09:30–16:00 ET) | +0.52R (66) | +0.08R (37) | +0.40R (19) (32%) | 10/15 | **yes** | 19 trades, PF 2.42, +$1,055 |
| Bias IFVG | both, bias OFF, levels ALL, leg APPROACH, no session, swing stop, 1:5, ADX trending | +0.20R (972) | +0.12R (502) | +0.02R (361) (25%) | 13/15 | **yes** | 358 trades, PF 1.12, +$2,466 |

### Boom 500 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 4 ATR, 1 ATR stop, 1:8, ADX ranging, vol high | +0.61R (86) | +0.45R (38) | +0.03R (37) (16%) | 12/15 | **yes** | 37 trades, PF 1.02, +$62 |
| RangeRevert | long, stretch 3 ATR, 2.5 ATR stop, 1:8, ADX trending, vol high | +0.43R (598) | +0.09R (319) | -0.18R (243) (10%) | 11/15 | no | — |
| RangeBreakout | long, 20-bar channel, 2.5 ATR stop, 1:8, EMA regime with, vol high | +0.14R (1078) | +0.01R (550) | -0.02R (366) (14%) | 10/15 | no | — |
| TrendDrift | both, min ADX 25, 5 ATR stop, 1:8, vol high, candle confirms | +0.10R (819) | +0.01R (406) | +0.02R (282) (26%) | 10/15 | **yes** | 284 trades, PF 0.95, −$896 |
| HTF FVG Flip | long, trend OFF, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, inversion ≥0.5 ATR, London | +0.42R (158) | +0.00R (81) | -0.17R (59) (22%) | 11/15 | no | — |
| Bias IFVG | long, bias H4, levels CISD_REJ, leg APPROACH, no session, swing stop, 1:5, inversion ≥0.5 ATR, ADX trending | +0.27R (320) | +0.03R (155) | -0.14R (79) (27%) | 9/15 | no | — |

### Crash 1000 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 3 ATR, 1 ATR stop, 1:5, EMA regime with | +0.11R (2720) | +0.06R (1382) | -0.13R (949) (30%) | 9/15 | no | — |
| RangeRevert | short, stretch 3 ATR, 5 ATR stop, 1:8, H1 trend, ADX ranging | +0.26R (331) | +0.10R (174) | -0.33R (112) (11%) | 8/15 | no | — |
| RangeBreakout | long, 55-bar channel, 5 ATR stop, 1:8, vol high, London | +0.52R (110) | +0.00R (51) | +0.10R (35) (23%) | 11/15 | **yes** | 35 trades, PF 1.08, +$223 |
| TrendDrift | short, min ADX 0, 5 ATR stop, 1:5, ADX ranging, London | +0.31R (332) | +0.01R (169) | -0.23R (115) (16%) | 9/15 | no | — |
| HTF FVG Flip | long, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, London | +0.43R (242) | +0.01R (147) | +0.36R (66) (32%) | 12/15 | **yes** | 66 trades, PF 1.38, +$1,266 |
| Bias IFVG | both, bias H4, levels CISD_REJ, leg REACTION, no session, swing stop, 1:5, inversion ≥0.25 ATR, vol high | +0.24R (624) | +0.19R (318) | -0.11R (218) (25%) | 10/15 | no | — |

### Crash 300 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 3 ATR, 1 ATR stop, 1:3, ADX ranging, New York | +0.69R (76) | +0.25R (35) | +0.16R (29) (34%) | 11/15 | **yes** | 29 trades, PF 1.18, +$342 |
| RangeRevert | both, stretch 3 ATR, 2.5 ATR stop, 1:8, ADX ranging, candle confirms | +0.22R (688) | +0.24R (343) | +0.04R (250) (14%) | 9/15 | **yes** | 251 trades, PF 0.98, −$367 |
| RangeBreakout | both, 20-bar channel, 2.5 ATR stop, 1:5, ADX ranging, New York | +0.21R (961) | +0.11R (460) | +0.27R (304) (23%) | 14/15 | **yes** | 302 trades, PF 1.26, +$5,119 |
| TrendDrift | long, min ADX 0, 2.5 ATR stop, 1:5, ADX ranging, New York | +0.15R (595) | +0.07R (278) | -0.09R (202) (18%) | 9/15 | no | — |
| HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:4, inversion ≥0.25 ATR | +0.20R (439) | +0.00R (234) | +0.14R (178) (28%) | 12/15 | **yes** | 181 trades, PF 1.10, +$1,058 |
| Bias IFVG | long, bias OFF, levels ALL, leg APPROACH, no session, swing stop, 1:4, no confluence | +0.20R (881) | +0.07R (439) | +0.02R (325) (26%) | 11/15 | **yes** | 344 trades, PF 0.96, −$830 |

### Crash 500 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 4 ATR, 5 ATR stop, 1:8, EMA regime with, vol high | +0.30R (252) | +0.07R (134) | +0.16R (83) (33%) | 10/15 | **yes** | 83 trades, PF 1.09, +$448 |
| RangeRevert | long, stretch 3 ATR, 1 ATR stop, 1:5, ADX ranging, strong close | +0.44R (453) | +0.39R (228) | +0.23R (163) (28%) | 12/15 | **yes** | 164 trades, PF 1.15, +$2,123 |
| RangeBreakout | both, 10-bar channel, 2.5 ATR stop, 1:5, London | +0.13R (1094) | +0.12R (578) | -0.11R (385) (17%) | 12/15 | no | — |
| TrendDrift | short, min ADX 0, 1 ATR stop, 1:8, strong close, New York | +0.09R (1237) | +0.00R (637) | -0.14R (451) (10%) | 9/15 | no | — |
| HTF FVG Flip | long, trend COUNTER, retest on, first tap only, displacement off, session filter off, swing stop, 1:5, H1 trend, ADX trending | +0.49R (70) | +0.31R (43) | -0.82R (16) (19%) | 10/15 | no | — |
| Bias IFVG | long, bias OFF, levels ALL, leg REACTION, no session, swing stop, 1:5, inversion ≥0.5 ATR, vol high | +0.48R (162) | +0.00R (91) | -0.12R (62) (27%) | 8/15 | no | — |

### Jump 100 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 1 ATR stop, 1:8, ADX ranging, New York | +0.30R (297) | +0.02R (129) | -0.34R (100) (9%) | 8/15 | no | — |
| RangeRevert | short, stretch 1.5 ATR, 1 ATR stop, 1:5, ADX ranging, candle confirms | +0.14R (981) | +0.03R (529) | -0.10R (333) (18%) | 10/15 | no | — |
| RangeBreakout | short, 10-bar channel, 1 ATR stop, 1:8, ADX ranging, New York | +0.16R (694) | +0.08R (350) | +0.04R (260) (13%) | 9/15 | **yes** | 69 trades, PF 1.29, +$1,431 |
| TrendDrift | short, min ADX 0, 2.5 ATR stop, 1:1, strong close, London | +0.04R (956) | +0.03R (501) | -0.13R (353) (46%) | 7/15 | no | — |
| HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement on, session filter off, swing stop, 1:5, inversion ≥0.25 ATR, vol high | +0.47R (96) | +0.05R (40) | -0.30R (34) (18%) | 9/15 | no | — |
| Bias IFVG | short, bias OFF, levels FVG, leg BOTH, no session, swing stop, 1:5, ≥1 overlapping level, NY open trigger (09:30–11:00 ET) | +0.48R (72) | +0.08R (37) | +0.12R (30) (37%) | 10/15 | **yes** | 29 trades, PF 1.37, +$593 |

### Jump 25 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:8, vol high, New York | +0.22R (840) | +0.11R (418) | -0.00R (311) (18%) | 10/15 | no | — |
| RangeRevert | long, stretch 1.5 ATR, 2.5 ATR stop, 1:8, EMA regime with, New York | +0.44R (418) | +0.04R (213) | +0.23R (151) (22%) | 12/15 | **yes** | 150 trades, PF 1.30, +$2,764 |
| RangeBreakout | long, 55-bar channel, 2.5 ATR stop, 1:8, vol high, New York | +0.28R (544) | +0.04R (270) | -0.27R (197) (14%) | 10/15 | no | — |
| TrendDrift | long, min ADX 25, 5 ATR stop, 1:5, H1 trend, New York | +0.22R (399) | +0.02R (218) | -0.18R (144) (26%) | 8/15 | no | — |
| HTF FVG Flip | both, trend OFF, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, H1 trend, New York | +0.42R (289) | +0.04R (123) | -0.32R (86) (17%) | 10/15 | no | — |
| Bias IFVG | both, bias H4, levels ALL, leg APPROACH, no session, swing stop, 1:5, New York | +0.34R (559) | +0.02R (280) | -0.20R (206) (20%) | 9/15 | no | — |

### Range Break 100 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 5 ATR, 1 ATR stop, 1:8, ADX ranging, vol high | +0.84R (69) | +0.12R (38) | +1.60R (19) (42%) | 10/15 | **yes** | 19 trades, PF 1.89, +$2,163 |
| RangeRevert | long, stretch 1.5 ATR, 5 ATR stop, 1:8, EMA regime with, vol high | +0.15R (604) | +0.07R (303) | -0.08R (212) (28%) | 9/15 | no | — |
| RangeBreakout | both, 55-bar channel, 5 ATR stop, 1:8, EMA regime with, ADX ranging | +0.19R (405) | +0.09R (221) | -0.02R (146) (26%) | 8/15 | no | — |
| TrendDrift | long, min ADX 0, 2.5 ATR stop, 1:5, ADX ranging, vol high | +0.18R (364) | +0.05R (173) | -0.18R (120) (25%) | 10/15 | no | — |
| HTF FVG Flip | both, trend OFF, retest on, re-taps ok, displacement on, session filter off, swing stop, 1:5, first tap, New York | +0.51R (222) | +0.29R (135) | -0.47R (71) (21%) | 10/15 | no | — |
| Bias IFVG | both, bias H4, levels ALL, leg BOTH, no session, swing stop, 1:5, ≥1 overlapping level, NY open trigger (09:30–11:00 ET) | +0.58R (88) | +0.01R (51) | -0.82R (29) (21%) | 9/15 | no | — |

### Range Break 200 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 5 ATR stop, 1:8, ADX trending, New York | +0.13R (718) | +0.09R (386) | +0.08R (266) (34%) | 11/15 | **yes** | 265 trades, PF 1.08, +$1,628 |
| RangeRevert | short, stretch 3 ATR, 2.5 ATR stop, 1:8, EMA regime with, strong close | +0.53R (166) | +0.20R (76) | -0.26R (50) (30%) | 10/15 | no | — |
| RangeBreakout | short, 10-bar channel, 5 ATR stop, 1:8, H1 trend, ADX trending | +0.20R (490) | +0.17R (297) | -0.00R (171) (33%) | 10/15 | no | — |
| TrendDrift | short, min ADX 20, 5 ATR stop, 1:8, H1 trend, London | +0.12R (390) | +0.13R (219) | +0.10R (136) (30%) | 10/15 | **yes** | 136 trades, PF 1.08, +$899 |
| HTF FVG Flip | short, trend COUNTER, retest on, first tap only, displacement off, session filter off, swing stop, 1:5, H1 trend | +0.84R (84) | +0.12R (49) | -0.41R (31) (29%) | 10/15 | no | — |
| Bias IFVG | short, bias OFF, levels FVG, leg APPROACH, no session, swing stop, 1:5, ≥1 overlapping level, H4 aligned | +0.61R (119) | +0.44R (81) | -1.09R (49) (33%) | 10/15 | no | — |

### Step Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:5, ADX trending, London | +0.35R (142) | +0.12R (65) | +0.61R (42) (33%) | 10/15 | **yes** | 44 trades, PF 1.90, +$2,037 |
| RangeRevert | short, stretch 1.5 ATR, 2.5 ATR stop, 1:8, EMA regime with, ADX ranging | +0.31R (187) | +0.22R (93) | -0.62R (73) (11%) | 7/15 | no | — |
| RangeBreakout | long, 55-bar channel, 1 ATR stop, 1:1.5, H1 trend, London | +0.11R (881) | +0.02R (452) | -0.09R (318) (40%) | 11/15 | no | — |
| TrendDrift | short, min ADX 20, 5 ATR stop, 1:1, vol high, candle confirms | +0.08R (823) | +0.03R (395) | -0.13R (292) (45%) | 9/15 | no | — |
| HTF FVG Flip | short, trend OFF, retest on, first tap only, displacement off, session filter off, swing stop, 1:4, ADX trending, RTH trigger window (09:30–16:00 ET) | +0.88R (65) | +0.42R (35) | -0.15R (22) (23%) | 12/15 | no | — |
| Bias IFVG | short, bias OFF, levels ALL, leg BOTH, no session, swing stop, 1:5, ADX trending, New York | +0.22R (474) | +0.02R (278) | +0.08R (186) (27%) | 11/15 | **yes** | 189 trades, PF 1.10, +$1,065 |

### Volatility 100 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:8, EMA regime with, New York | +0.40R (96) | +0.16R (39) | +0.72R (26) (31%) | 11/15 | **yes** | 26 trades, PF 1.97, +$1,333 |
| RangeRevert | both, stretch 2 ATR, 1 ATR stop, 1:8, EMA regime with, strong close | +0.59R (76) | +0.67R (36) | -0.47R (29) (7%) | 9/15 | no | — |
| RangeBreakout | short, 10-bar channel, 1 ATR stop, 1:2, EMA regime with, New York | +0.10R (1378) | +0.02R (659) | -0.09R (470) (33%) | 11/15 | no | — |
| TrendDrift | short, min ADX 20, 1 ATR stop, 1:1.5, candle confirms, New York | +0.07R (1417) | +0.05R (703) | -0.04R (507) (42%) | 10/15 | no | — |
| HTF FVG Flip | both, trend OFF, retest on, re-taps ok, displacement on, session filter off, swing stop, 1:4, first tap, inversion ≥0.25 ATR | +0.65R (81) | +0.20R (67) | -0.75R (35) (9%) | 9/15 | no | — |
| Bias IFVG | both, bias OFF, levels FVG, leg APPROACH, no session, swing stop, 1:5, inversion ≥0.5 ATR, vol high | +0.41R (210) | +0.06R (102) | -0.06R (71) (34%) | 10/15 | no | — |

### Volatility 25 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | short, spike 2 ATR, 1 ATR stop, 1:8, ADX trending | +0.37R (213) | +0.05R (112) | -0.10R (82) (11%) | 8/15 | no | — |
| RangeRevert | long, stretch 1.5 ATR, 2.5 ATR stop, 1:8, H1 trend, London | +0.33R (328) | +0.10R (167) | +0.03R (115) (17%) | 11/15 | **yes** | 115 trades, PF 1.03, +$236 |
| RangeBreakout | long, 20-bar channel, 2.5 ATR stop, 1:8, ADX trending, vol high | +0.14R (978) | +0.05R (503) | +0.05R (336) (21%) | 10/15 | **yes** | 335 trades, PF 1.06, +$1,305 |
| TrendDrift | long, min ADX 25, 2.5 ATR stop, 1:8, strong close, London | +0.20R (717) | +0.04R (341) | +0.36R (235) (22%) | 10/15 | **yes** | 234 trades, PF 1.44, +$6,319 |
| HTF FVG Flip | long, trend OFF, retest on, re-taps ok, displacement on, session filter off, swing stop, 1:5, H1 trend, ADX trending | +0.59R (99) | +0.43R (40) | -0.34R (34) (12%) | 11/15 | no | — |
| Bias IFVG | long, bias OFF, levels FVG, leg BOTH, no session, swing stop, 1:4, ≥2 overlapping levels, inversion ≥0.25 ATR | +0.29R (258) | +0.01R (110) | +0.13R (70) (30%) | 10/15 | **yes** | 71 trades, PF 1.23, +$1,039 |

### Volatility 75 Index

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 2.5 ATR stop, 1:8, vol high, London | +0.51R (100) | +0.09R (57) | +0.04R (35) (23%) | 8/15 | **yes** | 35 trades, PF 1.23, +$342 |
| RangeRevert | short, stretch 2 ATR, 2.5 ATR stop, 1:5, EMA regime with, ADX trending | +0.47R (395) | +0.17R (213) | -0.09R (142) (17%) | 12/15 | no | — |
| RangeBreakout | short, 10-bar channel, 1 ATR stop, 1:8, ADX trending, candle confirms | +0.08R (1513) | +0.03R (751) | -0.13R (507) (11%) | 6/15 | no | — |
| TrendDrift | both, min ADX 25, 2.5 ATR stop, 1:8, vol high, strong close | +0.11R (996) | +0.02R (506) | -0.19R (359) (16%) | 7/15 | no | — |
| HTF FVG Flip | both, trend WITH, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, ADX trending, vol high | +0.34R (282) | +0.04R (166) | -0.11R (103) (20%) | 11/15 | no | — |
| Bias IFVG | long, bias OFF, levels FVG, leg REACTION, no session, swing stop, 1:5, H4 aligned, ADX trending | +0.21R (399) | +0.06R (231) | -0.06R (154) (25%) | 10/15 | no | — |

## Real markets: best setting per strategy


### AUDUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 2 ATR, 1 ATR stop, 1:3, ADX ranging, vol high | +0.46R (102) | +0.26R (61) | +0.28R (38) (37%) | 10/15 | **yes** | 32 trades, PF 1.29, +$379 |
| RangeRevert | short, stretch 3 ATR, 1 ATR stop, 1:5, ADX ranging, New York | +0.64R (164) | +0.59R (98) | +0.95R (51) (29%) | 12/15 | **yes** | 29 trades, PF 0.88, −$189 |
| RangeBreakout | short, 10-bar channel, 2.5 ATR stop, 1:1.5, strong close, New York | +0.09R (920) | +0.07R (468) | +0.15R (316) (46%) | 12/15 | **yes** | 310 trades, PF 1.25, +$2,881 |
| TrendDrift | short, min ADX 0, 1 ATR stop, 1:1.5, strong close, London | +0.05R (1206) | +0.00R (583) | +0.02R (381) (44%) | 10/15 | **yes** | 292 trades, PF 0.93, −$693 |
| HTF FVG Flip | long, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, stop not floored, ADX trending | +0.50R (64) | +0.08R (26) | -0.01R (21) (29%) | 7/15 | no | — |
| Bias IFVG | long, bias OFF, levels ALL, leg REACTION, 08:00–16:00 ET, swing stop, 1:5, ≥2 overlapping levels, inversion ≥0.5 ATR | +0.79R (63) | +0.08R (38) | -0.32R (22) (27%) | 9/15 | no | — |

### BTCUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 4 ATR, 1 ATR stop, 1:8, EMA regime with, ADX trending | +0.86R (79) | +0.13R (23) | +0.27R (40) (15%) | 11/15 | **yes** | 30 trades, PF 1.17, +$300 |
| RangeRevert | both, stretch 3 ATR, 1 ATR stop, 1:8, EMA regime with | +0.67R (194) | +0.37R (137) | +0.74R (87) (21%) | 11/15 | **yes** | 69 trades, PF 2.09, +$3,914 |
| RangeBreakout | long, 10-bar channel, 1 ATR stop, 1:8, ADX ranging, New York | +0.31R (624) | +0.16R (363) | +0.09R (213) (13%) | 13/15 | **yes** | 192 trades, PF 0.92, −$951 |
| TrendDrift | long, min ADX 20, 5 ATR stop, 1:8, H1 trend, London | +0.30R (461) | +0.09R (221) | +0.03R (141) (28%) | 11/15 | **yes** | 140 trades, PF 0.99, −$51 |
| HTF FVG Flip | both, trend OFF, retest on, first tap only, displacement off, session filter on, swing stop, 1:3, H1 trend, vol high | +0.63R (97) | +0.00R (80) | +0.09R (54) (37%) | 11/15 | **yes** | 54 trades, PF 1.28, +$684 |
| Bias IFVG | long, bias H4, levels ALL, leg REACTION, no session, swing stop, 1:5, inversion ≥0.5 ATR, New York | +0.63R (189) | +0.07R (88) | -0.13R (76) (24%) | 8/15 | no | — |

### ETHUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 4 ATR, 1 ATR stop, 1:8, vol high | +0.31R (248) | +0.34R (81) | -0.47R (79) (8%) | 10/15 | no | — |
| RangeRevert | both, stretch 3 ATR, 5 ATR stop, 1:8, H1 trend, ADX ranging | +0.29R (140) | +0.45R (60) | -0.46R (40) (15%) | 9/15 | no | — |
| RangeBreakout | short, 10-bar channel, 2.5 ATR stop, 1:5, ADX trending, vol high | +0.08R (1163) | +0.00R (558) | -0.06R (419) (22%) | 8/15 | no | — |
| TrendDrift | both, min ADX 0, 5 ATR stop, 1:8, vol high, London | +0.15R (645) | +0.12R (330) | +0.17R (229) (33%) | 11/15 | **yes** | 228 trades, PF 1.23, +$2,497 |
| HTF FVG Flip | both, trend COUNTER, retest on, first tap only, displacement off, session filter off, swing stop, 1:4, RTH trigger window (09:30–16:00 ET) | +0.78R (60) | +0.06R (47) | +0.75R (25) (44%) | 10/15 | **yes** | 24 trades, PF 1.43, +$475 |
| Bias IFVG | long, bias H4, levels FVG, leg REACTION, 08:00–16:00 ET, swing stop, 1:5, inversion ≥0.5 ATR, New York | +0.66R (116) | +0.56R (37) | +0.00R (43) (30%) | 13/15 | **yes** | 44 trades, PF 0.68, −$847 |

### EURUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 1 ATR stop, 1:3, strong close, London | +0.35R (70) | +0.03R (26) | -0.85R (17) (6%) | 7/15 | no | — |
| RangeRevert | long, stretch 1.5 ATR, 1 ATR stop, 1:8, EMA regime with, strong close | +0.65R (118) | +0.10R (68) | -0.26R (45) (18%) | 9/15 | no | — |
| RangeBreakout | long, 10-bar channel, 1 ATR stop, 1:3, vol high, New York | +0.13R (1050) | +0.06R (524) | -0.26R (361) (20%) | 11/15 | no | — |
| TrendDrift | both, min ADX 0, 5 ATR stop, 1:8, strong close, London | +0.16R (540) | +0.00R (267) | +0.08R (175) (37%) | 10/15 | **yes** | 175 trades, PF 1.09, +$718 |
| HTF FVG Flip | short, trend OFF, retest on, first tap only, displacement off, session filter off, swing stop, 1:2, vol high, New York | +0.18R (70) | +0.32R (27) | -0.04R (18) (39%) | 10/15 | no | — |
| Bias IFVG | both, bias OFF, levels ALL, leg BOTH, 08:00–16:00 ET, swing stop, 1:5, ≥1 overlapping level, vol high | +0.30R (359) | +0.09R (195) | -0.18R (130) (30%) | 9/15 | no | — |

### GBPJPY

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 4 ATR, 5 ATR stop, 1:8, EMA regime with | +0.54R (77) | +0.32R (28) | -0.75R (28) (21%) | 9/15 | no | — |
| RangeRevert | short, stretch 3 ATR, 1 ATR stop, 1:5, strong close, New York | +0.29R (538) | +0.09R (223) | +0.38R (149) (21%) | 12/15 | **yes** | 136 trades, PF 1.28, +$2,206 |
| RangeBreakout | long, 10-bar channel, 2.5 ATR stop, 1:5, vol high, London | +0.26R (657) | +0.04R (347) | +0.07R (231) (26%) | 13/15 | **yes** | 231 trades, PF 1.07, +$918 |
| TrendDrift | long, min ADX 25, 1 ATR stop, 1:5, strong close, London | +0.19R (840) | +0.03R (418) | -0.13R (284) (17%) | 11/15 | no | — |
| HTF FVG Flip | both, trend COUNTER, retest on, first tap only, displacement off, session filter on, swing stop, 1:3, stop not floored, vol high | +0.37R (64) | +0.05R (28) | -0.92R (14) (7%) | 9/15 | no | — |
| Bias IFVG | long, bias OFF, levels CISD_REJ, leg BOTH, 08:00–16:00 ET, swing stop, 1:5, ≥2 overlapping levels, H4 aligned | +0.53R (98) | +0.11R (45) | -0.50R (26) (23%) | 8/15 | no | — |

### GBPUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | both, spike 2 ATR, 5 ATR stop, 1:8, vol high, strong close | +0.20R (75) | +0.07R (46) | -0.06R (31) (35%) | 10/15 | no | — |
| RangeRevert | short, stretch 3 ATR, 1 ATR stop, 1:5, candle confirms, New York | +0.23R (544) | +0.05R (254) | +0.26R (143) (22%) | 12/15 | **yes** | 140 trades, PF 1.17, +$1,329 |
| RangeBreakout | both, 10-bar channel, 2.5 ATR stop, 1:1.5, vol high, New York | +0.12R (941) | +0.00R (464) | -0.06R (322) (39%) | 11/15 | no | — |
| TrendDrift | short, min ADX 20, 1 ATR stop, 1:1.5, candle confirms, London | +0.07R (1132) | +0.08R (543) | +0.11R (377) (47%) | 12/15 | **yes** | 376 trades, PF 1.15, +$2,008 |
| HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, vol high, London | +0.32R (168) | +0.16R (79) | +0.23R (40) (32%) | 12/15 | **yes** | 39 trades, PF 1.16, +$222 |
| Bias IFVG | both, bias OFF, levels CISD_REJ, leg BOTH, 09:30–11:00 ET, swing stop, 1:3, inversion ≥0.25 ATR, ADX trending | +0.39R (143) | +0.01R (81) | +0.04R (63) (38%) | 10/15 | **yes** | 63 trades, PF 1.15, +$484 |

### US SP 500

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 2 ATR, 2.5 ATR stop, 1:8, ADX trending, London | +0.15R (282) | +0.18R (283) | -0.01R (174) (21%) | 8/11 | no | — |
| RangeRevert | long, stretch 2 ATR, 1 ATR stop, 1:8, strong close, New York | +0.34R (303) | +0.00R (345) | -0.64R (227) (7%) | 6/11 | no | — |
| RangeBreakout | both, 55-bar channel, 5 ATR stop, 1:8, ADX ranging, strong close | +0.38R (199) | +0.13R (208) | -0.18R (158) (24%) | 7/11 | no | — |
| TrendDrift | long, min ADX 0, 5 ATR stop, 1:8, candle confirms, strong close | +0.20R (246) | +0.31R (237) | +0.23R (171) (34%) | 10/11 | **yes** | 167 trades, PF 1.28, +$2,326 |
| HTF FVG Flip | both, trend OFF, retest on, re-taps ok, displacement on, session filter on, swing stop, 1:5, vol high | +0.05R (83) | +0.01R (88) | -0.04R (44) (25%) | 7/11 | no | — |
| Bias IFVG | long, bias OFF, levels CISD_REJ, leg BOTH, 08:00–16:00 ET, swing stop, 1:5, no confluence | +0.38R (173) | +0.00R (173) | +0.03R (112) (29%) | 7/11 | **yes** | 114 trades, PF 1.00, −$9 |

### US Tech 100

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 3 ATR, 2.5 ATR stop, 1:8, vol high | +0.34R (112) | +0.25R (125) | +0.04R (63) (19%) | 9/11 | **yes** | 63 trades, PF 0.99, −$52 |
| RangeRevert | short, stretch 3 ATR, 2.5 ATR stop, 1:8, H1 trend, candle confirms | +1.03R (86) | +0.22R (79) | +0.37R (65) (20%) | 9/11 | **yes** | 65 trades, PF 1.46, +$1,734 |
| RangeBreakout | both, 10-bar channel, 5 ATR stop, 1:8, strong close, New York | +0.51R (227) | +0.05R (257) | +0.22R (178) (34%) | 9/11 | **yes** | 176 trades, PF 1.36, +$2,675 |
| TrendDrift | both, min ADX 0, 2.5 ATR stop, 1:8, H1 trend, New York | +0.42R (252) | +0.04R (272) | +0.25R (187) (21%) | 9/11 | **yes** | 178 trades, PF 1.33, +$3,328 |
| HTF FVG Flip | short, trend OFF, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, ADX trending, London | +0.23R (69) | +0.10R (64) | -0.07R (45) (20%) | 6/11 | no | — |
| Bias IFVG | short, bias H4, levels CISD_REJ, leg APPROACH, no session, swing stop, 1:5, no confluence | +0.74R (63) | +0.14R (75) | -0.37R (65) (18%) | 5/11 | no | — |

### USDJPY

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 3 ATR, 1 ATR stop, 1:8, EMA regime with, London | +0.61R (84) | +0.04R (41) | +0.21R (54) (15%) | 10/15 | **yes** | 47 trades, PF 1.06, +$153 |
| RangeRevert | long, stretch 1.5 ATR, 2.5 ATR stop, 1:8, EMA regime with, H1 trend | +0.33R (384) | +0.46R (144) | -0.59R (160) (12%) | 10/15 | no | — |
| RangeBreakout | long, 20-bar channel, 5 ATR stop, 1:8, EMA regime with, ADX ranging | +0.32R (353) | +0.07R (183) | -0.08R (149) (27%) | 9/15 | no | — |
| TrendDrift | long, min ADX 0, 2.5 ATR stop, 1:8, ADX ranging | +0.21R (527) | +0.05R (259) | -0.01R (195) (17%) | 7/15 | no | — |
| HTF FVG Flip | both, trend OFF, retest on, first tap only, displacement on, session filter off, swing stop, 1:5, stop not floored, ADX trending | +0.42R (94) | +0.05R (53) | +0.10R (22) (27%) | 11/15 | **yes** | 21 trades, PF 1.05, +$53 |
| Bias IFVG | both, bias OFF, levels CISD_REJ, leg REACTION, 08:00–16:00 ET, swing stop, 1:5, stop not floored, London | +0.34R (347) | +0.02R (175) | -0.07R (99) (34%) | 11/15 | no | — |

### XAGUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 4 ATR, 1 ATR stop, 1:8, no confluence | +0.67R (131) | +0.65R (57) | -0.01R (37) (16%) | 9/15 | no | — |
| RangeRevert | both, stretch 3 ATR, 1 ATR stop, 1:8, ADX ranging, London | +0.48R (253) | +0.09R (150) | +0.01R (96) (14%) | 9/15 | **yes** | 89 trades, PF 1.12, +$571 |
| RangeBreakout | short, 20-bar channel, 5 ATR stop, 1:1, H1 trend, London | +0.04R (369) | +0.06R (141) | +0.22R (128) (63%) | 11/15 | **yes** | 98 trades, PF 1.76, +$1,460 |
| TrendDrift | both, min ADX 20, 5 ATR stop, 1:8, candle confirms, New York | +0.08R (425) | +0.06R (207) | +0.10R (152) (35%) | 8/15 | **yes** | 119 trades, PF 1.15, +$644 |
| HTF FVG Flip | both, trend COUNTER, retest on, re-taps ok, displacement off, session filter on, swing stop, 1:5, vol high | +0.41R (121) | +0.34R (38) | -0.36R (29) (17%) | 10/15 | no | — |
| Bias IFVG | long, bias OFF, levels FVG, leg BOTH, no session, swing stop, 1:5, vol high, NY open trigger (09:30–11:00 ET) | +0.58R (83) | +0.10R (27) | +0.07R (35) (29%) | 10/15 | **yes** | 27 trades, PF 1.55, +$609 |

### XAUUSD

| Strategy | Best 2023–24 setting | 2023–24 | 2025 | 2026 (win rate) | Quarters + | Held | App 2026 |
|---|---|---|---|---|---|---|---|
| SpikeFade | long, spike 2 ATR, 2.5 ATR stop, 1:8, H1 trend, New York | +0.75R (170) | +0.42R (105) | +0.55R (61) (28%) | 12/15 | **yes** | 61 trades, PF 1.49, +$1,705 |
| RangeRevert | long, stretch 3 ATR, 1 ATR stop, 1:8, EMA regime with, ADX trending | +0.94R (61) | +0.59R (28) | +0.63R (16) (19%) | 12/15 | **yes** | 16 trades, PF 1.70, +$668 |
| RangeBreakout | long, 10-bar channel, 2.5 ATR stop, 1:8, ADX trending | +0.30R (823) | +0.26R (403) | +0.07R (280) (20%) | 12/15 | **yes** | 276 trades, PF 1.11, +$1,629 |
| TrendDrift | long, min ADX 25, 5 ATR stop, 1:8, candle confirms | +0.25R (483) | +0.27R (240) | +0.06R (159) (30%) | 10/15 | **yes** | 153 trades, PF 1.06, +$396 |
| HTF FVG Flip | long, trend WITH, retest on, re-taps ok, displacement off, session filter off, swing stop, 1:5, no confluence | +0.29R (187) | +0.21R (98) | -0.30R (82) (21%) | 9/15 | no | — |
| Bias IFVG | long, bias H4, levels CISD_REJ, leg REACTION, 08:00–16:00 ET, swing stop, 1:4, inversion ≥0.25 ATR, London | +0.62R (121) | +0.48R (72) | -0.12R (40) (25%) | 13/15 | no | — |

## One setting on every real market, in the app backtester (2026)


**TrendDrift**: long, min ADX 0, 5 ATR stop, 1:8, ADX ranging, vol high. Profitable on **8/11** markets, total **+$6,535**.

| Market | App trades | Win rate | PF | Net | Max DD | Research 2026 |
|---|---|---|---|---|---|---|
| USDJPY | 114 | 44% | 1.42 | +$1,820 | 7.6% | +0.22R (114) |
| BTCUSD | 123 | 38% | 1.29 | +$1,558 | 12.3% | +0.25R (122) |
| GBPJPY | 108 | 46% | 1.31 | +$1,323 | 10.8% | +0.17R (108) |
| US SP 500 | 110 | 35% | 1.19 | +$990 | 9.6% | +0.20R (109) |
| US Tech 100 | 111 | 38% | 1.18 | +$863 | 9.8% | +0.16R (111) |
| ETHUSD | 135 | 30% | 1.11 | +$708 | 11.5% | +0.11R (135) |
| XAGUSD | 73 | 32% | 1.22 | +$626 | 8.9% | +0.15R (94) |
| EURUSD | 108 | 32% | 1.02 | +$86 | 9.4% | +0.05R (108) |
| AUDUSD | 121 | 35% | 0.96 | −$210 | 13.7% | -0.01R (121) |
| XAUUSD | 92 | 33% | 0.85 | −$532 | 11.3% | -0.11R (95) |
| GBPUSD | 113 | 33% | 0.86 | −$698 | 15.7% | -0.08R (113) |

**HTF FVG Flip**: long, trend COUNTER, retest on, re-taps ok, displacement off, session filter on, swing stop, 1:5, New York. Profitable on **7/11** markets, total **+$677**.

| Market | App trades | Win rate | PF | Net | Max DD | Research 2026 |
|---|---|---|---|---|---|---|
| ETHUSD | 24 | 25% | 1.73 | +$702 | 2.9% | +0.93R (24) |
| US Tech 100 | 23 | 30% | 1.54 | +$376 | 4.4% | -0.10R (24) |
| XAGUSD | 21 | 19% | 1.25 | +$201 | 4.4% | +0.14R (22) |
| GBPUSD | 15 | 60% | 1.33 | +$181 | 2.6% | +0.07R (16) |
| XAUUSD | 13 | 23% | 1.22 | +$145 | 3.2% | +0.02R (13) |
| AUDUSD | 16 | 44% | 1.14 | +$107 | 5.6% | +0.10R (16) |
| US SP 500 | 26 | 23% | 1.03 | +$49 | 4.5% | +0.13R (28) |
| EURUSD | 16 | 25% | 0.93 | −$50 | 6.3% | +0.01R (18) |
| USDJPY | 20 | 55% | 0.82 | −$130 | 4.7% | +0.26R (19) |
| GBPJPY | 16 | 50% | 0.82 | −$170 | 5.1% | +0.02R (15) |
| BTCUSD | 26 | 15% | 0.42 | −$734 | 8.5% | +0.03R (27) |

## Exact Backtester parameters for every app-verified row

Account: $10,000, risk 1%, sizing STATIC, fill model CONSERVATIVE, TP count 1, TP1 R:R = `tp1_rr` / `target_rr` below, min R:R ≤ that target, window 2026-01-01 → 2026-09-12. Paste into the strategy's parameter block.

| Market | Strategy | Parameters |
|---|---|---|
| AUDUSD | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 2.0, "stop_atr_multiple": 1.0, "tp1_rr": 3.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| AUDUSD | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "adx_filter": "RANGE", "time_filter": "NEWYORK"}` |
| AUDUSD | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 10, "stop_atr_multiple": 2.5, "tp1_rr": 1.5, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_strong_close": true, "time_filter": "NEWYORK"}` |
| AUDUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 1.0, "tp1_rr": 1.5, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_strong_close": true, "time_filter": "LONDON"}` |
| BTCUSD | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 4.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_trend_with": true, "adx_filter": "TREND"}` |
| BTCUSD | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_trend_with": true}` |
| BTCUSD | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 10, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "time_filter": "NEWYORK"}` |
| BTCUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 20, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_htf_trend": true, "time_filter": "LONDON"}` |
| BTCUSD | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "OFF", "require_retest": true, "require_unfilled_htf_fvg": true, "session_filter_enabled": true, "target_rr": 3.0, "side": "both", "require_htf_trend": true, "require_vol_high": true}` |
| Boom 1000 Index | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "WITH", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": false, "target_rr": 4.0, "side": "long", "require_vol_high": true}` |
| Boom 300 Index | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_trend_with": true, "time_filter": "LONDON"}` |
| Boom 300 Index | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": false, "target_rr": 4.0, "side": "both", "min_inversion_disp_atr": 0.5, "time_filter": "RTH"}` |
| Boom 300 Index | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "APPROACH", "key_levels": "ALL", "session_cutoff": "23:59", "session_filter_enabled": false, "session_start": "00:00", "target_rr": 5.0, "side": "both", "max_trades_per_day": 4, "day_stop_enabled": false, "require_adx_trend": true}` |
| Boom 500 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 4.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "RANGE", "require_vol_high": true}` |
| Boom 500 Index | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 25, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_vol_high": true, "require_candle_confirm": true}` |
| Crash 1000 Index | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 55, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_vol_high": true, "time_filter": "LONDON"}` |
| Crash 1000 Index | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": false, "target_rr": 4.0, "side": "long", "time_filter": "LONDON"}` |
| Crash 300 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 3.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "RANGE", "time_filter": "NEWYORK"}` |
| Crash 300 Index | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "RANGE", "require_candle_confirm": true}` |
| Crash 300 Index | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 20, "stop_atr_multiple": 2.5, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "RANGE", "time_filter": "NEWYORK"}` |
| Crash 300 Index | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": false, "target_rr": 4.0, "side": "both", "min_inversion_disp_atr": 0.25}` |
| Crash 300 Index | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "APPROACH", "key_levels": "ALL", "session_cutoff": "23:59", "session_filter_enabled": false, "session_start": "00:00", "target_rr": 4.0, "side": "long", "max_trades_per_day": 4, "day_stop_enabled": false}` |
| Crash 500 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 4.0, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_trend_with": true, "require_vol_high": true}` |
| Crash 500 Index | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_strong_close": true}` |
| ETHUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_vol_high": true, "time_filter": "LONDON"}` |
| ETHUSD | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": true, "session_filter_enabled": false, "target_rr": 4.0, "side": "both", "time_filter": "RTH"}` |
| ETHUSD | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "H4", "ifvg_leg_mode": "REACTION", "key_levels": "FVG", "session_cutoff": "16:00", "session_filter_enabled": true, "session_start": "08:00", "target_rr": 5.0, "side": "long", "max_trades_per_day": 4, "day_stop_enabled": false, "min_inversion_disp_atr": 0.5, "time_filter": "NEWYORK"}` |
| EURUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_strong_close": true, "time_filter": "LONDON"}` |
| GBPJPY | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_strong_close": true, "time_filter": "NEWYORK"}` |
| GBPJPY | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 10, "stop_atr_multiple": 2.5, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_vol_high": true, "time_filter": "LONDON"}` |
| GBPUSD | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_candle_confirm": true, "time_filter": "NEWYORK"}` |
| GBPUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 20, "stop_atr_multiple": 1.0, "tp1_rr": 1.5, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_candle_confirm": true, "time_filter": "LONDON"}` |
| GBPUSD | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": false, "target_rr": 5.0, "side": "both", "require_vol_high": true, "time_filter": "LONDON"}` |
| GBPUSD | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "BOTH", "key_levels": "CISD_REJ", "session_cutoff": "11:00", "session_filter_enabled": true, "session_start": "09:30", "target_rr": 3.0, "side": "both", "max_trades_per_day": 4, "day_stop_enabled": false, "min_inversion_disp_atr": 0.25, "require_adx_trend": true}` |
| Jump 100 Index | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 10, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "adx_filter": "RANGE", "time_filter": "NEWYORK"}` |
| Jump 100 Index | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "BOTH", "key_levels": "FVG", "session_cutoff": "23:59", "session_filter_enabled": false, "session_start": "00:00", "target_rr": 5.0, "side": "short", "max_trades_per_day": 4, "day_stop_enabled": false, "min_confluent_levels": 1, "time_filter": "NY_OPEN"}` |
| Jump 25 Index | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 1.5, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_trend_with": true, "time_filter": "NEWYORK"}` |
| Range Break 100 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 5.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| Range Break 200 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 2.0, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "TREND", "time_filter": "NEWYORK"}` |
| Range Break 200 Index | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 20, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_htf_trend": true, "time_filter": "LONDON"}` |
| Step Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 2.0, "stop_atr_multiple": 2.5, "tp1_rr": 5.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "TREND", "time_filter": "LONDON"}` |
| Step Index | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "BOTH", "key_levels": "ALL", "session_cutoff": "23:59", "session_filter_enabled": false, "session_start": "00:00", "target_rr": 5.0, "side": "short", "max_trades_per_day": 4, "day_stop_enabled": false, "require_adx_trend": true, "time_filter": "NEWYORK"}` |
| US SP 500 | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_candle_confirm": true, "require_strong_close": true}` |
| US SP 500 | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "BOTH", "key_levels": "CISD_REJ", "session_cutoff": "16:00", "session_filter_enabled": true, "session_start": "08:00", "target_rr": 5.0, "side": "long", "max_trades_per_day": 4, "day_stop_enabled": false}` |
| US Tech 100 | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 3.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_vol_high": true}` |
| US Tech 100 | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_htf_trend": true, "require_candle_confirm": true}` |
| US Tech 100 | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 10, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_strong_close": true, "time_filter": "NEWYORK"}` |
| US Tech 100 | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_htf_trend": true, "time_filter": "NEWYORK"}` |
| USDJPY | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_trend_with": true, "time_filter": "LONDON"}` |
| USDJPY | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 1.5, "fvg_displacement_body_pct": 0.6, "htf_trend_filter": "OFF", "require_retest": true, "require_unfilled_htf_fvg": true, "session_filter_enabled": false, "target_rr": 5.0, "side": "both", "require_stop_ok": true, "require_adx_trend": true}` |
| Volatility 100 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 2.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_trend_with": true, "time_filter": "NEWYORK"}` |
| Volatility 25 Index | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 1.5, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_htf_trend": true, "time_filter": "LONDON"}` |
| Volatility 25 Index | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 20, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "TREND", "require_vol_high": true}` |
| Volatility 25 Index | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 25, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_strong_close": true, "time_filter": "LONDON"}` |
| Volatility 25 Index | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "BOTH", "key_levels": "FVG", "session_cutoff": "23:59", "session_filter_enabled": false, "session_start": "00:00", "target_rr": 4.0, "side": "long", "max_trades_per_day": 4, "day_stop_enabled": false, "min_confluent_levels": 2, "min_inversion_disp_atr": 0.25}` |
| Volatility 75 Index | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 2.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_vol_high": true, "time_filter": "LONDON"}` |
| XAGUSD | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "adx_filter": "RANGE", "time_filter": "LONDON"}` |
| XAGUSD | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 20, "stop_atr_multiple": 5.0, "tp1_rr": 1.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "short", "require_htf_trend": true, "time_filter": "LONDON"}` |
| XAGUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 20, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "both", "require_candle_confirm": true, "time_filter": "NEWYORK"}` |
| XAGUSD | Bias IFVG | `{"max_hold_bars": 288, "bias_mode": "OFF", "ifvg_leg_mode": "BOTH", "key_levels": "FVG", "session_cutoff": "23:59", "session_filter_enabled": false, "session_start": "00:00", "target_rr": 5.0, "side": "long", "max_trades_per_day": 4, "day_stop_enabled": false, "require_vol_high": true, "time_filter": "NY_OPEN"}` |
| XAUUSD | SpikeFade | `{"max_hold_bars": 288, "spike_k_atr": 2.0, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_htf_trend": true, "time_filter": "NEWYORK"}` |
| XAUUSD | RangeRevert | `{"max_hold_bars": 288, "revert_k_atr": 3.0, "stop_atr_multiple": 1.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_trend_with": true, "adx_filter": "TREND"}` |
| XAUUSD | RangeBreakout | `{"max_hold_bars": 288, "breakout_lookback": 10, "stop_atr_multiple": 2.5, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "TREND"}` |
| XAUUSD | TrendDrift | `{"max_hold_bars": 288, "require_adx": true, "min_adx_to_trade": 25, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "require_candle_confirm": true}` |
| AUDUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| BTCUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| ETHUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| EURUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| GBPJPY (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| GBPUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| US SP 500 (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| US Tech 100 (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| USDJPY (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| XAGUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| XAUUSD (one-setting) | TrendDrift | `{"max_hold_bars": 288, "require_adx": false, "stop_atr_multiple": 5.0, "tp1_rr": 8.0, "max_trades_per_day": 4, "max_daily_risk_pct": 4.0, "side": "long", "adx_filter": "RANGE", "require_vol_high": true}` |
| AUDUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| BTCUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| ETHUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| EURUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| GBPJPY (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| GBPUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| US SP 500 (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| US Tech 100 (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| USDJPY (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| XAGUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |
| XAUUSD (one-setting) | HTF FVG Flip | `{"max_hold_bars": 288, "fvg_displacement_atr_mult": 0.0, "fvg_displacement_body_pct": 0.0, "htf_trend_filter": "COUNTER", "require_retest": true, "require_unfilled_htf_fvg": false, "session_filter_enabled": true, "target_rr": 5.0, "side": "long", "time_filter": "NEWYORK"}` |

## What each confluence actually does

**How to read the table.** Each confluence was switched on by itself, with every other setting fixed. The exits were the same throughout: 2.5 ATR stop and 1:2 target for the four synthetic strategies, and the strategy's own stop with a 1:2 target for HTF FVG and Bias IFVG. The table shows:
- **Added in 2026:** how much it changed the average R per trade in unseen 2026.
- **Helped on:** how many markets it improved.
- **Trades kept:** the share of trades still taken with the confluence on.

Only confluences that helped on most markets are listed.

### Real markets (FX, metals, crypto, indices — 11 markets)

| Strategy | Confluences that help | Added in 2026 | Helped on | Trades kept |
|---|---|---|---|---|
| **TrendDrift** | London window | **+0.24R** | 10/11 | 82% |
| | ADX ranging (< 20) | **+0.17R** | **11/11** | 95% |
| | New York window | +0.17R | 9/11 | 86% |
| | Volatility above daily median | **+0.16R** | **11/11** | 91% |
| | Strong close | +0.07R | 9/11 | — |
| **SpikeFade** | London window | +0.05R | 8/11 | 52% |
| | Volatility high | +0.05R | 9/11 | 71% |
| **RangeRevert** | ADX ranging | +0.05R | 8/11 | 82% |
| | Candle confirms | +0.04R | 8/11 | — |
| **RangeBreakout** | Volatility high | +0.06R | 9/11 | 98% |
| **HTF FVG Flip** | London window | +0.05R | 9/11 | 49% |
| **Bias IFVG** | NY open (09:30–11:00 ET) | +0.06R | 8/11 | 34% |
| | London window | +0.05R | 10/11 | 71% |

**Hurts on real markets:**
- **SpikeFade with candle-confirm or strong-close:** −0.24R to −0.28R. On a spike, waiting for the candle to turn means entering late.
- **SpikeFade with EMA-regime-with:** −0.07R.
- **HTF FVG with inversion ≥ 0.5 ATR:** −0.03R, and it cuts two-thirds of the trades.

### Synthetic indices (14 markets)

| Strategy | Confluences that help | Added in 2026 | Helped on |
|---|---|---|---|
| **TrendDrift** | Volatility high | +0.07R | **12/14** |
| | Strong close | +0.03R | 11/14 |
| | Candle confirms | +0.03R | 12/14 |
| **RangeRevert** | Strong close | +0.05R | 11/14 |
| | Candle confirms | +0.05R | 11/14 |
| | ADX trending | +0.02R | 12/14 |
| **RangeBreakout** | ADX trending | +0.03R | 10/14 |
| **HTF FVG Flip** | Inversion ≥ 0.5 ATR | +0.04R | 9/14 |
| **Bias IFVG** | ADX trending / inversion ≥ 0.25 ATR | +0.01R | 10/14 |

**SpikeFade on synthetics:** no single confluence moves it more than ±0.02R across the 14 indices, and each helps on only about half of them. Its edge is market by market, not a filter that works everywhere. That's why the Crash 300, Crash 500, Step, Volatility 100 and Range Break 200 settings differ from each other.

**Hurts on synthetics:**
- **HTF FVG with the RTH session:** −0.09R. A New York cash session doesn't exist on synthetics.
- **HTF FVG with the New York window:** −0.05R.
- **Bias IFVG with CISD-only key levels:** −0.06R.

### One setting across many markets

This tests whether a single setting works everywhere, instead of tuning one per market:

| Group | Setting | 2023–24 | 2025 | 2026 | Profitable in 2026 on |
|---|---|---|---|---|---|
| Real | **TrendDrift long**, min ADX 0, 5 ATR stop, 1:8, ADX ranging + volatility high | +0.09R | +0.04R | **+0.11R** (1,230 trades) | **8/11**: BTCUSD, ETHUSD, EURUSD, GBPJPY, US SP 500, US Tech 100, USDJPY, XAGUSD |
| Real | **HTF FVG Flip long**, counter-trend, retest, re-taps allowed, no displacement, RTH session, New York window, 1:5 | +0.18R | +0.10R | **+0.16R** (222 trades) | **10/11**: all except US Tech 100 |
| Real | RangeRevert long, stretch 1.5 ATR, 5 ATR stop, 1:8, EMA-regime-with + ADX ranging | +0.17R | +0.09R | +0.08R | 7/11 |
| Synthetic | Every strategy | — | — | ≈ 0R or negative | no strategy positive on more than 6/14 |

On synthetic indices, profitable settings don't carry over from one index to the next. Each index needs its own setting, and those are in the per-market tables.

## How this was tested

**Markets (25).**
- **Synthetic:** Crash 300/500/1000, Boom 300/500/1000, Volatility 25/75/100, Jump 25/100, Step Index, Range Break 100/200.
- **Real:** EURUSD, GBPUSD, USDJPY, AUDUSD, GBPJPY, XAUUSD, XAGUSD, BTCUSD, ETHUSD, US Tech 100, US SP 500.

**Data.**
- MT5 (Deriv-Demo) M5 bars from Oct 2022 to 11–12 Sep 2026, plus M15, H1 and H4 for the FVG strategies.
- US Tech 100 and US SP 500 history starts 22 Jan 2024, so their selection period is 2024 only.

**Three periods, each used for one job only.**

| Period | Dates | Role |
|---|---|---|
| Select | 2023-01-01 → 2024-12-31 | Settings are chosen here only |
| Validate | 2025 | The chosen setting must also be profitable here |
| Unseen | 2026-01-01 → 2026-09-12 | Never used for choosing; this is the honest test |

A setting is listed as **held** only if it made money in all three periods.

**Trade rules, identical in research and in the app.**
- **Entry:** the next bar's open after the signal. Stop and target are measured from the actual fill.
- **Positions:** one at a time per market, at most 4 signals a day. A signal counts toward the daily limit when it fires, even if a trade is already open.
- **Exits:** stop, target, or **288 M5 bars (one day) after entry, at that bar's close**.
- **Costs:** each trade pays its entry bar's spread.
- **Crash/Boom/Jump/Range Break stops:** filled with the tick-measured spike rule (`fill_model.SPIKE_FILLS`), not at the stop price. Without this, random entries on Crash showed a fake +0.46R.
- **App extras:** the app backtester also charges slippage and commission, so its returns come in somewhat below the research.

**What was searched.**
- **SpikeFade:** spike size 2/3/4/5 ATR.
- **RangeRevert:** stretch 1.5/2/3 ATR.
- **RangeBreakout:** 10/20/55-bar channel.
- **TrendDrift:** min ADX 0/20/25.
- **Stops and targets for the four above:** stop 1/2.5/5 ATR × target 1/1.5/2/3/5/8R.
- **HTF FVG Flip:** trend filter, first tap, retest, displacement and RTH session variants.
- **Bias IFVG:** bias source, key-level type, IFVG leg and session variants.
- **HTF FVG and Bias targets:** 1/1.5/2/3/4/5R; stops are the strategy's own swing stop.
- **Sides:** long, short, both.
- **Confluences:** none, one, or any two of the flags below.

**Confluences taken apart.** Each one is a separate on/off switch in the Backtester and Settings.

| Confluence | Meaning |
|---|---|
| trend_with | EMA20/EMA50 regime points the trade's way |
| htf_trend | Close on the trade's side of a rising/falling 600-bar M5 EMA (≈ H1 trend) |
| adx_trend / adx_range | ADX(14) ≥ 20 / < 20 |
| vol_high | ATR(14) at or above its one-day median |
| candle_confirm | Signal candle closes the trade's way |
| strong_close | Signal candle closes in its outer 30% |
| london / newyork | Signal between 07:00–16:00 / 12:30–21:00 UTC |
| first_tap (HTF) | Only the gap's first tap |
| inv_disp_025 / 050 | Inversion close ≥ 0.25 / 0.5 ATR beyond the gap |
| stop_ok | Skip setups whose structural stop was widened to the floor |
| levels_conf1 / 2 (Bias) | 1 / 2 other key levels overlap the tapped one |
| h4_aligned (Bias) | Latest H4 FVG points the trade's way |
| rth / ny_open | 09:30–16:00 / 09:30–11:00 New York time |

**How a pick is chosen per market and strategy.**
- Only the select period is used. It needs at least 60 trades with a positive average.
- It must also be positive in 2025, with at least 20 trades.
- The best score (average R × √trades) wins.
- Only confluences the engines can reproduce are allowed.

**Honest limit.** About 6,500 combinations were tried per market. A genuine edge that survives that much searching needs a t-statistic near 3.9. No single pick reaches it. So the "held in 2026" column is the evidence to weigh, along with how many quarters were profitable.

**To reproduce any row in the Backtester.**
- **Account:** $10,000, 1% risk, STATIC sizing, CONSERVATIVE fills, TP count 1.
- **Target:** TP1 R:R = the row's target, and min R:R at or below it.
- **Strategy params:** the row's parameters, plus `max_hold_bars` = 288.
- **Synthetic strategies:** max trades per day 4, max daily risk 4%.
- **Bias IFVG:** max trades per day 4, day-stop off.
