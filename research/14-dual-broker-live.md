14 — Dual-broker live run: Deriv vs FundedNext, and a codebase audit
=====================================================================

**2026-08-31, market open** (Monday, London/NY session)
Scripts: `dual_broker.py`, `tz_offsets.py`, `live_fetch.py`, `full_backtest.py`,
`run_full_backtest.py`, `portfolio_report.py`

---

## 1. Both terminals reached independently — and the feeds verified different

The MetaTrader5 Python package holds **one terminal connection per process**, and
if you do not pass `path=` it attaches to whichever terminal it finds first. That
is exactly how you end up unknowingly comparing a broker against itself, so each
connection is pinned by executable path and re-verified on every call.

| | Deriv | FundedNext |
|---|---|---|
| terminal | `C:\Program Files\MetaTrader 5 Terminal` | `C:\Program Files\MetaTrader 5` |
| account | 6194570 @ Deriv-Demo | 11869564 @ FundedNext-Server |
| company | Deriv.com Limited | FundedNext Ltd |
| balance | $9,570.53 | $22,993.35 |
| symbols | 798 | 76 |
| XAUUSD bid/ask | 4439.93 / 4440.09 | 4440.04 / 4440.49 |
| XAUUSD spread | **16 pts (0.16)** | **45 pts (0.45)** |

**Verdict: genuinely different feeds.** Different companies, different balances,
different symbol counts, and materially different spreads on the same metal.

---

## 2. A data bug that invalidated the previous comparison

The first cross-broker check reported a **$22.60 mean close difference** on
XAUUSD, with a maximum of $135.81 — while bid/ask sat 11 cents apart. Those two
facts cannot both describe the same market.

**Cause: MT5 returns bar and tick timestamps in the broker's own server
timezone, not UTC.**

| broker | server offset | measured from |
|---|---|---|
| Deriv | **UTC+0** | XAUUSD, EURUSD, BTCUSD ticks vs real UTC |
| FundedNext | **UTC+3** | same |

Intersecting raw timestamps was comparing **13:00 Deriv against 10:00
FundedNext** — three hours of gold movement, not a pricing difference.

**Report 13's entire cross-broker comparison was computed on misaligned bars and
should not be relied on.** Every timestamp is now shifted to true UTC on the way
into the cache (`live_fetch.py`).

---

## 3. Live costs — this reverses report 13's headline

Report 13 was built on **Friday-close spreads** read on a Sunday. Re-read during
Monday's session:

| instrument | Deriv cost R | FundedNext cost R | FN / Deriv |
|---|---:|---:|---:|
| EURUSD | 0.2074 | **0.0188** | **0.09×** |
| USDJPY | 0.1788 | **0.0513** | 0.29× |
| ETHUSD | 0.2030 | **0.0607** | 0.30× |
| AUDUSD | 0.2193 | **0.0798** | 0.36× |
| GBPUSD | 0.1551 | **0.0704** | 0.45× |
| XRPUSD | 0.1877 | 0.1273 | 0.68× |
| USDCHF | 0.2096 | 0.1899 | 0.91× |
| GBPJPY | 0.1352 | 0.1328 | 0.98× |
| EURGBP | 0.2805 | 0.2774 | 0.99× |
| BTCUSD | 0.0917 | 0.0940 | 1.03× |
| HK50 | 0.1241 | 0.1509 | 1.22× |
| GER30 | 0.0342 | 0.0482 | 1.41× |
| XPTUSD | 0.7283 | 1.0494 | 1.44× |
| SPX500 | 0.0474 | 0.0745 | 1.57× |
| NTH25 | 0.2429 | 0.3967 | 1.63× |
| XAGUSD | 0.0904 | 0.1555 | 1.72× |
| NDX100 | 0.0203 | 0.0357 | 1.76× |
| XAUUSD | **0.0167** | 0.0468 | 2.80× |

**Median ratio 1.01× — the two brokers cost about the same overall.**
FundedNext is cheaper on **9 of 18**.

### Corrections this forces on report 13

| report 13 claim (weekend data) | truth at market open |
|---|---|
| "FundedNext is ~2× dearer (median 1.95×)" | **median 1.01× — a wash** |
| "FX is 3–5× worse on FundedNext" | **FX is 2–11× BETTER on FundedNext** |
| "GBPUSD 5.00× dearer" | **0.45× — less than half Deriv's cost** |
| "EURGBP 1.769 R, untradeable" | 0.277 R — high, but tradeable |
| "cheapest are GER30/NDX100" | **cheapest are EURUSD (0.019 R) and NDX100 (0.036 R)** |

Weekend spreads were inflating costs by roughly **10× on FX**. FundedNext
EURUSD read 0.395 R on Sunday and **0.0188 R** on Monday — a 21× difference.

### Why this matters beyond bookkeeping

Reports 08 and 12 concluded that confluence edges (+0.018 to +0.027 R) were
hopeless because costs were 0.2–0.4 R. **At real trading-hours costs of
0.017–0.09 R on the good instruments, that verdict no longer holds
automatically.** A +0.027 R edge against a 0.019 R cost is thin but no longer
absurd. The Riptide result still stands on its own measurement, but the
*reasoning* that killed it — "costs are ten times any signal" — was partly an
artefact of weekend data.

**Cheapest instruments, market open:**
Deriv — XAUUSD 0.017, NDX100 0.020, GER30 0.034, SPX500 0.047, XAGUSD 0.090.
FundedNext — EURUSD 0.019, NDX100 0.036, XAUUSD 0.047, GER30 0.048, USDJPY 0.051.

---

## 4. Codebase audit

### 4.1 Live and backtest run DIFFERENT exit code — twice

| concern | backtest path | live path | risk |
|---|---|---|---|
| **Trailing** | `RiskEngine` → `TrailingManager.calculate_trailing_sl()` | `position_manager._calculate_trailing_sl()` | **two implementations** |
| **Break-even** | `backtester/engine._breakeven_stop()` | `BreakevenManager.check_breakeven()` | **two implementations** |

`backtester/engine.py` opens with *"Bar-by-bar backtesting engine using the same
RiskEngine as live"* — true for signal evaluation and sizing, **not** for these
two exit paths.

Checked and currently **aligned**: both trailing implementations support the same
four methods (`ATR_TRAIL`, `STRUCTURE_TRAIL`, `FIXED_PIPS`, `PCT_TRAIL`), the
same defaults (`atr_trail_multiplier` 1.5, `trail_step_pips` 5.0), and both
honour `trail_require_be_first`, `trail_mode` and `trail_trigger_rr`.

**So there is no divergence today — but there is nothing preventing one.** Any
future change to trailing must be made twice. This is the single highest-value
refactor in the codebase: delete `position_manager._calculate_trailing_sl` and
call `TrailingManager`.

### 4.2 The exit ladder from report 11 was never implemented

`grep` for ladder/ratchet logic in `backend/` returns only unrelated comments.
The `(trigger_R → stop_R)` ratchet — the recommendation that measured best on
**both** brokers (17/18 symbols, ~30% less drawdown) — **does not exist in the
code**. Nothing to verify, because nothing was built.

### 4.3 Dead and orphaned configuration

| parameter | status |
|---|---|
| `be_offset_pips` | **dead — 0 read sites anywhere outside the schema** |
| `trail_activation_rr` / `trail_trigger_rr` | duplicate names for one value; `config_schema.py:1134` exists purely to validate they agree |

**Backend parameters with no frontend control** (settable via API, invisible in
the UI):

- `trail_require_be_first`
- `trail_trigger_tp_level`
- `be_trigger_tp_level`
- `be_spread_multiple`

`trail_require_be_first` is the notable one: it gates whether trailing can start
before break-even, it was added as a deliberate fix, and a user cannot reach it.

### 4.4 API surface

76 backend routes, 71 referenced by `frontend/src/services/api.js`. Five backend
routes are never called by the frontend:

`/analyses`, `/circuit-breaker/reset`, `/keys`, `/prop-firm/reset-breach`,
`/signals/{signal_id}/snapshot`

`/circuit-breaker/reset` and `/prop-firm/reset-breach` are recovery actions with
no UI — the same class of problem as bug **B2** (a stuck state a user cannot
clear).

### 4.5 Not a bug, checked and cleared

`BOS fired` appears up to 38 times on an identical price level. Verified against
`market_structure.py:149`: deduplication is by **bar index**, not price, and
distinct swings can legitimately share a price. Consistent with observation.

---

## 5. Full backtest — in progress

Every strategy × every asset × RR 1:2, 1:3, 1:4, 1:5, on both brokers, $10,000
capital, 1% risk, live spreads, driven through the real strategy engines with
correctly-sliced multi-timeframe data. Roughly 300 cells at ~2/minute.

**Early observations from the completed cells (small samples — treat as
provisional):**

| broker | symbol | strategy | best RR | P&L | maxDD | n | WR | PF |
|---|---|---|---|---:|---:|---:|---:|---:|
| Deriv | US Tech 100 | NYOpenRetest | 1:3 | **+$2,546** | 3.0% | 20 | 55.0% | 3.54 |
| Deriv | SOLUSD | APA | 1:5 | **+$1,885** | 2.8% | 13 | 61.5% | 4.43 |
| Deriv | US SP 500 | VWAP | 1:3 | +$438 | 7.2% | 27 | 33.3% | 1.25 |
| Deriv | US Tech 100 | HTFFVGFlip | 1:5 | +$391 | 1.0% | 2 | 50.0% | 4.87 |
| Deriv | US Tech 100 | BiasIFVG | 1:3 | +$280 | 2.0% | 8 | 37.5% | 1.64 |
| Deriv | SOLUSD | CRT | 1:2 | −$1,038 | 10.6% | 10 | 20.0% | 0.20 |

**A caveat that matters:** signal counts are **2–28 per cell**, far below the
hundreds in the database sweep. The strategies are highly selective over the
8,000-bar window used here. Cells with fewer than ~30 trades cannot support a
verdict (§0.5-5), and several of the best-looking numbers above sit on 2 to 13
trades. The cumulative and correlation views will carry more weight than any
single cell.

Sharpe and Sortino are reported as `n/a` where there are fewer than 5 trading
days or 5 losing days respectively — with a handful of trades the downside
deviation collapses and the ratio becomes meaningless (a Sortino of 129 was
produced before this guard was added).
