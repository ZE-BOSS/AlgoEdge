# 03 — Fundamental confluences

**Stage C** · produced 2026-08-30

---

## Verdict: Stage C is not executable, and the reason is structural

Not "we ran out of time". There is **no historical fundamental data stored in
this system**, and no provider serves the order book or options chain
retrospectively.

> **Partial correction (2026-08-30).** "None of the providers can supply any" is
> too strong for **order flow**. MT5 *does* serve historical ticks, and this
> project's `MT5OrderFlowProvider` infers CVD from bid/ask movement rather than
> from a real tape — so that same inference can be run over the past without
> waiting to collect it. The obstacle is speed, not availability: a single
> symbol-day took **20+ minutes** to download (bug **B10**), so it needs an
> overnight bulk pull. The order **book** (depth) and **options/GEX** remain
> genuinely unavailable historically — those are live-only and gone once the
> moment passes.

### 1. No fundamental has ever influenced a backtest

`backend/strategies/core/fundamental_gate.py` implements four gates —
`EconCalendarGate`, `OrderFlowGate`, `CorrelationGate`, `GexRegimeGate`. Every
one of them opens with:

```python
def check(self, signal, is_backtesting=False):
    if is_backtesting:
        return False, ""
```

The module's own docstring is explicit about why: *"Historical fundamental data
is not yet available in the provider layer, so attempting a live fetch during a
backtest would pull present-day data for a past bar."* That is the correct call
— it is look-ahead bias — but it means the gates are, by construction, invisible
to every backtest.

### 2. No strategy uses them even in live trading

`base_strategy.py:84` sets `self.fundamental_gates = None`. The only references
to `FundamentalGateRunner` in the codebase are inside a **docstring example**.
No engine constructs one. The gates are live-capable and unused.

`CorrelationGate` is additionally a stub — it ends
`return False, "" # placeholder — full impl requires live price momentum data`
and can never block anything.

### 3. Every provider is present-tense only

| provider | source | history available? |
|---|---|---|
| `ForexFactoryCalendarProvider` | `ff_calendar_thisweek.json` | **no** — this week only, by URL |
| `MT5OrderFlowProvider` | MT5 tick aggregation | live snapshot |
| `MT5BookProvider` | MT5 market book | **live only** — the book is never stored |
| `YahooOptionsProvider` / `CBOEOptionsProvider` | current chain | current expiry chain only |
| `PolygonProvider` | Polygon.io | needs `POLYGON_API_KEY` — **not set** |
| `DatabentoProvider` | MBO tape | needs `DATABENTO_API_KEY` — **not set** |

### 4. Nothing is persisted

`backend/data/models.py` defines 13 tables. **None** of them stores a
fundamental reading. There is no calendar table, no order-book snapshot table,
no GEX table, no COT table. Every fetch is read-once-and-discard.

So the position today is: a signal is computed live, discarded, and there is no
record that it ever existed. Nothing can be backtested against.

---

## Answering the question directly: *can fundamentals alone be a strategy?*

**Not from where this system currently stands, and not soon.** Two separate
reasons, and they matter in different ways:

**The blocking reason** is that you cannot validate what you cannot replay.
Turning any fundamental into a strategy means measuring it — frequency,
direction accuracy, forward excursion, P&L — and that requires point-in-time
history. T5.1 is not one task among several in Phase 5; it is the gate on the
entire question. Until fundamental readings are stored *with the timestamp they
were observed*, any backtest that uses them is look-ahead biased and its result
is worth nothing. A promising number produced before T5.1 lands would be a
false positive, and acting on it would cost real money.

**The structural reason** is coverage. Even with storage built, the honest
inventory is thin:

- **Order flow** — MT5's book is a broker-side aggregate on a demo feed, with
  no aggressor flags. It is an inference, not a tape. Real order flow needs
  Databento (MBO) and a paid key.
- **GEX / options** — genuinely exists for SPX and major US index/equity
  underlyings. It does **not** exist for Deriv synthetics, FX pairs, or metals,
  which is most of the traded book. §5.3's own warning applies: do not
  fabricate coverage.
- **Economic calendar** — real and freely available going forward, but the
  current provider fetches one week and keeps nothing. It is also a *blackout*
  signal (don't trade into NFP), not a directional one. It reduces variance; it
  does not generate entries.
- **COT** — not implemented at all. Weekly, three days stale on release, and
  covers futures rather than the CFDs actually traded.

**The recommendation.** Do not pursue a fundamentals-only strategy. Build T5.1
(point-in-time storage) and start *accumulating* — it costs little, and in six
months there is a dataset that can answer this question honestly. Meanwhile the
realistic near-term role for fundamentals is the narrow one they are already
shaped for: **`EconCalendarGate` as a live risk blackout.** That does not need
history to justify — not holding a position into a high-impact release is a
risk decision, not a predictive claim — and it is the one gate that is finished,
correct, and simply not wired to any strategy.

That is a genuinely useful thing to ship, and it is roughly a day's work.

---

## Stage C checklist

| item | status |
|---|---|
| Order **flow** as a measured signal | ⚠️ **reconstructible from historical ticks** — needs an overnight bulk pull (B10) |
| Order **book** depth | ❌ blocked — live snapshot only, never stored |
| GEX / gamma zones, respect rates | ❌ blocked — no stored history |
| Economic calendar behaviour around events | ❌ blocked — provider is one-week, no storage |
| COT / positioning | ❌ not implemented |
| Full §0.3 metric set per fundamental | ❌ blocked |

**Unblocked by:** for order flow, an overnight tick download (no waiting
required). For the book, GEX and COT: T5.1 point-in-time storage plus a
collection period.
