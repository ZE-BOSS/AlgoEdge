# AlgoEdge — Order Flow & Institutional Positioning Edge: Framework & Implementation Plan

**Purpose**: This extends the "Parked — Fundamental Analysis" section (§7) of the prior `AlgoEdge-Audit-Implementation-Plan.md` into a full design. It does not replace that document — read them together. This one answers: *how do we legally read institutional/dealer positioning from public data, map it to AlgoEdge's actual tradeable instruments, and wire it into the existing backtest/live/portfolio stack without corrupting strategy signal logic or crossing into non-public information.*

**Not financial advice** — this is an engineering and research-design document. Every threshold, weight, and "edge" claim below traces back to either (a) peer-reviewed academic findings, (b) an unvalidated practitioner heuristic that must be calibrated on your own data before being trusted, or (c) is flagged as out of scope. Section 0 tells you which is which for every source used.

---

## 0. Source Material — What Each Document Actually Is

| Source | What it is | Confidence on *concepts* | Confidence on *specific numbers* |
|---|---|---|---|
| Sitaru, Calinescu & Cucuringu (2023) — Decomposed OFI | Real NASDAQ LOBSTER data, real regression R², real backtested Sharpe (up to 13.5) | High | High, but equity-LOB specific — no direct NQ/FX/synthetic transfer |
| Tóth, Palit, Lillo & Farmer (2014) — Order flow persistence | Real LSE broker-identified data, explicit null-hypothesis Monte Carlo testing | High | High, same transfer caveat as above |
| Chopra — *Institutional Order Flow Analytics* (SSRN, 2026) | Self-published practitioner working paper; real academic citations underpinning the *phenomenon*; case studies explicitly labeled hypothetical; author markets a paid curriculum built on this exact framework | Medium — the four data streams (options flow, dark pool, L2/tape, VWAP structure) are genuinely observable and genuinely informative per the cited academic literature | Low — 0.5 vol/OI ratio, 2.5× ATS spike, tier boundaries, and the 0.40/0.30/0.15/0.15 weights are the author's own heuristics, not backtested in the paper |
| Chopra — *Delta/Gamma on Expiration Dates* (SSRN, 2026) | Explicitly a "draft" research *proposal* — zero empirical results. Mechanics (delta/gamma math, hedge-ratio arithmetic) are standard and correct. | Medium — dealer gamma hedging is a real, well-documented phenomenon (this is the same mechanism your prior audit's §7 already flagged as legitimate) | N/A — no specific implementation is given; the paper proposes a "Dealer Hedging Pressure Index" as a concept only |

**Rule for everything below**: treat every Chopra-derived threshold as a config value with a default, not a hardcoded constant. The build order in §10 puts "calibrate against AlgoEdge's own history" before "trade live off it."

**Legal boundary (carried forward from the prior audit, unchanged)**: everything here uses (a) public/purchasable market data about what already happened, and (b) public *scheduling* information about when CPI/NFP/FOMC will be released. Nothing here uses or seeks advance knowledge of an embargoed number before its official release.

---

## 1. The Real Mechanism: How Options-Dealer Hedging Transmits Into Price

This is the one piece of "fundamental" edge in these papers that has genuine academic backing (Kyle 1985, Glosten-Milgrom 1985, Pan & Poteshman 2006), and it's what your prior audit already greenlit. Stated plainly:

1. A customer buys/sells options. The market maker takes the other side and now holds directional risk (delta).
2. To stay roughly delta-neutral, the dealer buys or sells the underlying (or a correlated proxy — futures, ETF shares) in proportion to that delta.
3. As price moves, delta changes at a rate set by **gamma**. Gamma is largest for near-the-money, near-expiration options — which is why 0DTE and weekly-options days concentrate this effect.
4. **Positive dealer gamma** (dealers net long options) → they sell into rallies, buy into dips → dampens volatility, can produce "pinning" near heavy-OI strikes.
5. **Negative dealer gamma** (dealers net short options) → they buy into rallies, sell into dips → amplifies volatility and trend continuation.

Two honesty points that must survive into the implementation, not just this document:
- **Dealer positioning is never directly observable** from public data. Every "GEX" (gamma exposure) number anyone computes — including SpotGamma/SqueezeMetrics-style commercial products — is an *estimate* built on an assumption (usually: assume dealers are short the calls and puts retail/institutional customers bought). It's a well-established estimation convention, not ground truth.
- Paper 2 itself is explicit that this is a **conditional** transmission mechanism — earnings, macro news, index rebalancing, and ETF flows can dominate it on any given day. It should enter AlgoEdge as a *regime feature/filter*, not a standalone strategy.

---

## 2. Asset-Class Transmission Map

This is the most important corrective to the request as framed: **the options→dealer-hedging→price mechanism does not apply equally (or at all) across your current instrument set.**

| AlgoEdge instrument | Options market that hedges into it | Public data source | Signal quality |
|---|---|---|---|
| NQ / MNQ futures | QQQ options, NDX options, ES/SPX options (correlated tech-heavy proxies) | CME futures COT report; OPRA-based flow vendors; CBOE data | **Good** — this is the cleanest fit for the whole framework |
| XAU/USD, XAG/USD | COMEX GC/SI futures options; GLD/SLV ETF options | CME COT report; OCC open interest | **Good** |
| BTC/USD, ETH/USD | Deribit options (dominant global venue by volume/OI); CME BTC/ETH options | Deribit's public API (OI, IV, greeks, max pain — free); Coinglass (OI/liquidation heatmaps) | **Good** — arguably the *best* real-time public data of any asset class here |
| Forex majors (EUR/USD, GBP/USD, etc.) | CME FX futures options (6E, 6B, 6J, 6A, 6C, 6S) | CFTC Commitments of Traders (weekly, ~3-day lag) | **Weak/partial** — there is no OPRA-equivalent consolidated FX options tape and no FINRA-ATS equivalent for the OTC FX market. Retail CFD/spot FX dealer positioning is structurally opaque. Downgrade this to a low-weight regime input (COT net-positioning extremes), not a tick-level signal. |
| **Deriv synthetic indices** (Volatility 10/25/50/75/100, Crash/Boom, Jump, Step) | **None** | **None exists** | **Out of scope — see below** |

### 2.1 Why synthetics are a hard stop for this entire framework

Deriv's synthetic indices are generated by Deriv's own algorithm (a continuous random-walk-style process with a fixed, disclosed volatility parameter) precisely *so that* there is no external market to reference. There is no underlying company, no futures contract, no options chain, no dealer inventory, no institutional order flow to observe — by design. Any "order flow" or "dealer positioning" signal you computed and applied to a synthetic index would be pure noise dressed up as an edge.

**Action**: strategies trading Deriv synthetics must stay purely technical/statistical (price action, the synthetic's own historical volatility/statistical properties). Do not wire the order-flow/conviction-score module (§7) into synthetic-index strategies at all — not even at zero weight, because that invites someone later "just turning it on."

---

## 3. Data Source Inventory

| Data | Source | Cost | Latency | Maps to |
|---|---|---|---|---|
| Options flow (sweeps, UOA, tier classification) | Commercial vendors: Unusual Whales, Cheddar Flow, Market Chameleon, ORATS, CBOE LiveVol | Paid, tiered | Near-real-time | Chopra Layer 1 |
| Raw options chain (strike, OI, IV) for self-computed GEX | Polygon.io options endpoint, CBOE DataShop, OptionMetrics (academic/institutional) | Paid | Delayed to real-time depending on tier | Chopra Layer 1 + Paper 2's GEX concept |
| FINRA OTC/ATS weekly volume by venue | FINRA/Morningstar ATS Transparency data | Free | Weekly, ~1-week lag | Chopra Layer 2 |
| Futures positioning (large spec/commercial net) | CFTC Commitments of Traders report — covers NQ/MNQ (via equity index futures), GC/SI, all 6 major FX futures, and now BTC/ETH futures | Free | Weekly, ~3-day lag | Cross-asset regime input |
| Crypto options OI/IV/greeks/max pain | Deribit public API | Free | Real-time | Crypto Layer 1 equivalent |
| Crypto OI/liquidation heatmaps across exchanges | Coinglass | Free tier + paid | Real-time | Crypto tape-reading equivalent |
| Tick/L1 data for CVD & footprint charts | Whatever futures/FX data feed AlgoEdge already has (MT5 tick data if available, or the broker/vendor feed) | Existing | Real-time | Layer 3 equivalent — see §4 |
| CPI/NFP/PCE release **schedule** (timing only) | Bureau of Labor Statistics published release calendar; FRED/ALFRED release calendar | Free | Published months ahead | News/event module (§5) |
| FOMC meeting **schedule** (timing only) | Federal Reserve's published meeting calendar | Free | Published a year ahead | News/event module (§5) |
| Historical *actual* release timestamps (for backtesting the news filter) | TradingEconomics/Econoday historical calendar APIs (paid), or FRED's ALFRED vintage data (free, covers actual-value history) | Free–paid | Historical | News/event backtest data |

---

## 4. Real-Time Order-Flow Visualization ("where is this one placing their order")

Be precise about what's buildable without a full CME MDP3.0 feed (expensive, complex):

- **Cumulative Volume Delta (CVD) / footprint bars**: buildable from tick or 1-second trade data you likely already receive — bucket trades by whether they hit bid or ask, running total. This does *not* require full Level 2 depth, only trade-side classification, and is the actual mechanism behind most retail "order flow" tools (Bookmap, ATAS, Sierra Chart footprint charts).
- **Large-lot/block detection**: filter the same tick stream for prints above a size threshold (calibrate per-instrument; don't hardcode).
- **Options flow blotter**: a live feed of incoming sweeps, tagged Tier 1/2/3 per §7's configurable classifier.
- **GEX-by-strike chart** with a marked "zero-gamma / flip level" — the single most commonly requested version of this visualization, and genuinely computable from options-chain OI + estimated greeks (Paper 2's methodology, §6.5 of that paper).
- **Dark pool volume overlay**: weekly ATS ratio-to-average, plotted against price.

**Architecture**: ingestion services → normalized event stream over Redis pub/sub (reuse the event-bus pattern already recommended in the prior audit's §6) → WebSocket to frontend → chart components. This keeps the visualization layer decoupled from the strategy/execution layer, same separation-of-concerns principle as the rest of the system.

---

## 5. News/Event Trading Toggle — Correct Implementation

**Config schema** (per-strategy or global override):

```yaml
news_filter:
  enabled: true
  high_impact_events: [CPI, FOMC, NFP, PCE]
  blackout_before_minutes: 15
  blackout_after_minutes: 45
  gate_on: scheduled_time   # never actual_release_time — see note below
```

**Critical correctness note**: the blackout window must be computed against the *scheduled* release time (known in advance, public), never against the *actual* release time. In live trading you obviously don't know the actual release happened until it happens — gating on scheduled time is both the only thing that's causally possible and the only thing that's unambiguously legal.

**New DB table**:
```
economic_events(
  event_id, name, scheduled_time, actual_release_time,
  impact_level, actual_value, forecast_value, previous_value
)
```

**For backtesting**: you need a *historical* calendar with real past scheduled/actual timestamps, not just today's forward calendar — this is a separate data need from the live feed (see §3's row on historical calendar sources).

**Direct fix-forward from the prior audit**: this module resolves §2.3 ("News Filter — Wrong Endpoint") and should be built through the same shadow-replay harness recommended in §3.3 of that document, so live and backtest apply the identical blackout logic against the identical event source — no drift between the two.

**Toggle behavior**: `enabled: false` → strategy trades through scheduled events with no blackout at all. `enabled: true` → no new entries (and optionally: tighten/close existing positions) inside the blackout window, in both backtest and live, sourced from the same table.

---

## 6. Portfolio Correlation & Session-Timing Framework

- **Rolling correlation matrix** (20-day and 60-day Pearson on returns) across NQ/MNQ, XAU/XAG, BTC/ETH, FX majors. **Explicitly exclude Deriv synthetics from this matrix** — any correlation you'd measure against a synthetically-generated random process is noise, not signal, and including it risks a false sense of diversification.
- **Session-liquidity map** (standard, not proprietary): London open (~3am–5am ET) drives EUR/GBP and gold liquidity; NY open (9:30am ET) drives NQ/MNQ and USD pairs; Asia session drives JPY pairs and, to a lesser extent, crypto. Use this to gate which pairs a strategy is even allowed to consider at a given hour (this already exists as `allowed_sessions` in the prior audit's §4 config sketch — extend it, don't duplicate it).
- **Event-aware pair selection**: cross-reference `economic_events` against each instrument's actual sensitivity — CPI/FOMC move USD pairs, NQ, and gold hard; BTC moves on the same events but with more idiosyncratic noise; a JPY pair cares more about BOJ than the Fed.
- **Correlation-aware exposure cap**: this is the direct extension of the prior audit's §4 correlation gate — but now applied *across the order-flow conviction score too*. A Tier-1 bullish options signal on QQQ, a positive-GEX regime read on NQ, and a long BTC position are correlated "risk-on tech" exposure even though they're three different instruments and three different signal types. The portfolio gate needs to see them as one cluster, not three independent bets.

---

## 7. Making the Conviction Score Actually Testable

Reproduce Chopra's formula as a *configurable*, not hardcoded, component:

```
C = w1*S1 + w2*S2 + w3*S3 + w4*S4
```
where S1–S4 are binary or continuous confirmation signals from options flow, dark pool/COT, tape/CVD, and VWAP-structure alignment respectively.

**Do not ship with the paper's suggested weights (0.40/0.30/0.15/0.15) or threshold (0.70) as fixed values.** Instead:

1. Expose w1–w4 and the action threshold as parameters in the strategy config (same pattern as the existing `risk_per_trade_pct`, `max_trades_per_day` fields).
2. Build the same kind of calibration sandbox already recommended in the prior audit's §4 (Monte Carlo sizing-policy testing) — here, grid-search or walk-forward the weights/threshold against AlgoEdge's own historical order-flow-tagged trades, and compare resulting Sharpe/drawdown distributions.
3. Only promote a calibrated weight set to a live default after it's survived out-of-sample testing on your own data — this is exactly the IS/OS discipline the Sitaru paper in your other summary used, and exactly what's missing from Chopra's paper.

This is also where the "visualize and adjust parameters, see the effect on backtest/live" request plugs in directly: a frontend parameter panel over w1–w4 and threshold, wired to a live-recalculating equity curve — same UX pattern as the graphical strategy-preview tool already scoped in the prior audit's §5.

---

## 8. Architecture Additions (concrete, matching the existing stack)

**New backend modules** (Python, alongside `backtest.py`/`engine.py`/`portfolio_engine.py`):
- `options_flow.py` — ingest vendor flow data, apply Tier 1/2/3 classifier
- `dealer_gamma.py` — compute estimated GEX from options-chain OI + greeks (Paper 2's methodology)
- `dark_pool.py` — ingest/parse FINRA ATS weekly data, compute relative-concentration ratio
- `economic_calendar.py` — event scheduling service (§5)
- `correlation_engine.py` — rolling correlation matrix + portfolio exposure clustering (§6)
- `order_flow_conviction.py` — combines S1–S4 into C, config-driven weights (§7)

**New DB tables**: `options_flow_signals`, `dealer_gamma_snapshots`, `dark_pool_prints`, `economic_events`, `correlation_snapshots`

**New frontend components** (React, alongside `Backtester.jsx`):
- `OptionsFlowBlotter.jsx`, `GammaExposureChart.jsx` (with zero-gamma flip marker), `DarkPoolVolumeChart.jsx`, `EventCalendarPanel.jsx` (with the enable/disable toggle from §5), `CorrelationHeatmap.jsx`, `ConvictionScorePanel.jsx` (sliders for w1–w4 + threshold, §7)

**Integration rule**: attach an `order_flow_context` object per-bar/per-signal that strategies can *read* as a filter or conviction booster. Keep this additive and orthogonal to existing strategy signal logic — same rule the prior audit already stated for infrastructure fixes (§ "Rules for whoever executes this plan," item 4): don't let this module quietly change what a strategy's core entry logic does.

**Authoring path**: same offline spec → coding-agent implementation → validation-gate pipeline already established in the prior audit's §5. Write the order-flow module specs the same way the three existing strategy specs (HTF FVG Flip, Bias/Key-Level/IFVG, NY Open Break-Retest) were written, and keep any LLM involvement at authoring time only — zero LLM calls in the live per-candle path.

---

## 9. Explicit Non-Goals

- Do **not** apply any part of this framework to Deriv synthetic indices (§2.1).
- Do **not** treat retail FX flow with the same confidence as equity/index options flow — there's no comparable public data infrastructure (§2, §3).
- Do **not** treat Chopra's specific thresholds/weights as pre-validated — every one is a starting parameter (§0, §7).
- Do **not** gate the news filter on actual release values or timing — scheduled-time-only, always (§5).
- Do **not** put an LLM call in the live signal-generation path (carried forward from the prior audit's §5).

---

## 10. Suggested Build Order

1. Economic calendar service + news toggle (§5) — smallest scope, fixes a known existing bug, unblocks safe backtesting of everything else.
2. Self-computed GEX from options-chain data (§1, §3) — highest-quality signal, cleanest instrument fit (NQ/MNQ), most academically grounded.
3. Dark pool ratio ingestion (§3) — free data, weekly cadence, low engineering cost.
4. Correlation engine + portfolio exposure clustering (§6) — needed before you stack multiple order-flow-informed strategies live.
5. Conviction-score module with configurable weights + calibration sandbox (§7) — do not go live with default weights.
6. Real-time visualization layer (§4) — last, because it's the most expensive (paid vendor data) and least load-bearing for actual PnL; build it once the underlying signals are already calibrated and worth watching.
