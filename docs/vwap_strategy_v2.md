# VWAP v2 — Fair Value / Confluence Framework (AlgoEdge)

**Supersedes:** [`vwap_strategy_implementation_plan.md`](vwap_strategy_implementation_plan.md) (v1's engine
rules — the doc's §1 comparison and stop-loss-convention resolution are still current; only the
entry/exit engine described from §2 onward is replaced).

**Implements:** `backend/strategies/strategy_vwap/engine.py` + `params.py`, per Master Implementation
Plan Phase 8 / Part 8.

**Source:** `implementation/strategy_update/` — three TapeDragon carousels (Order Book/DOM, Bookmap
liquidity heatmap, VWAP). Only the VWAP carousel is implemented here; the order-flow/DOM material is
covered separately (Phase 10 / `docs/orderflow_data_layer.md`).

---

## 1. Why v1 needed replacing

The source material's stated framework: VWAP gives **four things** — fair value, trend context,
extremes (±1σ/±2σ/±3σ bands), and confluence. Formula: `context + confluence + confirmation =
high-probability trade`. Explicit warning: **"let VWAP guide your bias, not your entry."**

v1 implemented none of the band framework and inverted the core principle — VWAP itself (via a single
pullback trigger) *was* the entry signal, not a bias filter. Gap analysis (Master Plan §8.3):

| Source requirement | v1 | v2 |
|---|---|---|
| σ bands (±1/±2/±3) | Absent | `_calculate_anchored_vwap_with_bands()` |
| VWAP guides bias, not entry | VWAP *is* the trigger | VWAP is bias; bands are the entry reference |
| Pullback moves *toward* VWAP | Any red/green candle qualified | `pullback_requires_convergence`: distance to VWAP must shrink vs. prior bar |
| First pullback only | Not tracked | `first_pullback_only` + `pullback_taken_this_session` |
| Volume confirmation | None | `volume_confirmation_mult` on both setups |
| Mean reversion (extremes) | Not modelled at all | Setup 2: Band Reversion |
| Confluence score | Hardcoded `80` | Real 0–100, 5-component |

---

## 2. VWAP + bands (`_calculate_anchored_vwap_with_bands`)

Session-anchored (resets at 09:30 ET each trading day, matching v1's `_calculate_anchored_vwap`), now
computed alongside a running volume-weighted standard deviation in the same pass:

```
running_vwap   = cumsum(vol * typical_price) / cumsum(vol)         # per session
sq_dev         = vol * (typical_price - running_vwap)^2
variance       = cumsum(sq_dev) / cumsum(vol)                      # or rolling, if vwap_band_lookback > 0
std            = sqrt(variance)
band(k*sigma)  = vwap ± k * std
```

Using the *running* VWAP (not the session's final value) for the deviation term avoids a
forward-looking band. `vwap_band_sigmas` (default `[1.0, 2.0, 3.0]`) controls which multiples are used;
`vwap_band_lookback` (default `0`) switches the std term from session-cumulative to a rolling window if
set. `vwap_bands_enabled=False` disables the whole band framework and the engine falls back to v1
behaviour exactly (`entry_mode` is forced to `PULLBACK_TO_VALUE`, and stops resolve via
`_resolve_sl_distance`'s ATR/fixed-points method instead of band levels).

---

## 3. Setup 1 — Pullback to Value (trend continuation)

Entry conditions (all must hold on the trigger bar; entry fills at the *next* bar's open):

1. `price > VWAP` (BUY) or `price < VWAP` (SELL)
2. VWAP sloping the same direction (`vwap_now` vs. `vwap_prev`, compared one anchor-window back)
3. Momentum confirms: lookback price move ≥ `momentum_threshold_pct`
4. Price is inside `pullback_max_distance_sigma` (default 1.0×σ) of VWAP
5. Distance to VWAP is **shrinking** vs. the prior bar (`pullback_requires_convergence`, default `True`)
   — replaces v1's bare red/green candle test, which admitted a candle accelerating *away* from VWAP
6. `first_pullback_only` (default `True`): no prior Setup-1 trigger since the session anchor
7. Trigger-bar volume ≥ `volume_confirmation_mult` (default 1.2×) of its rolling mean

**Stop:** `min(pullback trigger candle's low, VWAP − 1σ)` for BUY (max/high for SELL) — whichever is
further from entry — then the existing cost floors (`min_sl_pips`, `min_sl_spread_mult`) still apply.
**Target:** `VWAP + 2σ` (BUY) / `VWAP − 2σ` (SELL), using the σ values captured at trigger time.

---

## 4. Setup 2 — Band Reversion (mean reversion — new)

Entry conditions:

1. Close beyond `reversion_min_sigma` (default 2.0×σ) — "never fade inside 2σ"
2. VWAP slope flat: `|slope| ≤ reversion_max_vwap_slope_atr_pct × ATR` (default 10%)
3. Rejection wick against the extension ≥ `reversion_min_rejection_wick_pct` (default 50%) of the
   trigger candle's total range, when `reversion_requires_rejection` is `True`
4. Momentum does **not** confirm the extension direction, when `reversion_requires_trend_neutral` is
   `True` — "do not fade a strong trend"
5. Volume ≥ `volume_confirmation_mult` of its rolling mean

**Stop:** beyond `reversion_min_sigma`'s companion 3rd band (`VWAP ± 3σ` at trigger time), then cost
floors apply. **Target:** `VWAP` itself (fair value) — "the classic VWAP edge."

Note the close must be **beyond** the band threshold *and* still show a rejection wick — i.e. the
candle travelled even further intrabar before pulling back to a close that's still past the trigger
sigma, not a full round-trip back inside the band (that would be a Setup-1-shaped candle, not this
one).

---

## 5. Confluence score (replaces the hardcoded `80`)

| Component | Points | Basis |
|---|---|---|
| Mandatory chain | 40 | Awarded whenever a signal fires — every gate above already passed |
| VWAP distance | 0–15 | Setup 1: closer to VWAP scores higher. Setup 2: further beyond the reversion threshold scores higher |
| Trend agreement | 0–15 | VWAP slope + momentum sign vs. trade direction (no HTF market-structure detector is wired into this engine, so this is slope+momentum only, not the spec's full 3-input version) |
| Volume confirmation | 0–15 | Trigger-bar volume vs. its rolling mean, scaled |
| Session quality | 0–15 | 09:00–11:00 ET scores highest per the measured data cited in the Master Plan |

Range in practice: 40 (bare mandatory chain) → 100.

---

## 6. Target-mode exemption from the global R-grid

`target_mode` (`SIGMA_BAND` default, or `R_GRID` for v1 behaviour). Under `SIGMA_BAND`, the signal
declares `metadata.structural_tp` / `structural_tp_rr` / `tp_is_structural=True` — the **same pattern
CRT already uses** (`docs/CRT_Strategy_Spec.md`'s "Architectural discrepancy" note) — so a
`SIGMA_BAND`-mode VWAP signal's declared target survives even though `RiskParams`'s TP ladder
(`tp1_rr`/`tp2_rr`/`tp3_rr`) still owns the actual placed multi-TP legs. A +2σ or "return to VWAP"
target has no relationship to a fixed 1.5R/3R/5R grid; resolving that fully (exempting VWAP from the
grid the way CRT needs to be exempted too) is the same open product decision flagged in the CRT spec,
not made here.

---

## 7. Regression-checked, unchanged from v1

Per Master Plan task 8.7 — these three gates were correctly marked KEEP in the Part 2 filter register
and must not be lost in the rewrite. All three are structurally untouched in this pass (same code,
same call sites):

- `max_trades_per_day` (default 4)
- `max_losses_per_day` (default 2, tracked via `notify_outcome`)
- `_is_in_exclusion` (session window: `session_exclude_end` → `entry_cutoff`)

---

## 8. Verification

Verified with two synthetic end-to-end runs (not a full historical backtest — no live MT5 connection
available in this environment):

- **Setup 1:** a steady uptrend with one red, volume-elevated, VWAP-converging pullback bar correctly
  produced a `BUY PULLBACK_TO_VALUE` signal one bar later, with `stop_loss` below entry and
  `take_profit` above it.
- **Setup 2:** a flat/choppy session with one sharp upward spike (closing beyond +1.5σ, large upper
  rejection wick, volume-confirmed) correctly produced a `SELL BAND_REVERSION` signal one bar later,
  with `stop_loss` above entry (beyond +3σ) and `take_profit` at VWAP.

**You should run a real historical backtest across at least the instruments in `debug/vwap/` before
trusting these setups' win rate or expectancy on real data** — this verification confirms the
mechanics fire correctly, not that the strategy is profitable.
