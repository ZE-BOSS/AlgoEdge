# AlgoEdge — Phase 13: Visual Intelligence Plan

**Date:** 2026-08-23
**Supersedes for UI scope:** `MASTER-IMPLEMENTATION-PLAN.md` Part 5 (which narrowed the UI
work down to a parameter renderer and dropped the replay engine entirely)
**Reinstates:** `AlgoEdge-Visualization-Portfolio-Architecture-Plan.md` §4 (replay engine)
and §5 (fundamentals UI suite), both of which Part 6 of the master plan marked
"Not started — keep" and then never scheduled into any phase.

---

## PART A — Audit: what the master plan actually shipped

Verified against the working tree on 2026-08-23, not against the checkbox states.

### A.1 Headline numbers

`TASKS.md` carries 180 checkboxes: **132 checked, 48 unchecked.** Of the 48 open:

| Bucket | Count | Real status |
|---|---|---|
| Descoped / withdrawn after re-verification | 6 | `1.1`–`1.4`, `Rule-3`, `Doc-8` — closed, not pending |
| Blocked on a live MT5 connection | 7 | `0.6`, `1.15`, `1.16`, `1.17`, `2.25`, `4.9`, `Rule-6` |
| Blocked on a decision you owe | 2 | `D-5` (Bias-IFVG direction), `1.14` (GER40 contract spec) |
| Deliberately deferred with a stated reason | 6 | `1.8`, `1.10`, `1.18`, `3.3`, `3.12`, `3.14`/`3.15` |
| **Frontend work never started** | **22** | `3.2`, `3.6`–`3.9`, `3.13`, `7.2`–`7.7`, `7.9`–`7.17`, `12.11`, `12.12` |
| Backend wiring left open | 2 | `10.5` (order flow → MarketContext), `Doc-6` |

**The finding that matters: every backend phase (0–6, 8–10, 12) landed. The entire
frontend half — Phase 7 in full, plus the frontend halves of Phase 3 and Phase 12 —
was never built.** 22 of the 48 open items are one body of work: the UI.

That is also why the replay chart went missing. Part 6 of the master plan disposed of
`AlgoEdge-Visualization-Portfolio-Architecture-Plan.md` with the line *"its §4 (replay
engine) and §5 (fundamentals UI) are superseded in scope by Part 5 here"* — but Part 5
is a **parameter-display** spec (schema-driven form renderer, diagnostic panels). It does
not contain a chart, a replay, a WebSocket bar feed, or a symbol tab. §4 was not
superseded; it was dropped. Reinstated below as Part C.

### A.2 Defects found during this audit that are on no task list

These are new. All four were found by reading the code, not the plan.

| # | Defect | Evidence | Severity |
|---|---|---|---|
| **V1** | **`smc_data` is always empty.** `trade_grouper.py:316` reads `signal.metadata["markings"]` to build the chart's FVG/OB boxes and structure markers. `grep -rn "markings" backend/ --include="*.py"` returns **three hits, all inside `trade_grouper.py` itself.** No strategy has ever written that key. Every backtest chart therefore renders entry/SL/TP lines and nothing else — no zones, no key levels, no confluences. | `backend/utils/trade_grouper.py:316-318` vs. zero producers | 🔴 This is precisely the "see how the strategy was used on chart" gap |
| **V2** | **Backend logs never reach the frontend.** `utils/logger.py` registers three loguru sinks: stderr, `logs/backend.log`, `logs/trades.log`. None of them is a WebSocket sink. The only thing the Live Logs panel ever receives is what `bot_service.log_system_event()` explicitly broadcasts — a few dozen call sites out of thousands of log lines. | `backend/utils/logger.py` (no WS sink), `Backtester.jsx::LiveLogPanel` | 🔴 Matches your "logs are on the terminal, not the frontend" |
| **V3** | **No log retrieval endpoint at all.** `logs/backend.log` rotates at 10 MB with 30-day retention and is never exposed over HTTP. There is no way to pull a past session's logs from the platform. | `ls backend/api/routes/` — no `logs.py` | 🟠 |
| **V4** | **Chart data is per-trade only, capped at 500 bars.** `trade_grouper._extract_chart_data` slices ±30 bars around each trade and hard-caps at 500. There is no continuous series for a symbol across the run, so a "scroll through the whole window and see every trade" view is not constructible from the current response shape. | `backend/utils/trade_grouper.py:292-294` | 🟠 Blocks Part C directly |

### A.3 Architectural constraint that shapes the replay design

The backtest is **two-phase**, and this is not obvious from the plan:

1. **Phase 1 — signal generation.** `backtest.py:578-620` (single) / `1146-1230` (portfolio)
   walks bars one at a time calling `strategy_engine.on_bar()`, collecting `TradeSignal`s.
   Progress 10%→90% comes from here.
2. **Phase 2 — simulation.** `run_backtest` / `PortfolioBacktestEngine.run` executes in
   `asyncio.to_thread`, consuming the whole signal list at once and producing trades.

**Trades do not exist during Phase 1.** Any design that promises to stream `trade_opened`
events live during the run is wrong about how this engine works. What *is* available live
during Phase 1 is: the current bar, and every signal the moment it is detected.

The design in Part C is built on that reality rather than around it — Phase 1 streams a
sliding bar window with signal markers as they fire, and the same chart flips into a full
trade replay the moment Phase 2 returns. Two modes, one component, no fiction.

---

## PART B — Closing the open backend items

Ordered by whether they can be closed without you.

### B.1 Closable now
- **`10.5`** — wire `data/orderflow.py`'s CVD/OFI primitives into `services/market_context.py`.
  Gated behind a config flag so the tick fetch is opt-in, since it is network-bound.
- **`Doc-6`** — regenerate once the code change it describes lands.

### B.2 Needs your input (blocking, stated plainly)
- **`D-5`** Bias-IFVG: does the IFVG form on the approach leg to the key level, or only
  after price taps it? Different entries, different backtests.
- **`1.14`** GER40 contract spec from your broker (`symbol_info("GER40")`).
- **`1.15`/`1.16`** XRPUSD lot constraints — needs real `symbol_info("XRPUSD")` output.

### B.3 Needs a live MT5 session (yours to run, not mine)
`0.6`, `2.25`, `4.9`, `Rule-6`. Each is a validation pass, not a build.

---

## PART C — The replay chart (reinstated §4, fully specified)

### C.1 What it replaces

`Backtester.jsx:2033-2049` currently renders four pulsing grey rectangles while a run is in
flight. That block is deleted and replaced by `<BacktestReplay>`.

### C.2 Two modes, one component

| | **Live mode** (during the run) | **Replay mode** (after) |
|---|---|---|
| Source | WebSocket `replay_*` events | The finished result object |
| Bars | Streamed in batches, window slides right | Full series, scrubbable |
| Overlays | Signal markers as detected | Full trade geometry + confluences |
| Control | Auto-advance only | Play / pause / speed / seek bar |
| Portfolio | Auto-switches tab as each symbol simulates | Free tab switching |

### C.3 Event protocol

New WS message types, all namespaced `replay_`. Emitted from the Phase-1 loop.

```
replay_init      { run_id, mode: "single"|"portfolio", legs: [{slot_id, symbol, strategy_id, timeframe}] }
replay_leg_start { run_id, slot_id }
replay_bars      { run_id, slot_id, bars: [{time,open,high,low,close}], cursor, total }
replay_signal    { run_id, slot_id, time, direction, entry, sl, tp, confluence_score,
                   signal_type, markings: [...] }
replay_leg_done  { run_id, slot_id, signal_count }
replay_done      { run_id }
```

**Batching.** One `replay_bars` message per 400 processed bars carrying the last 400 bars,
throttled to a floor of 120 ms between sends per leg. A fast run emits far more bars than a
screen can show; the throttle is what keeps the socket from becoming the bottleneck.

**Backpressure.** `ConnectionManager` gains a per-user send queue with a 32-message cap and
drop-oldest semantics for `replay_bars` only (never for `replay_signal` — signals are the
information, bars are the scenery). A dropped bar batch is recoverable because each batch
carries `cursor`, so the client can detect a gap and request a resync.

### C.4 Rendering

`lightweight-charts@5.2.0` is already a dependency and `components/TradeChart.jsx` already
uses it, including a canvas `RectanglePrimitive` for zone geometry. The replay chart extends
that foundation rather than starting over:

- **Sliding window.** Keep `setVisibleLogicalRange` pinned to the last N bars (default 180)
  while in live mode, so the window slides as bars arrive. Any user scroll or zoom detaches
  the pin and shows a "follow" button to re-attach — same interaction as TradingView.
- **Batched paint.** WS messages accumulate into a ref; a single `requestAnimationFrame`
  loop drains the buffer and calls `series.update()`. Never one paint per message.
- **Marker budget.** Markers are capped at 300 in view; below a zoom threshold, confluence
  markers collapse to a single density glyph per cluster.
- **Off-thread geometry.** Zone/marking geometry resolution moves to a Web Worker only if
  profiling shows it is needed — not preemptively.

### C.5 Portfolio tabs

One tab per **slot**, not per symbol — the portfolio engine is already slot-keyed
(`slot_key` at `backtest.py:1225`), and Phase 12 explicitly allows the same symbol under two
strategies. A tab therefore reads `GBPJPY · VWAP_v1`, and `GBPJPY · APA_v1` is a second,
separate tab. Labelling them by symbol alone would merge two independent legs into one
chart, which is exactly the aliasing the visualization plan's "independent legs" principle
forbids.

Auto-advance: the active tab follows `replay_leg_start` during a run. Manual selection
pins the tab and stops the auto-follow for the rest of the run.

### C.6 Confluence rendering — fixing V1

This is the part that makes the chart answer "was the strategy implemented correctly".

**New module `backend/strategies/core/markings.py`** — one vocabulary every strategy emits:

```python
@dataclass
class Marking:
    kind: str      # "FVG" | "OB" | "LIQUIDITY" | "LEVEL" | "STRUCTURE" | "ZONE" | "NOTE"
    label: str     # "H4 FVG", "BOS", "Asia High", "OTE 0.705"
    timeframe: str
    start_time: int
    end_time: int | None   # None = extend to the right edge
    top: float | None      # box geometry
    bottom: float | None
    price: float | None    # line/point geometry
    role: str      # "confluence" | "trigger" | "invalidation" | "context"
    detail: dict   # the actual parameter values behind it, shown on hover
```

Each strategy's `on_bar` accumulates `Marking`s for the conditions it *actually tested*, and
attaches them as `metadata["markings"]` on the returned `TradeSignal`. `trade_grouper.py:317`
already consumes that key — no change needed there beyond widening the `kind` filter.

Per-strategy marking sets:

| Strategy | Markings to emit |
|---|---|
| APA | Head/shoulder pivots, neckline, BOS candle, retest zone, invalidation level |
| VWAP | VWAP line, ±1σ/±2σ bands, pullback candle, session anchor, structural TP band |
| CRT | Range high/low, sweep candle, close-back-inside candle, HTF bias source |
| HTF FVG Flip | HTF FVG box, flip candle, displacement leg, entry FVG |
| Bias IFVG | Key level, IFVG box, bias source candle, tap point |
| NY Open Retest | Session open line, break candle, retest zone, ORB box |

`role` is what lets the UI show, at a glance, which confluences were **required** (trigger),
which were **supporting** (confluence), and which were **context**. Hovering any marking
shows `detail` — the Fib level, the ATR multiple, the actual measured displacement.

**Discipline:** markings are emitted by the same code path that made the decision, at the
moment it made it. They are never recomputed at render time. A marking that disagrees with
the trade is a bug in the strategy, and that is the whole point of showing them.

### C.7 Continuous chart data — fixing V4

Add `replay_series` storage per leg: a downsampled continuous series for the whole run
window (target ≤ 6,000 bars, downsampled by time bucket if the raw window exceeds it),
stored alongside the run. This is what makes "scroll through the whole duration and see
every trade" possible. Per-trade `chart_data` stays as-is for the zoomed-in trade view.

### C.8 Order flow bubbles

`data/orderflow.py` already computes CVD/OFI from `copy_ticks_range`. Rendered as a
`ScaledBubblePrimitive` on the price pane: radius proportional to signed volume magnitude,
colour by sign, positioned at the tick's price level. Off by default, toggled per chart —
bubble density at M1 over a multi-month window will not render usefully and should not be
the default.

---

## PART D — Missing UIs (Phase 10's never-built half)

### D.1 Strategy Lab (`/strategy-lab`)
Create → preview → optimize → promote, in one screen.
- **Create**: schema-driven parameter form fed by `core/schema_introspection.py` (already
  built, task 7.1 — this is the consumer the backend has been waiting for).
- **Preview**: run the strategy over a chosen window and render *signals only* on the
  replay chart, no full backtest. The "does it fire where I'd expect" check from
  `AlgoEdge-Audit-Implementation-Plan.md:198`.
- **Optimize**: single-symbol parameter sweep; results as a sortable grid + a parallel-
  coordinates plot; select a row to load its config.
- **Promote**: "Send to Backtest" / "Promote to Live", carrying the config object from
  §3.4 of the visualization plan unmodified.

### D.2 Fundamentals (`/fundamentals`)
Panels: Order Flow (CVD/OFI + bubbles), Options (OI/GEX by strike), Correlation matrix,
Economic calendar. Each panel declares which provider it needs, and greys out with a
"connect a provider" prompt when none is configured.

### D.3 Analysis (`/analysis`)
The Claude-powered analysis surface — Part F.

### D.4 Run Report (`<RunReport>`)
Task `7.12`–`7.17`'s six panels — Signal Funnel, Risk Deployment, Exit Attribution,
Blocked-Signal Timeline, Cost Impact — as one component shared by backtest and live, per
task `7.17`. The backend data for all six already exists (Phase 0 shipped
`rejection_funnel`, `blocked_signals`, `sizing_diagnostics`); only the rendering is missing.

---

## PART E — Market data providers, free and paid

Design: one `MarketDataProvider` protocol, N implementations, provider chosen per data
class in Settings, with a live latency/health readout so the choice is informed.

```python
class MarketDataProvider(Protocol):
    name: str
    tier: Literal["free", "paid"]
    capabilities: set[str]   # {"options_chain","gex","order_book","order_flow","correlation","calendar"}
    async def fetch(self, capability: str, **kw) -> ProviderResult: ...
    async def health(self) -> ProviderHealth: ...
```

| Capability | Free option | Paid option | Recommendation |
|---|---|---|---|
| Options chain / OI | Yahoo Finance (`yfinance`), CBOE delayed CSV | Polygon.io, ORATS | **Start CBOE** — direct-from-exchange delayed file, no scraping fragility |
| GEX | Computed from the free chain above | Polygon + own model | Compute it yourself either way; the vendor only supplies the chain |
| Order book / DOM | MT5 `market_book_get` (already available) | Databento, Bookmap | **MT5** — you already have it; CFD depth is thin but real |
| Order flow | `data/orderflow.py` from MT5 ticks (built) | Databento MBO | **Keep MT5.** Honest limit: no aggressor flag, so CVD is inferred |
| Correlation | Computed from your own MT5 bars | — | **Own bars.** No vendor needed and no vendor is better here |
| Calendar | ForexFactory JSON (already wired) | Econoday, FMP | **Keep ForexFactory** |
| Crypto flow | Binance/Bybit public WS | Kaiko, Amberdata | **Binance public WS** — genuinely free, genuinely fast |

**Latency answer to your question directly:** for a 2–10 s refresh, polling is the wrong
shape for everything except options chains (which only update on exchange dissemination
anyway, ~15 min delayed on free tiers — polling faster buys nothing). For anything that
genuinely moves at seconds — order book, order flow, crypto flow — use a **persistent
subscription** (MT5 `market_book_add` callback, Binance WS) and push to the frontend over
the WS you already have. One socket, no polling.

The concrete shape:
- A `ProviderRegistry` with per-capability selection, persisted in config.
- A shared cache with per-capability TTL, so switching provider does not restart from cold.
- A **provider health strip** in Settings: name, tier, last fetch latency, error rate,
  quota remaining. You pick free now and flip to paid later with no code change.

**Free tiers have real limits and this plan does not pretend otherwise:** Yahoo has no SLA
and rate-limits aggressively; CBOE's free file is end-of-day-ish delayed; free crypto WS
endpoints drop connections under load. The registry's job is to make those failures visible
rather than silent.

---

## PART F — Claude-powered analysis

`services/llm_service.py` already exists with an Anthropic path, and `LLMAnalysis` is
already a table. What is missing is: the analysis targets, the context builders, and the UI.

### F.1 Targets
| Target | Context assembled |
|---|---|
| Backtest run | Metrics, rejection funnel, sizing diagnostics, exit attribution, per-strategy breakdown |
| Portfolio run | The above per leg + correlation between legs |
| Live trades / journal | Closed trades with entry context and MFE/MAE |
| Signals | Fired vs. blocked, with the gate that blocked each |
| Logs | A time-bounded slice, error-weighted |
| Strategy config | Current params vs. the strategy's own spec doc |

### F.2 Shape
- `POST /api/analysis/run` with `{target_type, target_id, question?, provider, model}`
- Context builders live in `services/analysis_context.py`, one per target, each returning a
  **token-budgeted** summary. Raw payloads are not shipped to the model — a portfolio result
  object is megabytes; the builder reduces it to the numbers that carry information.
- Results persist to `LLMAnalysis` with the context digest, so an analysis stays
  interpretable later.
- Streaming responses over the existing WS.

### F.3 Model
Default `claude-opus-5` for analysis depth, `claude-haiku-4-5` for the fast/cheap path,
selectable per request. **Note:** `llm_service.py:35-42` currently pins
`claude-sonnet-4-20250514` and a malformed `claude-haiku-4-5-20250514` — the latter is not a
valid model id and would fail at call time. Both need updating.

### F.4 Key handling
The Anthropic key is read from backend config/env and never sent to the browser. The
frontend calls your backend; your backend calls Anthropic.

---

## PART G — Logging (fixing V2/V3)

1. **WS sink.** A loguru sink that pushes structured records into an `asyncio.Queue`, drained
   by a broadcaster task feeding `manager.broadcast_to_user`. Level-filtered per client, so a
   DEBUG firehose is opt-in.
2. **Ring buffer.** In-process `deque(maxlen=5000)` so a page load gets immediate backfill
   instead of an empty panel.
3. **Session log store.** Each run/session gets an id; records tagged with it. New
   `GET /api/logs` (filter by session, level, category, time range, text) and
   `GET /api/logs/sessions`.
4. **Log viewer UI** with level/category filters, text search, and "Analyze with Claude" on
   any selection — which closes the loop you described: see the error, analyze it in place.

---

## PART H — Sequence

| Step | Work | Depends on |
|---|---|---|
| 1 | `markings.py` + emit from all 6 strategies (**V1**) | — |
| 2 | Log pipeline: WS sink, ring buffer, `/api/logs` (**V2/V3**) | — |
| 3 | Replay stream backend + `replay_series` (**V4**) | — |
| 4 | `ReplayChart` + `BacktestReplay` tabs; delete the skeleton | 1, 3 |
| 5 | `<RunReport>` six panels (`7.12`–`7.17`) | — |
| 6 | Analysis backend + `/analysis` UI | 2 |
| 7 | Provider registry + `/fundamentals` | — |
| 8 | Strategy Lab | 1, 4 |
| 9 | Schema-driven forms (`7.2`–`7.9`, `3.2`) | — |

Steps 1–3 are backend and unblock everything visual. Step 4 is the thing you actually asked
to see.

---

## PART I — Open questions

1. **Optimizer objective.** Sharpe, expectancy, or a custom weighting? Changes the sweep.
2. **Options underlyings.** Which symbols do you want chains for — SPX/NDX only, or single
   names too? Free-tier coverage differs sharply.
3. **Replay default speed.** Bars-per-second in replay mode, and should live mode be
   throttled to human-watchable, or run at engine speed?
4. **Analysis retention.** How long do saved analyses live, and should they be attached to
   the run they analysed or standalone?

---

## PART J — Execution log (2026-08-23)

What actually shipped this session, and what did not. Reported against the Part H
sequence.

### J.1 Shipped and tested

**Step 1 — Confluence markings (V1) — DONE**
- `backend/strategies/core/markings.py` — new. `Marking` dataclass, 8-kind
  vocabulary, 4 roles, `MarkingCollector`, numpy/pandas-safe serialisation, and
  `ts()` coercion for the four timestamp shapes the engines actually hold.
- All six strategies now emit `metadata["markings"]`: APA (H&S pivots, neckline,
  retest band, head invalidation), VWAP (anchor, ±1/2/3σ grid, value area,
  trigger candle), CRT (C1 range, swept liquidity, C2 sweep, HTF bias),
  HTF-FVG-Flip (HTF gap, tap, LTF inverted gap, displacement), Bias-IFVG (bias,
  key level, IFVG, inversion), NY-Open-Retest (ORB box, range mid, break candle).
- Two engines needed geometry *captured at decision time* because their state is
  cleared before entry: CRT's `c2_trigger["geom"]` and FVG-Flip's
  `state["htf_fvg"]`. Without that the chart could only ever show the entry, not
  the structure it came from.
- `trade_grouper.py` routing widened from 3 kinds to all 8, importing the kind
  sets from `markings.py` so the two cannot drift.
- **Tests:** `tests/test_markings.py`, 15 passing — including an end-to-end run
  that drives NY-Open-Retest through its real M15→M5 state machine and asserts
  markings arrive with valid geometry.

**Step 2 — Log pipeline (V2/V3) — DONE**
- `backend/services/log_stream.py` — loguru sink → 5,000-record ring buffer →
  batched WebSocket broadcast (`log_batch`, 250 ms). Thread-safe by construction:
  the sink only appends, because it runs on whatever thread logged, including the
  `asyncio.to_thread` simulation workers.
- Session tracking: each backtest opens a log session; every record it emits is
  tagged, and `log_session_id` is returned on the run and its result.
- `backend/api/routes/logs.py` — `/api/logs`, `/logs/sessions`, `/logs/files`,
  `/logs/file`. Level filtering is a **floor**, not equality. Path-traversal
  guarded.
- Wired into `main.py` lifespan; sink installed from `utils/logger.py`.

**Step 3 — Replay stream (V4) — DONE**
- `backend/services/replay_stream.py` — `ReplayStreamer` with batching (400
  bars), per-leg throttling (120 ms floor), forced flush before every signal so a
  marker can never precede its bar, and `downsample()` with OHLC bucket
  aggregation for the continuous series.
- Wired into both backtest routes. Portfolio legs keyed by **slot**, so the same
  symbol under two strategies stays two independent legs.
- `GET /api/backtest_result/replay` serves the finished series; it is stripped
  from the completion WS payload deliberately (it is the largest object in a
  portfolio result and the client already streamed it).
- **Tests:** `tests/test_replay_stream.py`, 12 passing — batching, throttle-
  without-loss, cursor monotonicity, signal/bar ordering, per-slot independence,
  downsample extreme preservation, and broken-socket isolation.

**Step 4 — Replay chart UI — DONE**
- `frontend/src/components/ReplayChart.jsx` — lightweight-charts renderer,
  append-not-redraw on live batches, sliding logical-range window, marker budget,
  marking overlays coloured by role.
- `frontend/src/components/BacktestReplay.jsx` — per-slot tab strip with
  auto-advance, live→replay transition, play/pause/speed/seek, and a signal rail
  showing each entry's confluence chain.
- `CustomChartPrimitives.js` extended: shared base, open-ended rectangles,
  `LevelPrimitive`, `BubblePrimitive` (√-scaled radius for order flow).
- The skeleton loader at `Backtester.jsx:2033` is gone.

**Step 6 — Claude analysis — DONE (backend + UI)**
- `backend/services/analysis_context.py` — token-budgeted context builders. Real
  measurement: a 4,469 KB backtest result reduces to a 4.6 KB prompt (~1,150
  tokens) with representative trade sampling spanning the outcome distribution.
- `backend/api/routes/analysis.py` — `/api/analysis/run|history|providers`,
  results persisted to `LLMAnalysis` with the question and digest.
- `frontend/src/pages/Analysis.jsx` — ask/answer, saved history, and the log
  viewer with "Analyse" wiring the two together.

### J.2 Defects found and fixed while building

| # | Defect | Where |
|---|---|---|
| **V5** | `llm_service.py` pinned `claude-haiku-4-5-20250514` — not a valid model id; every fast-path request would have failed. Default was also a retired Sonnet 4 snapshot. | `services/llm_service.py:35-42` |
| **V6** | `LLMService()` is constructed with no arguments at every call site, so `api_keys` was always `{}` and the key was always `""`. No LLM call could ever have authenticated. | `services/llm_service.py` |
| **V7** | `max_tokens=1024` on every LLM call — truncates a real analysis mid-sentence. | `services/llm_service.py` |
| **V8** | `win_rate`, `max_drawdown_pct` and the `*_hit_rate` fields are **fractions**, not percentages, despite the `_pct` suffix (`reports.py:175`, `metrics.py:203`). The first version of the context builder reported a 21.4% drawdown as "0.2%". | `services/analysis_context.py` (fixed there; the naming trap remains in the analytics layer) |
| **V9** | The chart's user-scroll detection via `subscribeVisibleLogicalRangeChange` fires on `setData()`'s own auto-fit, so the chart disabled its own follow-pin on the opening batch of every run. Replaced with wheel/pointer detection. | `ReplayChart.jsx` |
| **V10** | `requestAnimationFrame` is suspended in a hidden browser tab, so switching away mid-run buffered the entire backtest and flooded on return. Added a `setTimeout` fallback clock. | `BacktestReplay.jsx` |
| **V11** | `asyncio.create_task` returns before the coroutine runs, so a `try` around it only catches "no running loop". A dead client socket produced one unretrieved-task traceback per bar batch. Moved the guard inside the task, plus a strong reference set so tasks are not GC'd mid-flight. | `services/replay_stream.py` |

### J.3 Not done — stated plainly

- **Step 5** `<RunReport>` six panels (`7.12`–`7.17`). Backend data all exists.
- **Step 7** Provider registry + `/fundamentals` page. Specified in Part E; not built.
- **Step 8** Strategy Lab (`/strategy-lab`). Specified in Part D.1; not built.
- **Step 9** Schema-driven parameter forms (`7.2`–`7.9`, `3.2`). `schema_introspection.py`
  is ready on the backend; the React consumer is not written.
- **Task 10.5** order flow → `MarketContext` wiring. Untouched this session.
- **Visual verification** of the replay chart was done against a synthetic harness
  driving real WebSocket events (600 bars, 2 signals, 2 portfolio tabs, confluence
  tags rendering) — **not** against a live MT5 backtest, which this environment
  cannot run. Everything in J.1 needs one real run against your MT5 connection
  before it is trusted on real data.

---

## PART J — Execution report (2026-08-23)

What actually shipped this session, and what is still open. Written after the
work, against the working tree, not against intentions.

### J.1 Shipped and verified

| Item | Status | Evidence |
|---|---|---|
| **V1** — `markings.py` + emission from all 6 strategies | **Done** | `tests/test_markings.py` drives NY-Open-Retest through its real M15→M5 state machine and asserts the markings reach `signal.metadata` with valid geometry. 20/20 tests pass. |
| **V1b** — `trade_grouper` routing widened | **Done** | Was a three-kind filter (`OB`/`FVG`/`STRUCTURE`); now imports the kind sets from `markings.py` so it cannot drift from the vocabulary the strategies emit. |
| **V2/V3** — log pipeline | **Done** | `services/log_stream.py` ring + WS pump, sink installed from `utils/logger.py`, `GET /api/logs{,/sessions,/files,/stats}` live. Backend log confirms `Log stream attached to WebSocket manager`. |
| **V4 / §C.7** — continuous replay series | **Done** | `ReplayStreamer.series_payload()` attached to both routes, persisted via the new `backtest_runs.replay_data` column, served by `/api/backtest_result/replay` and `/api/backtests/{id}/replay`. |
| **§C.3** — replay stream | **Done** | `ReplayStreamer` wired into both Phase-1 loops: `replay_init`/`leg_start`/`bars`/`signal`/`leg_done`/`done`. Portfolio legs keyed by `slot_key`, not symbol. |
| **§D.1–D.4** — the four missing UIs | **Done** | `StrategyLab.jsx`, `Fundamentals.jsx`, `Logs.jsx`, `RunReport.jsx` (+ `SchemaForm`, `ModelPicker`, `AnalyzeButton`). Routed in `App.jsx`, build clean, lint clean. |
| **Part E** — provider registry | **Done** | `data/providers.py` — 8 providers across 6 capabilities, free defaults, paid slots declared and inert without a key. `/api/fundamentals/*` live (11 routes). |
| **Part F** — Claude everywhere | **Done** | Model registry with per-model real ceilings; `/analysis/models`; targets extended to `trade`, `strategy_config`, `orderflow`, `fundamentals`. `<AnalyzeButton>` on Backtester, per-trade chart, Journal, Signals, Logs, Fundamentals, Strategy Lab. |
| **10.5** — order flow → consumer | **Done** | `compute_orderflow_snapshot()` composes the Phase-10 primitives; consumed by the provider registry and the Fundamentals panel. |

### J.2 A real defect found by running it — worth calling out

Phase 10's order-flow module had **never been run against live data** (task 10.5
said so plainly: the functions were "ready to be called" and nothing called
them). Running it exposed that it does not work on this broker's feed.

`classify_ticks` read `ticks["last"]` unconditionally. On the live Deriv feed
`last` is **0.0 on every tick**, and `volume`/`volume_real` are **0 on every
tick** — it is a quote feed, not a trade feed. So `last <= bid` was trivially
true and *every tick classified as a sell*:

```
BTCUSD  before:  cvd = -12721, delta = -12721, imbalance = -1.0, bubbles = 0, vpoc = None
V75     before:  cvd = -900,   delta = -900,   imbalance = -1.0, bubbles = 0, vpoc = None
```

`cvd == -tick_count` exactly, on every symbol, forever. Not a weak signal — a
constant that reads as relentless selling pressure. The volume profile and the
bubble overlay were empty for the same reason (an all-zero price column
collapses to one bin).

Fixed by adding a **quote rule** (tick rule on the mid) for quote-only feeds,
keeping Lee-Ready where traded prices exist, and routing every price read
through `tick_price()`:

```
BTCUSD  after:   cvd = -602, buy 6306 / sell 6908, imbalance = -0.046, bubbles = 146, vpoc = 77281.61
V75     after:   cvd = -16,  buy 441 / sell 457,   imbalance = -0.018, bubbles = 162, vpoc = 49836.54
```

The snapshot now reports `classification` (`quote_rule` vs `lee_ready`) and
`volume_is_tick_count`, and carries a caveat string — because the quote rule is
a genuinely weaker proxy and a consumer should be able to say so. Locked in by
five regression tests.

**The general lesson, stated plainly:** the master plan's Phase 10 was marked
complete with its consumer-side wiring left open, and that gap hid a total
failure of the module on the only broker this system uses. "Implemented but not
wired" is not the same as "working."

### J.3 Still open

| Item | Why |
|---|---|
| Browser verification of the authenticated pages | Needs a login. Build, lint, route registration and backend behaviour are verified; the rendered pages behind the auth guard are not. |
| `D-5` Bias-IFVG direction | Still your decision — see Part B.2. |
| `1.14` GER40 / `1.15`–`1.16` XRPUSD contract specs | Needs `symbol_info()` output from your terminal. |
| `0.6`, `2.25`, `4.9`, `Rule-6` | Validation passes for you to run against live MT5. |
| Optimizer sweep in Strategy Lab | The screen ships with create/preview/promote; the parameter sweep needs the objective decision in Part I.1. |
| `yfinance` / `databento` providers | Registered but their SDKs are not installed, so they show as unavailable. `cboe` (free, exchange-published, greeks included) is the default and needs neither. |

---

## PART K — D-5 and the instrument profiles, resolved (2026-08-23)

Both were delegated with "use your recommended solution, the one that ensures an
optimal result." Neither turned out to need a guess.

### K.1 D-5 — Bias-IFVG direction

**The spec already answered it.** `docs/strategy-2-bias-keylevel-ifvg.md` Step 3:

> *"Define the **manipulation leg**: the swing (high→low, or low→high) that
> **actually touched** the Step 2 key level. Scan the 5m timeframe for FVGs that
> exist **within that specific leg** (not elsewhere on chart)."*

The leg that *touched* the level is the leg that **approached** it. The engine
did the opposite — `fvg_time >= manipulation_leg_start`, where
`manipulation_leg_start` was set to the tap moment, so only gaps forming *after*
the tap qualified. Its own inline comment flagged the mismatch and left it open.

**Shipped:** `BiasIFVGParams.ifvg_leg_mode`, defaulting to spec-conformant.

| Mode | Window | Why it exists |
|---|---|---|
| `APPROACH` **(default)** | leg start → tap | What the spec describes |
| `REACTION` | tap → ∞ | The engine's previous behaviour. Retained because the **entire existing backtest corpus was produced under it**, so historical results are only reproducible with it |
| `BOTH` | leg start → ∞ | For a like-for-like comparison run |

`_approach_leg_start()` finds where the leg began — the highest high before the
tap for a BUY, the lowest low for a SELL, over a 40-bar M5 window. It fails safe:
an exception returns `None` and the caller falls back to the tap time, which
reproduces `REACTION` exactly rather than dropping the setup.

**Which mode is more profitable is empirical, not doctrinal.** The default
follows the spec; the flag exists so a backtest settles it instead of an
argument. Run all three on the same window and compare.

### K.2 Instrument profiles — verified, not guessed

Task 1.16 asked for exactly this and was never built. Now it is
(`scripts/verify_instrument_profiles.py`), and the first run was sobering:

```
matched      : 2
MISMATCHED   : 57
unavailable  : 22   (GER40, NAS100, US30, ... — this broker does not list them)
```

**57 of 59 verifiable profiles disagreed with the broker.** Selected:

| Symbol | Field | Profile | Broker | Consequence |
|---|---|---|---|---|
| XRPUSD | `lot_min` / `lot_step` | 50 / 10 | **500 / 100** | Debug runs sized at 19.22 lots — below `volume_min`, so **rejected outright**. Those backtests simulated unplaceable orders |
| XAUUSD | `lot_max` | 50 | **10** | Sizer could emit 5x the broker's ceiling |
| XCUUSD | `contract_size` | 25000 | **1.0** | 25,000x |
| DOGUSD | `lot_min` | 100 | **1500** | Same class as XRPUSD |
| USDJPY | `point_value_per_lot` | 0.67 | **0.6289** | A **frozen FX rate** (audit C1) |
| USDCHF | `point_value_per_lot` | 1.15 | **1.2477** | Same |

The FX ones cannot be fixed by editing constants: they are a quote frozen into a
literal, and they go stale again the moment the rate moves.

**So the fix is architectural, not another editing pass.** `get_instrument_profile()`
now overlays live `symbol_info` onto the static profile, cached per process:

- **Overridden** (broker is authoritative): `lot_min`, `lot_max`, `lot_step`,
  `point_size`, `contract_size`, and `point_value_per_lot` derived as
  `trade_tick_value x point_size / trade_tick_size`.
- **Not overridden** (yours, not the broker's): `session_filter`, `news_filter`,
  `trades_24_7`, `instrument_type`.
- **Falls back** to the static profile when MT5 is absent (CI, Linux) or the
  symbol is unlisted — never returns `None` where it previously returned a
  profile, since `None` means "refuses to trade" downstream (audit C5).
- **Logs every override once**, so a silent resize is impossible.
- `ALGOEDGE_DISABLE_LIVE_PROFILES=1` pins the static tables for exact
  reproduction of an old run.
- Cached for the process so a run sees ONE profile throughout —
  re-reading mid-run would let size drift with the FX rate and make the run
  unreproducible. `refresh_live_profiles()` between runs.

A test locks the invariant that matters: BTCUSD's `point_size` and
`point_value_per_lot` both shift 1000x (1.0/1.0 → 0.001/0.001), and sizing
depends on their **ratio**, so the correction must not resize anything there.

**GER40 (task 1.14) is not resolved and cannot be here** — `symbol_select` returns
False on this account, so the profile is unverifiable *and* unreachable (a run
naming it fails at data fetch, not at sizing). Left untouched rather than
adjusted on a guess.

### K.3 Also fixed in passing

- **`frontend/.env` pointed at a deployed AWS backend** running older code —
  404 on every Phase 13 endpoint. That was the login "network error". Now
  localhost, with the remote kept as a comment.
- **`services/api_key_store.py`** (Fernet encryption, `APIKey` table) existed
  wired to **nothing** — the third orphan module found this session, after
  `markings` and `orderflow`. Now backs a new **Settings → AI** page: keys are
  stored encrypted, never returned to the browser, and take precedence over the
  environment.
- **Order-flow cache TTL** was 2s against a ~6s fetch, so every request missed
  the cache. Raised to 15s (the cache key includes the window, so short windows
  stay fast).
