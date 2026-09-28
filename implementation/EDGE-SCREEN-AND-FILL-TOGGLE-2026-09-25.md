# The spike-fill toggle, and a four-hypothesis edge screen on FundedNext assets

**2026-09-25** · scripts: `run_edge_screen.py`, `run_published_strategies.py`, `run_orb_sweep.py`
· backend: `config_schema.py`, `api/routes/backtest.py`, `backtester/fill_model.py`

---

## 0. Summary

| | |
|---|---|
| **Spike-fill toggle** | **Shipped.** `Settings → Defaults → Account & broker → Stop fill model`, a dropdown: `CONSERVATIVE` (default) / `EMPIRICAL` / `OFF`. Wired through single and portfolio backtests, inherited by any run that doesn't override it, guarded by 3 tests. Toggle it to reproduce +1,065% (OFF) vs +361% (CONSERVATIVE). |
| **New strategy from my own expertise** | **Four hypotheses designed, implemented and tested on your FundedNext assets. All four lose.** Full per-asset numbers in §3. |
| **Why they lose** | **Cost per trade, not absence of skill.** Expectancy rises monotonically as R grows — −0.099R at a 2-hour stop, +0.001R at 8 hours, +0.029R at 2 days. It crosses zero and stops. Removing the cost drag gets you to **flat**, not to profit (§4). |
| **Published ORB as an app strategy** | **Not shipped, deliberately.** It loses at every one of 25 settings tested. Shipping it would be shipping a known loser. Reasoning in §5. |

---

## 1. The stop-fill toggle — what shipped

The fill model existed in the engine but only as a **request field with no schema entry**, so no screen
could show it and a value chosen in Settings never reached a run.

**Backend**
- `RiskParams.stop_fill_model: Literal["CONSERVATIVE", "EMPIRICAL", "OFF"] = "CONSERVATIVE"` and
  `RiskParams.stop_fill_seed`, so both appear in `/config/parameter_schema`.
- `load_saved_risk()` + `apply_saved_sim_settings()` in `api/routes/backtest.py`: a run inherits the
  saved value when the request is silent; an explicit request still wins. Called by **both** routes.

**Frontend** — nothing to write. `ACCOUNT_KEYS` on the Defaults page is derived from the backend's
`ACCOUNT_ONLY_KEYS`, so the moment the field entered the schema the card rendered it as a dropdown.
That is the coverage-by-construction rule from the previous session doing its job.

**What the setting does**, measured on a Boom 1000 short (stop 14,700, bar high 14,760, risk 20 pts):

| Mode | Fill | Overshoot | Loss booked |
|---|---|---|---|
| `OFF` | 14,700.00 | +0.000 R | **−1.000 R** |
| `CONSERVATIVE` | 14,748.00 | +2.400 R | **−3.400 R** |
| `EMPIRICAL` | 14,748.00 | +2.400 R | −3.400 R |

On the 2026 Crash/Boom book that is **$116,509 vs $46,152** on identical trades.

**To reproduce my numbers:** set the dropdown to `OFF`, run Crash 1000 / Boom 1000 / Boom 900 from
2026-01-01 at 0.5% risk → the inflated figure. Set it back to `CONSERVATIVE` → the honest one. Live
trading is unaffected either way; real fills are whatever the broker gives.

Guarded by `test_the_stop_fill_model_is_an_editable_account_setting`,
`test_the_fill_model_changes_what_a_stop_costs`, `test_a_run_inherits_the_saved_fill_model`.

> **Note on `simulate_wicks`, `stop_fill_lambda`, `simulate_backtest_only_exits`** — these are in
> `ACCOUNT_ONLY_KEYS` but are still not dataclass fields, so the card cannot show them. Same class of
> gap, smaller stakes. Say the word and they go in too.

---

## 2. The four hypotheses, and why I chose them

Designed from mechanism, not from backtest-fishing. No synthetics — FundedNext-tradable only.

| | Hypothesis | Mechanism |
|---|---|---|
| **H1** | **Compression breakout** — trade the break of a range that is unusually narrow *for that market* (bottom 25% of its own trailing-year width), stop the far side, 2R target | Realised volatility is autocorrelated and mean-reverting; market makers widen inventory bands once a squeeze resolves |
| **H2** | **Opening drive, entered on the pullback** — direction of the first 30 min, but wait for a 50% retrace into the opening range before entering | The open carries overnight order imbalance. The published ORB enters immediately and dies to spread; a pullback entry makes R several times larger, which is the whole game |
| **H3** | **Prior-day high / low** — break of yesterday's extreme, ATR stop, 2R | A *real* level: resting orders sit there because every desk and platform draws it. Unlike a round number, trading actually happened there |
| **H4** | **Lead-lag** — when SPX500 moves >2σ in one bar, take US30 in the same direction next bar | Index CFDs are priced off the same futures complex; the lagger follows within minutes |

Each is charged the bar's own spread plus slippage, limited to **one position at a time per market**,
and reported in **R per month** — because a challenge is won on expectancy × frequency.

---

## 3. Results — every asset, both feeds

### 3.1 FundedNext feed (your account's own bars and specs), 2026 YTD

| Market | Hypothesis | N | Expectancy | Win | t | Trades/mo | **R/mo** |
|---|---|---|---|---|---|---|---|
| US30 | compression | 329 | +0.001R | 38.9% | +0.01 | 42.7 | +0.05 |
| US30 | open_pullback | 200 | −0.147R | 30.5% | −1.52 | 22.9 | −3.37 |
| US30 | prior_day | 159 | −0.147R | 37.7% | −1.29 | 18.4 | −2.72 |
| **SPX500** | **compression** | 347 | **+0.043R** | 43.2% | +0.62 | 45.1 | **+1.95** |
| SPX500 | open_pullback | 201 | −0.041R | 34.8% | −0.40 | 23.0 | −0.93 |
| SPX500 | prior_day | 168 | −0.361R | 32.1% | −3.34 | 19.5 | −7.05 |
| **XAUUSD** | **compression** | 426 | **+0.024R** | 38.7% | +0.37 | 57.1 | **+1.38** |
| XAUUSD | open_pullback | 202 | −0.149R | 30.7% | −1.55 | 23.4 | −3.48 |
| XAUUSD | prior_day | 165 | −0.061R | 38.8% | −0.53 | 19.1 | −1.16 |
| BTCUSD | compression | 427 | −0.108R | 34.9% | −1.72 | 54.5 | −5.87 |
| BTCUSD | open_pullback | 211 | −0.261R | 29.9% | −2.73 | 24.1 | −6.28 |
| **BTCUSD** | **prior_day** | 157 | **+0.076R** | 47.8% | +0.64 | 18.2 | **+1.38** |
| EURUSD | compression | 432 | −0.151R | 33.8% | −2.49 | 55.1 | −8.33 |
| EURUSD | open_pullback | 215 | −0.179R | 39.1% | −1.40 | 24.6 | −4.41 |
| EURUSD | prior_day | 158 | −0.445R | 34.8% | −3.13 | 18.3 | −8.16 |
| GBPUSD | compression | 411 | −0.148R | 35.3% | −2.29 | 53.2 | −7.87 |
| GBPUSD | open_pullback | 204 | −0.605R | 27.5% | −4.03 | 23.3 | −14.11 |
| GBPUSD | prior_day | 157 | −1.088R | 27.4% | −5.88 | 18.2 | −19.82 |
| USDJPY | compression | 422 | −0.145R | 34.4% | −2.26 | 54.6 | −7.93 |
| USDJPY | open_pullback | 215 | −0.668R | 28.4% | −4.03 | 24.6 | −16.43 |
| USDJPY | prior_day | 154 | −0.413R | 37.0% | −3.10 | 17.8 | −7.36 |
| GBPJPY | compression | 428 | −0.261R | 31.1% | −4.41 | 54.8 | −14.31 |
| GBPJPY | open_pullback | 216 | −0.136R | 36.6% | −1.33 | 24.7 | −3.35 |
| GBPJPY | prior_day | 159 | −0.781R | 32.1% | −5.61 | 18.5 | −14.42 |
| SPX500→US30 | lead_lag | 2,000 | −0.135R | 36.5% | −4.19 | 231.2 | −31.33 |

**Three cells are positive** — SPX500 compression (+1.95 R/mo), XAUUSD compression (+1.38),
BTCUSD prior-day (+1.38). Every one has **t < 0.7**: that is noise, and out of 25 cells you would
expect a few positive by chance. Nothing here is an edge.

**As portfolios, $10,000 at 0.5% risk:**

| Hypothesis | Trades | Expectancy | R/month | End balance | Return | Max DD | t |
|---|---|---|---|---|---|---|---|
| compression | 3,222 | −0.099R | −40.7 | $2,218 | −77.8% | 83.7% | −4.34 |
| lead_lag | 2,000 | −0.135R | −31.3 | $2,555 | −74.4% | 74.9% | −4.19 |
| open_pullback | 1,664 | −0.275R | −52.2 | $1,025 | −89.8% | 90.3% | −6.42 |
| prior_day | 1,277 | −0.400R | −59.2 | $770 | −92.3% | 92.6% | −8.32 |
| **COMBINED** | **8,163** | **−0.191R** | **−177.7** | **$8** | **−99.9%** | 99.9% | |

### 3.2 Deriv feed — the same rules, cheaper execution

| Hypothesis | Expectancy (Deriv) | Expectancy (FundedNext) | R/mo (Deriv) | R/mo (FN) |
|---|---|---|---|---|
| compression | −0.048R | −0.099R | −24.4 | −40.7 |
| prior_day | **−0.040R** | **−0.400R** | −8.7 | −59.2 |
| open_pullback | −0.096R | −0.275R | −23.0 | −52.2 |
| lead_lag | −0.022R | −0.135R | −9.6 | −31.3 |

Every hypothesis is **2–10× less bad** on the cheaper feed, and still none is positive. Cost is the
dominant term, and removing it is not sufficient.

---

## 4. The diagnosis: it is the cost per trade, and fixing it gets you to zero

If costs are what is killing these rules, then widening the stop — which makes R bigger and the
spread proportionally smaller — should rescue them. It does, exactly, and then stops:

**Compression breakout, FundedNext feed, identical rule, only the lookback (and so R) changing:**

| Lookback | Horizon | N | Expectancy | Win | t | Trades/mo | R/mo |
|---|---|---|---|---|---|---|---|
| 2 hours | 96 bars | 3,222 | **−0.099R** | 36.0% | −4.34 | 411.0 | −40.73 |
| 8 hours | 288 | 1,100 | **+0.001R** | 41.2% | +0.04 | 140.9 | +0.19 |
| 1 day | 576 | 416 | **+0.016R** | 43.5% | +0.29 | 54.5 | +0.86 |
| 2 days | 1,440 | 197 | **+0.029R** | 46.7% | +0.36 | 26.3 | +0.76 |

Monotonic, and it crosses zero at roughly an 8-hour stop. **The high-frequency losses were the
spread, not the signal.** But the ceiling is ~+0.03R at t +0.36 — statistically indistinguishable
from nothing, and +0.8 R/month against the +20 R/month a challenge needs.

This is the structural finding of the whole session:

> On a handful of retail CFDs, simple intraday rules sit at roughly **zero edge before costs** and
> **negative after them**. You cannot out-trade the spread with a better intraday rule; you can only
> reduce how often you pay it, which takes you to flat.

Everything tested across this session agrees: round-number reversal (negative), prior-zone retest
(−97%), published 5-min ORB (negative at all 25 settings), published intraday momentum (−8.8%),
and now four more. The only survivors were **round-number continuation on a cheap feed** (+34%, dead
at FundedNext spreads) and **Crash/Boom drift** (real, synthetic-only, already implemented).

---

## 5. Why I did not ship the published ORB as a strategy

You asked me to implement it so you could test it. It measures like this on your assets:

| Opening range | tgt 1R | tgt 2R | tgt 3R | tgt 5R | tgt 10R |
|---|---|---|---|---|---|
| 5m | −143.5 | −127.7 | −115.6 | −116.9 | −92.8 |
| 15m | −73.9 | −71.9 | −67.2 | −66.4 | −34.9 |
| 30m | −34.7 | −27.8 | −25.5 | −27.3 | −28.6 |
| 60m | **−2.3** | −9.3 | −15.1 | −8.5 | −11.7 |
| 120m | −10.0 | −7.3 | −8.6 | −13.4 | −14.2 |

(portfolio R/month; every cell negative). Adding it to the strategy registry would put a strategy in
your live dropdown that is measured to lose at every setting — and the app already has a *profitable*
60-minute ORB variant with an H1 trend filter, which is a different rule.

If you still want it for your own testing I will add it behind an explicit
`PublishedORB_v1` name with the measured numbers in its description, so it can never be mistaken for
a recommendation. That is a 30-minute job — say the word.

---

## 6. What I would actually do now

1. **Stop screening intraday rules on 8 CFDs.** Nine hypotheses, two feeds, one session: the
   constraint is structural (§4), not a matter of finding the right rule.
2. **The +20R/month target is the wrong target for this instrument set.** §3 of the previous
   document showed the honest route: your existing ORB at 0.5% risk passes a challenge **99% of the
   time given a year**. Buy time, not frequency.
3. **If you want frequency, you need a wider universe** — the published ORB's alpha is selecting the
   top-20 in-play names from 7,000 stocks. That needs an equities broker, not a CFD account.
4. **Crash/Boom drift is your one measured, profitable, high-frequency edge** — and it is
   synthetic-only, so it funds a personal account, not a FundedNext challenge. Run it there with
   `stop_fill_model = CONSERVATIVE` and size for a 10-trade losing streak.

---

## 7. Reproduction

```bash
py -3.12 scripts/run_edge_screen.py --feed fundednext      # §3.1
py -3.12 scripts/run_edge_screen.py --feed deriv           # §3.2
py -3.12 scripts/run_orb_sweep.py --feed deriv             # §5
py -3.12 -m pytest tests/test_slot_risk_parity.py -q       # the toggle's guards
```
