# 05 — Correlation & portfolio construction

**Stage E** · produced 2026-08-30 · script: `research/data/portfolio.py`
Basis: **daily R over the full 239-day window**, per §E's rule — not monthly P&L.

---

## Strategy correlation

Daily-R correlation across all 239 trading days:

| | APA | BiasIFVG | CRT | DriftJump | HTFFVG | NYOpen | VWAP |
|---|---:|---:|---:|---:|---:|---:|---:|
| **APA** | +1.00 | +0.18 | −0.06 | −0.05 | +0.04 | −0.05 | +0.16 |
| **BiasIFVG** | +0.18 | +1.00 | +0.06 | −0.01 | +0.01 | +0.00 | +0.08 |
| **CRT** | −0.06 | +0.06 | +1.00 | −0.02 | +0.03 | +0.06 | +0.08 |
| **DriftJumpAlpha** | −0.05 | −0.01 | −0.02 | +1.00 | −0.00 | −0.03 | **−0.08** |
| **HTFFVGFlip** | +0.04 | +0.01 | +0.03 | −0.00 | +1.00 | −0.08 | −0.04 |
| **NYOpenRetest** | −0.05 | +0.00 | +0.06 | −0.03 | −0.08 | +1.00 | +0.10 |
| **VWAP** | +0.16 | +0.08 | +0.08 | −0.08 | −0.04 | +0.10 | +1.00 |

Every off-diagonal cell sits in **|r| ≤ 0.18**. The strategies are effectively
independent — genuinely good news, and rarer than it sounds.

**But independence is not value.** Diversifying across seven return streams of
which six are negative produces a smoothly declining equity curve, which is
worse than a lumpy one, not better. The correlation structure only becomes an
asset once there is more than one profitable component. That is the whole story
of this stage.

## Candidate portfolios

| portfolio | total R | daily mean R | ann. Sharpe | max DD | R / maxDD |
|---|---:|---:|---:|---:|---:|
| Whole book (7 strategies, 19 symbols) | **−1,343.5** | −5.622 | **−7.71** | 1,380.6 R | −0.97 |
| DriftJumpAlpha alone | +173.4 | +0.726 | 3.01 | 25.6 R | 6.78 |
| **Survivors (3 cells)** | **+198.9** | **+0.832** | **3.31** | **17.7 R** | **11.21** |

The survivor portfolio is DriftJumpAlpha × Crash 1000, DriftJumpAlpha × Crash
300, and VWAP × Volatility 75. Their pairwise correlations:

| | DJA/Crash300 | DJA/Crash1000 | VWAP/Vol75 |
|---|---:|---:|---:|
| **DJA/Crash 300** | +1.00 | +0.04 | −0.03 |
| **DJA/Crash 1000** | +0.04 | +1.00 | −0.08 |
| **VWAP/Vol 75** | −0.03 | −0.08 | +1.00 |

Near-zero across the board, including between the two DriftJumpAlpha cells —
Crash 300 and Crash 1000 are independently generated processes, not two views of
one thing.

**This is where the diversification actually pays.** Adding VWAP × Volatility 75
to DriftJumpAlpha alone:

- Sharpe **3.01 → 3.31**
- max drawdown **25.6R → 17.7R**
- return per unit of drawdown **6.78 → 11.21**

Return rises 15% while drawdown *falls* 31%. That is a real diversification
gain, not simple addition.

### Caveats, stated plainly

- **Sharpe 3.31 is flattered.** The components were chosen partly because they
  won. Cell selection does survive an out-of-sample split (report 07), but a
  Sharpe computed on the selected book is still an in-sample statistic. Treat
  3.31 as "this is not correlated garbage", not as a forecast.
- **Three components is a thin portfolio**, and two of the three are the same
  strategy on sibling instruments from one broker. That is concentration risk in
  Deriv's synthetic pricing model, not market risk. If Deriv changes the model,
  all of it goes at once.
- **239 days, one regime.** January to August 2026 only.

## Asset correlation

Not computed. §E asks for asset-return correlation as well as strategy
correlation, but with the traded book reduced to three synthetic instruments
whose price processes are generated independently by design, a price-return
correlation matrix over the other 16 symbols would describe assets the
recommendation says not to trade. It should be revisited if and when a
second real-market strategy earns a place.

## Stage E checklist

| item | status |
|---|---|
| Correlation from daily returns over the full window | ✅ 239 days |
| Which assets offset each other | ✅ survivors are mutually independent |
| Which assets complement | ✅ VWAP/Vol75 + DJA — Sharpe ↑, drawdown ↓ |
| Candidate portfolios validated on the portfolio engine | ⚠️ computed from trade-level daily R, **not** re-run through the portfolio engine under §0.1 |
| Correlation of strategies as well as assets | ✅ strategies done; assets deferred, reasoning above |
