# 04 — Technical × fundamental interaction

**Stage D** · produced 2026-08-30

---

## Blocked, downstream of Stage C

Stage D asks whether adding a fundamental to a technical gate makes a trade
better. That question requires two inputs:

1. Per-gate technical telemetry — **missing**, bug B7 (see report 02).
2. Point-in-time fundamental history — **missing**; partly reconstructible for
   order flow (historical ticks exist, see report 03's correction), genuinely
   unavailable for order-book depth and options/GEX.

Neither exists, so the interaction matrix has no cells to fill. Producing one
would mean fabricating it.

## The two named questions

**"What does order flow do for APA?"** Unanswerable today. Worth stating what it
would take, because it is more than it looks: APA fires 1,236 trades across 19
symbols over eight months. To measure the interaction you need the order-book
state *at each of those 1,236 entry timestamps*. That state was never recorded
and cannot be reconstructed — MT5 does not serve historical book depth. The only
route is forward: store the book at every signal from now on, and revisit once
there are enough observations. At APA's observed rate that is roughly 150
signals a month, so a usable sample is 6–12 months away.

**"What does gamma do for VWAP?"** Same blocker, plus a coverage problem that
does not resolve with time. VWAP's 2,186 trades are concentrated in crypto (795)
and metals (434) — instruments with **no listed options market**, hence no gamma
exposure to compute. Of VWAP's traded symbols only US SP 500 and US Tech 100
have real GEX data, and those are 522 trades between them across all index
symbols. So even after T5.1 the honest answer for VWAP × gamma will be "measured
on two symbols, no coverage on the rest."

That is worth knowing *now*, because it means the VWAP × GEX pairing is not
worth building infrastructure for. Report 07 recommends against VWAP on crypto
on much stronger grounds anyway.

---

## One thing this stage can say without the data

Stage D's premise is that fundamentals might rescue marginal technical setups.
Report 01 argues that premise is aimed at the wrong target: **64% of the book's
loss is friction**, and no filter — technical or fundamental — reduces the
spread paid on a trade it still takes. A fundamental gate can only *remove*
trades. If the trades it removes are a random sample, it removes proportional
cost and proportional edge and changes expectancy by nothing.

So even a working Stage D would be second-order behind the cost work and the
asset selection in report 07. It should be sequenced after them, not before.

## Stage D checklist

| item | status |
|---|---|
| Per strategy × gate × fundamental, R1–R5 with and without | ❌ blocked (B7 + no history) |
| Identify pairs where the fundamental adds | ❌ blocked |
| Order flow for APA | ❌ blocked — needs ~6–12 months of forward collection |
| Gamma for VWAP | ❌ blocked — and no options coverage on 56% of VWAP's trades |

**Unblocked by:** B7 fix + re-run, then T5.1, then a collection period.
