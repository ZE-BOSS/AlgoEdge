10 — The never-tested cells, and the first real gate telemetry
==============================================================

**Stage H.2 (partial)** · 2026-08-30 · script `research/data/run_missing_cells.py`

Three cells were in the plan's asset list and never executed. This runs them
against real MT5 bars through the real strategy engine and the real
`BacktestEngine`, with the gate recorder switched on — so it also serves as the
end-to-end proof of the **B7** fix.

**Read the caveats before the numbers.** This run is *not* directly comparable
to the 116-run sweep:

| | the 116-run sweep | this run |
|---|---|---|
| window | 2026-01-01 → 2026-08-27 (~8 months) | **2026-06-22 → 2026-08-30 (~10 weeks)** |
| bars | 5,000 | 20,000 M5 |
| cost source | live MT5 per symbol | **`ASSET_CLASS_DEFAULT`** (spread 2.0p) — MT5 was not connected to this process |

The Crash 1000 control is included precisely so the three can be read against
each other rather than against the old sweep.

---

## 1. Results

| cell | signals | trades | P&L | verdict |
|---|---:|---:|---:|---|
| DriftJumpAlpha × **Crash 1000** *(control)* | 406 | 71 | **+$417.00** | works, as expected |
| DriftJumpAlpha × **Crash 500** | 406 | 69 | **−$696.34** | **loses** |
| DriftJumpAlpha × **Jump 100** | 0 | 0 | $0.00 | **cannot run** — see §3 |

### This overturns a recommendation I made

Report 08 called Crash 500 *"the strongest untested candidate in the book"* and
*"the single most promising untested thing you have"*, on the grounds that it
returns **+0.303 R on random entry** and sits between Crash 300 and Crash 1000 on
every measured property.

The geometry claim was right. The recommendation was wrong. **DriftJumpAlpha on
Crash 500 loses $696 over the same window in which it makes $417 on Crash 1000.**

That is not a contradiction — it is the sharpest confirmation yet of report 08's
other finding: *the strategy's entry logic subtracts from the geometric edge.*
Crash 500 has the better raw geometry of the two and the worse strategy result.

**Corrected recommendation:** do not add Crash 500 to DriftJumpAlpha as it
stands. The instrument is worth trading; this strategy is not the way to trade
it. Test the raw geometry rule (buy, 0.5×ATR stop, 5R target) against it
instead — that is what measured +0.303 R.

---

## 2. The first real gate telemetry (B7 verified end-to-end)

`strategy_rejections` was `{}` in all 116 saved runs. It is now populated. From
19,700 evaluated candidates on Crash 1000:

| gate | candidates blocked | share of all evaluated |
|---|---:|---:|
| **`daily_trade_cap`** | **15,263** | **77.5%** |
| `drift_regime_active` | 2,711 | 13.8% |
| `pullback_within_atr` | 533 | 2.7% |
| `jump_cooldown` | 211 | 1.1% |
| `min_rrr_drift` | 75 | 0.4% |
| `post_jump_ema_reclaim` | 26 | 0.1% |

Crash 500 is near-identical (`daily_trade_cap` 15,247, `drift_regime_active`
2,985).

**The headline is that a risk control, not a confluence, does 77% of the
filtering.** `daily_trade_cap` discards more than three quarters of everything
DriftJumpAlpha ever considers. Every technical gate combined accounts for under
18%.

Two things follow:

1. **The cap is the single most consequential parameter in this strategy** and it
   has never been measured. It is currently 7 trades/day. Whether 7 is right —
   or whether it is quietly throwing away the strategy's best setups — is now a
   one-parameter sweep, and it should be the first thing the re-run tests.
2. **Ablating the technical gates will barely move the result** while the cap
   dominates. §0.5-4 of the plan says a gate that never blocks cannot change an
   outcome; the corollary is that gates blocking 0.1–2.7% can hardly change one
   either. Sweep the cap first, then ablate.

---

## 3. Jump 100 cannot be run by this strategy

`crash_symbol_only` blocked **19,700 of 19,700** candidates. DriftJumpAlpha is
hard-gated to Crash instruments by design.

So report 08's suggestion to test Jump 100 with DriftJumpAlpha was not
executable without a code change — I should have checked the gate before
proposing it. Jump 100's measured **+0.045 R** on random SELL entry stands, but
capturing it needs either a new strategy or an explicit widening of that gate,
and Jump 100 is a two-sided jump process rather than drift-plus-crash, so the
DriftJumpAlpha logic is unlikely to transfer.

---

## 4. One thing that looked like a bug and is not

The run log shows `BOS fired` up to **38 times on an identical broken level**
(2,795 events across 370 distinct prices). That looks like a detector re-arming
on the same break.

It is not. `market_structure.py:149` dedupes by **bar index**, not price, and the
code comments the reason: two genuinely distinct swings can share a price by
coincidence — which is common on a synthetic index that revisits levels. The
observed ratio is consistent with distinct swings.

Recorded here so the same false alarm is not raised again.
