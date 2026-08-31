# 02 — Technical confluences

**Stage B** · produced 2026-08-30 · source: 7,463 trades across 116 runs

---

> **Superseded in part (2026-08-30).** Everything below about the *gate recorder*
> is still true, but the conclusion "Stage B cannot be answered" was wrong. The
> question "is this confluence worth anything" does not need the recorder at all
> — it can be measured on raw market data. That is done in
> **`research/08` §5**, which is the actual Stage B answer. The recorder is still
> needed for a narrower question: how well each strategy *implements* those
> structures.

## The gate-recorder blocker, stated first

**B.1 and B.2 cannot be answered from this sweep's stored telemetry.** Per-gate frequency, pass
rate and block count are empty in all 116 runs: `strategy_rejections` is `{}`
everywhere and `confluence_stats.by_confirmation` contains only the single
lumped bucket `base_structure`.

The cause is *not* missing code. The engines carry **49 `self.gate(...)` call
sites**, `backend/strategies/gate_recorder.py` is a complete implementation, and
`backend/backtester/ablation.py` has a working recording pass. But
`base_strategy.py:92` builds `GateRecorder(enabled=False)` and **nothing on the
`/api/backtest` path ever turns it on** — only `ablation.run_recording_pass`
does, and the route cannot reach it. `engine.py:1381` guards on
`getattr(_gates, "enabled", False)` and so contributed nothing, silently, for
every run.

This is bug **B7**. It is a configuration fix plus a re-run, not a build.

| gate call sites now instrumented | |
|---|---:|
| VWAP | 19 |
| DriftJumpAlpha (`strategy_two`) | 9 |
| APA | 8 |
| CRT | 5 |
| NYOpenRetest | 4 |
| BiasIFVG | 3 |
| HTFFVGFlip | 1 |
| **total** | **49** |

HTFFVGFlip with a single instrumented gate is the weak spot — it is also the
strategy with the smallest sample (183 trades). Both need attention before a
re-run is worth doing.

---

## What *could* be measured

Trade-level fields are complete, so the following is real evidence.

### Confluence score — inconsistent, and inert on three strategies

| strategy | score range observed | corr(score, expectancy) | verdict |
|---|---|---:|---|
| HTFFVGFlip | 80–109 | **+0.96** | works (n=157, thin) |
| APA | 50–89 | **+0.75** | works |
| BiasIFVG | 60–109 | +0.10 | noise |
| VWAP | 50–89 | **−0.80** | **inverted** — higher score is worse |
| CRT | 90–99 only | — | **constant, no information** |
| NYOpenRetest | 90–99 only | — | **constant, no information** |
| DriftJumpAlpha | 80–89 only | — | **constant, no information** |

Three of seven strategies emit a confluence score with **zero variance**. On
those, `reject_below_confluence` and `confluence_risk_tiers` are incapable of
changing any outcome — they would be dead configuration.

On VWAP the relationship is *inverted*: score 60–69 returns −0.117R across 839
trades while 80–89 returns −0.372R across 93. Scoring more confluences on VWAP
selects worse trades.

Pooled across strategies the score looks meaningless (score 80 → −0.012R,
score 90 → −0.316R) but that pooled view is a strategy confound and should not
be quoted — the per-strategy split above is the honest one.

### Session — the confound is real, again

Rule §0.5-3 was right to warn about this. Raw, the Asian session looks like the
one profitable window:

| session | n | exp R | total R |
|---|---:|---:|---:|
| ASIAN | 786 | **+0.200** | +157.4 |
| LONDON | 571 | −0.122 | −69.5 |
| LONDON/NY | 2,620 | −0.217 | −568.3 |
| NY | 3,351 | −0.249 | −836.0 |

**+128R of that +157R is Crash 300 and Crash 1000 — i.e. DriftJumpAlpha.**
Controlling for it:

- **DriftJumpAlpha by session:** ASIAN +0.253, LONDON +0.279, LONDON/NY +0.262,
  NY +0.235. Flat. The edge is not time-of-day at all; the Asian concentration
  only reflects when Crash indices produce most signals.
- **Session-bound markets only** (FX, indices, metals): LONDON −0.164,
  LONDON/NY −0.172, NY −0.247. Every session loses. A session filter can make
  the book lose *less*, never win.
- **Crypto only** (genuinely 24/7, so session is a real time variable):
  ASIAN +0.272 on 174 trades vs NY −0.296 on 1,099. A genuine within-class
  contrast — but the +47R comes from a handful of large winners against a 21.3%
  win rate. **Candidate, not a finding.** Needs the re-run to confirm.

So the "Asian session works" headline collapses almost entirely to
"DriftJumpAlpha works", exactly as it did last time.

### Direction

BUY −0.161R on 4,192 trades, SELL −0.204R on 3,271. No usable asymmetry.

### Stop placement — MAE of winners

| strategy | winners | median MAE | p75 | p90 | share > 0.5R |
|---|---:|---:|---:|---:|---:|
| DriftJumpAlpha | 132 | 0.24 R | 0.49 | 0.78 | 24.2% |
| VWAP | 383 | 0.23 R | 0.55 | 0.76 | 27.9% |
| NYOpenRetest | 99 | 0.25 R | 0.59 | 0.81 | 33.3% |
| APA | 133 | 0.28 R | 0.54 | 0.77 | 27.8% |
| BiasIFVG | 105 | 0.34 R | 0.62 | 0.88 | 35.2% |
| CRT | 73 | 0.43 R | 0.75 | 0.91 | 39.7% |

Roughly three quarters of winning trades never take more than 0.5R of heat.

**Do not read this as "halve the stops".** At a fixed 1% risk, halving the stop
distance doubles the fraction of R eaten by spread — and report 01 shows
friction is already 64% of the loss. Tightening stops makes the dominant
problem worse. The lever this data actually supports is the opposite one:
*avoid instruments where the stop is small relative to the spread.*

---

## B.2 checklist — answered in `research/08` §5

Measured on 24 symbols / 1.14M bars pulled directly from MT5, so none of it
depended on the gate recorder after all:

| structure | measured lift | status |
|---|---:|---|
| Liquidity sweeps | **+0.027 / +0.018 R** | ✅ best atoms in the set |
| Premium / discount | **+0.023 / +0.019 R** | ✅ second best |
| HTF bias | +0.003 R | ✅ measured — a coin flip (27/48) |
| Sessions | ≈ 0 | ✅ measured — confounded at book level |
| Wick rejection | −0.004 R | ✅ now reachable, and neutral |
| Displacement / momentum | −0.014 R | ✅ measured — negative |
| FVG / IFVG | **−0.017 R** | ✅ measured — negative |
| Volume confirmation | −0.021 R | ✅ measured — negative, drop it |
| Retest | **−0.027 R** | ✅ measured — negative |
| Break of structure | **−0.037 R** | ✅ measured — worst |
| Head & shoulders follow-through | — | ❌ not yet reduced to an atom (APA-specific) |

What still needs the B7 re-run is narrower than this report originally implied:
per-gate *pass and block rates*, i.e. how faithfully each strategy implements
these structures — not whether the structures are worth anything.

## B.3 ablation

Not attempted. `ablation.py` provides the machinery (`disabled_gates`, and it
marks the run so an ablation can never be mistaken for a baseline), but running
it against gates whose block rates are unknown would violate §0.5-4 — a gate
that never blocks cannot change an outcome, and there is no point spending a
re-run to learn that. **Order of operations: B7 fix → recording pass → read
block rates → ablate only the gates that block often.**
