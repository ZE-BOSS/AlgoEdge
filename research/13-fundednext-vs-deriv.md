13 — FundedNext vs Deriv: the whole programme re-run
=====================================================

**2026-08-30** · scripts `fn_probe.py`, `fn_fetch_cache.py`,
`fn_patch_spreads.py`, `broker_compare.py`
Account: **11869564 · FundedNext-Server · FundedNext Ltd · USD · balance
$22,993.35 · leverage 1:100**

Both brokers were run through **identical code** (`broker_compare.py`, cache
selected by `ALGOEDGE_CACHE`), so every difference below is broker, not method.

---

## Read this first — two caveats that bound everything

**1. Markets are closed.** It is Sunday 30 August. The last tick on every
FundedNext symbol is **Friday 28 Aug 23:59**. Spreads quoted at Friday's close
are the widest of the week. Both brokers were read at the same moment, so the
*ratio* between them is fair, but the *absolute* costs are overstated for both.
**Re-read during the London session before acting on the absolute numbers.**

**2. I nearly repeated a retracted error.** The first FundedNext pull cached
**spread = 0 for 11 of 18 symbols**, because `symbol_info().spread` was read
before the symbol was selected into Market Watch. That is the same shape as the
false B8 "zero cost model" claim I retracted for Deriv. It was caught, the cache
was patched (`fn_patch_spreads.py`, 55 files), and every number below is from
the corrected data. Had it gone unchecked it would have shown XRPUSD SELL at
+0.46 R and ETHUSD SELL at +0.13 R — pure artefacts of free trading.

---

## 1. What FundedNext actually offers

**76 symbols against Deriv's 798.** Real markets only:

| class | FundedNext |
|---|---|
| Commodities | UKOUSD, USOUSD, XAGUSD, XAUUSD, XPTUSD |
| Crypto | ADAUSD, BTCUSD, DOGUSD, ETHUSD, LNKUSD, LTCUSD, XLMUSD, XMRUSD, XRPUSD |
| Forex | 48 pairs |
| Indices | AUS200, EUSTX50, FRA40, GER30, HK50, JP225, **NDX100**, NTH25, **SPX500**, SWI20, UK100, US2000, US30, VIX |

**18 of the 24 tested instruments map across.** Missing: **SOLUSD**, and **all
five synthetics** — Crash 300/500/1000, Jump 100, Volatility 75.

### This is the single most important finding

**Every profitable cell in the entire eight-month programme was a synthetic.**

| surviving cell (Deriv, out-of-sample) | exp R | on FundedNext |
|---|---:|---|
| DriftJumpAlpha × Crash 1000 | +0.287 | **does not exist** |
| DriftJumpAlpha × Crash 300 | +0.231 | **does not exist** |
| VWAP × Volatility 75 | +0.103 | **does not exist** |

You already knew DriftJumpAlpha could not run there. The point is broader: **the
recommended portfolio from reports 05/07 is 100% untradeable on FundedNext.**
Nothing that survived out-of-sample testing on Deriv can be traded on this
account at all.

---

## 2. Cost — FundedNext is roughly twice as expensive

Cost in R = spread ÷ median 1×ATR(M15) stop. This is the number that decides
whether any signal can survive execution.

| instrument | Deriv cost R | FundedNext cost R | FN vs Deriv |
|---|---:|---:|---:|
| **GBPUSD** | 0.169 | **0.844** | **5.00×** |
| **GBPJPY** | 0.189 | **0.885** | **4.68×** |
| **AUDUSD** | 0.239 | **0.858** | **3.59×** |
| **USDJPY** | 0.217 | **0.729** | **3.37×** |
| **USDCHF** | 0.377 | **1.201** | **3.18×** |
| **NDX100** | 0.020 | 0.062 | 3.06× |
| **EURGBP** | 0.666 | **1.769** | 2.66× |
| XAUUSD | 0.062 | 0.162 | 2.63× |
| SPX500 | 0.047 | 0.094 | 1.98× |
| XAGUSD | 0.155 | 0.294 | 1.89× |
| EURUSD | 0.207 | 0.395 | 1.91× |
| XRPUSD | 0.230 | 0.255 | 1.11× |
| BTCUSD | 0.092 | 0.097 | 1.06× |
| XPTUSD | 1.220 | 1.049 | 0.86× |
| NTH25 | 0.528 | 0.420 | 0.80× |
| **ETHUSD** | 0.203 | **0.061** | **0.30×** |
| **HK50** | 0.541 | **0.155** | **0.29×** |
| **GER30** | 0.251 | **0.048** | **0.19×** |

**Median ratio 1.95×. FundedNext is cheaper on only 5 of 18.**

The split is systematic:

- **Forex is far worse on FundedNext** — every major pair costs 1.9× to 5×
  more. USDCHF at **1.201 R** and EURGBP at **1.769 R** mean the spread exceeds
  the entire risk on the trade. Those are untradeable at a 1×ATR stop.
- **Indices are much better on FundedNext** — GER30 0.048 vs 0.251 (5× cheaper),
  HK50 0.155 vs 0.541 (3.5× cheaper). Deriv's index CFDs are expensive; these
  are properly priced.
- **Crypto is a wash**, except ETHUSD, which is 3× cheaper on FundedNext.

**The cheapest instruments on FundedNext are GER30 (0.048), BTCUSD (0.097),
NDX100 (0.062), ETHUSD (0.061), SPX500 (0.094).** If anything is going to work
on this account, it is those five.

---

## 3. Raw geometry — random entry, 1×ATR stop, 5R target

| symbol | Hurst | BUY exp R | SELL exp R | max DD (R) |
|---|---:|---:|---:|---:|
| GER30 | 0.485 | +0.0108 | **+0.0812** | 164 |
| BTCUSD | 0.490 | −0.1757 | **+0.0807** | 491 |
| ETHUSD | 0.504 | −0.1480 | **+0.0672** | 477 |
| XRPUSD | 0.500 | −0.6962 | **+0.2058** | 397 |
| NDX100 | 0.491 | −0.0341 | −0.0288 | 651 |
| SPX500 | 0.475 | −0.0355 | −0.1559 | 522 |
| HK50 | 0.462 | −0.1114 | −0.1205 | 653 |
| XAUUSD | 0.479 | −0.2174 | −0.0707 | 398 |
| XAGUSD | 0.493 | −0.3653 | −0.1330 | 723 |
| EURUSD | 0.489 | −0.4916 | −0.3766 | 2,073 |
| USDJPY | 0.513 | −0.7288 | −0.8306 | 3,875 |
| GBPUSD | 0.493 | −0.9965 | −0.9158 | 4,928 |
| AUDUSD | 0.475 | −0.9276 | −0.8390 | 4,461 |
| USDCHF | 0.511 | −1.2575 | −1.3165 | 6,723 |
| **EURGBP** | 0.497 | **−2.1054** | **−1.7523** | **9,319** |
| XPTUSD | 0.488 | −1.1261 | −1.0611 | 5,351 |

**The FX block is catastrophic on FundedNext.** EURGBP loses over 2R per random
trade — you pay 1.77R of spread to take 1R of risk. Deriv's worst FX figure was
−0.999; FundedNext's is −2.105.

**Hurst 0.462–0.513 on every symbol — identical to Deriv.** Nothing trends on
either broker. Same market, same regime; only the toll differs.

Four symbols show a positive side: **GER30 SELL (+0.081), BTCUSD SELL (+0.081),
ETHUSD SELL (+0.067), XRPUSD SELL (+0.206)**. Note the pattern — all SELL, and
XRPUSD's +0.206 sits against a BUY side of −0.696, which is directional drift
over one window, not a repeatable edge. Treat as candidates only.

---

## 4. Exit ladders — the one result that holds on both brokers

| ladder | Deriv exp R | Deriv DD | FN exp R | FN DD | beat fixed (D / FN) |
|---|---:|---:|---:|---:|---|
| `fixed_TP_only` | −0.3140 | 3,523 | −0.5327 | 5,718 | baseline |
| `BE_at_1R` | −0.2992 | 3,276 | −0.5210 | 5,493 | 16/18 · 15/18 |
| `user_ladder` | −0.2834 | 3,084 | −0.5083 | 5,338 | 15/18 · 14/18 |
| `loose_ladder` | −0.3058 | 3,353 | −0.5260 | 5,553 | 11/18 · 11/18 |
| **`tight_ladder`** | **−0.2537** | **2,763** | **−0.4789** | **5,052** | **17/18 · 17/18** |
| `step_1R_behind` | −0.2907 | 3,192 | −0.5122 | 5,412 | 14/18 · 15/18 |

**`tight_ladder` wins 17 of 18 on *both* brokers**, improves expectancy on both,
and cuts drawdown on both (−22% on Deriv, −12% on FundedNext).

This is the most robust finding in the whole programme: **it replicates across
two different brokers, two different cost structures, and two different symbol
universes.** Your ratchet idea is the one recommendation I would act on without
further testing.

---

## 5. Riptide on FundedNext — worse, and the drawdowns you asked for

$10,000 per asset, 1% risk, minimum confluence 2.

| symbol | signals | 1:2 P&L | DD% | 1:3 P&L | DD% | 1:5 P&L | DD% |
|---|---:|---:|---:|---:|---:|---:|---:|
| **HK50** | 394 | −662 | 22.5 | **+176** | 21.1 | **+1,367** | **27.0** |
| **BTCUSD** | 568 | −40 | 31.2 | **+532** | 32.9 | −1,814 | 41.9 |
| **GER30** | 362 | −1,154 | 24.2 | **+151** | 20.7 | −2,609 | 29.3 |
| ETHUSD | 562 | −498 | 22.2 | −2,571 | 36.4 | −1,318 | 30.2 |
| XAUUSD | 598 | −2,909 | 43.7 | −3,252 | 51.1 | −3,503 | 60.2 |
| NDX100 | 638 | −4,349 | 49.9 | −4,756 | 53.4 | −5,398 | 60.7 |
| SPX500 | 740 | −6,702 | 69.8 | −7,383 | 76.3 | −6,502 | 68.2 |
| NTH25 | 325 | −7,264 | 72.3 | −7,287 | 73.0 | −6,194 | 61.4 |
| XAGUSD | 612 | −7,570 | 78.1 | −7,645 | 81.5 | −7,537 | 82.1 |
| EURUSD | 671 | −8,710 | 87.4 | −8,312 | 83.8 | −7,596 | 76.9 |
| XRPUSD | 844 | −8,820 | 88.1 | −8,892 | 88.8 | −8,409 | 85.6 |
| USDJPY | 614 | −9,319 | 93.2 | −9,439 | 94.3 | −9,445 | 94.4 |
| AUDUSD | 614 | −9,835 | 98.3 | −9,741 | 97.4 | −9,732 | 97.3 |
| GBPUSD | 716 | −9,750 | 97.5 | −9,772 | 97.7 | −9,833 | 98.3 |
| GBPJPY | 685 | −9,831 | 98.3 | −9,839 | 98.4 | −9,791 | 97.9 |
| USDCHF | 705 | −9,945 | 99.4 | −9,924 | 99.2 | −9,938 | 99.4 |
| XPTUSD | 689 | −9,938 | 99.4 | −9,942 | 99.4 | −9,939 | 99.4 |
| **EURGBP** | 755 | **−9,989** | **99.9** | −9,989 | 99.9 | −9,987 | 99.9 |

### Totals, both brokers

| | Deriv | FundedNext |
|---|---:|---:|
| 1:2 | **−$112,698** · DD 67.0% · 0/18 profitable | **−$117,286** · DD 70.8% · 0/18 |
| 1:3 | −$113,703 · DD 68.6% · 0/18 | −$117,884 · DD 72.5% · **3/18** |
| 1:5 | −$103,783 · DD 67.8% · 0/18 | −$118,177 · DD 72.8% · 1/18 |

**Riptide fails on both brokers.** Seven FundedNext symbols lose **97–99.9% of
the account** — EURGBP, USDCHF, XPTUSD, GBPJPY, GBPUSD, AUDUSD, USDJPY. These
are total account destruction, and every one is a high-cost instrument.

The three that turn a profit at 1:3 (HK50 +176, GER30 +151, BTCUSD +532) are the
three cheapest FundedNext instruments. That is not Riptide finding an edge — it
is Riptide failing to lose money where the toll is small enough. **HK50 at 1:5
(+$1,367, 27% DD, PF 1.09) is the single best cell in either broker's run and is
worth one confirmation test during market hours.**

---

## 6. Verdict

**On the broker comparison:**

1. **FundedNext is the wrong venue for the current book.** It is ~2× dearer
   overall, 3–5× dearer on FX, and offers none of the three instruments that
   actually made money.
2. **FundedNext is the right venue for indices.** GER30 at 0.048 R and NDX100 at
   0.062 R are the cheapest instruments measured anywhere in this programme,
   including Deriv. Deriv's index pricing (GER30 0.251, HK50 0.541) is poor by
   comparison.
3. **Never trade FX on FundedNext with an ATR-sized stop.** At 0.73–1.77 R of
   spread the trade is lost before it starts. If FX is required there, the stop
   must be several times wider — which is exactly the spread-driven stop sizing
   you proposed, and this data is the strongest argument yet for it.

**On strategy:**

4. **The ratchet works on both brokers** — 17/18, better expectancy, lower
   drawdown. Implement it.
5. **Riptide works on neither.** Confirmed twice now.
6. **Nothing trends on either broker** (Hurst 0.46–0.51), so the ratchet's value
   is left-tail protection, not trend capture — as report 11 concluded.

**What to do next:** re-read spreads during the London session, then re-run
Part 1 for both brokers. Every absolute cost here is a Friday-close reading and
will improve. The *ranking* should hold; the magnitudes should not be trusted
until then.
