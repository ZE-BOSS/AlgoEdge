"""
backend/analytics/forecast_harness.py — [P3.10 / P3.11]

The post-cutoff test: can a forecaster make money on market data it cannot
possibly have seen?

WHY POST-CUTOFF RATHER THAN BLINDING
------------------------------------
A model asked about a market it was trained on may be recalling rather than
forecasting. Blinding (no names, no dates, relative levels) makes recall
*harder*; it cannot make it impossible, because the shape of a famous move can
survive anonymisation. A decision whose outcome lies after the model's
knowledge cutoff cannot be recalled by any version of that model, however much
it knows about the instrument in general. So the primary test runs only on
post-cutoff decisions, and blinding stays on as a second line of defence.

The earlier alternative — the assistant producing the forecasts in-session —
was withdrawn on 2026-09-11 for a reason worth keeping: the in-session model has
read the backtests, knows the project and knows what result would be convenient,
so it is MORE contaminated than a clean API call, not less.

WHAT A RUN GUARANTEES
---------------------
- decisions only on bars timestamped at or after `cutoff`;
- the provider is shown bars up to and including the decision bar, never after;
- entry at the NEXT bar's open, with stop and target re-anchored by parallel
  shift — the convention the backtester and live path share (Phase 1);
- a fixed resolution horizon, and decisions stepped by that horizon, so trades
  on one instrument can never overlap;
- stops priced by `fill_model` (the backtester's own adverse-fill model), targets
  as limit orders, and a bar that touches both resolves to the stop;
- plumbing failures (billing, auth, budget) counted separately and never scored
  as model abstentions — and a provider that fails on its first few calls stops
  the run instead of producing hundreds of meaningless FLATs.

THE CONTROL
-----------
The same pipeline is re-run on surrogate price series that keep the volatility
path and destroy direction (`sign_flip`). A result the pipeline also produces on
structureless data is the pipeline, not the market.

  full       — K complete surrogate runs, one pooled expectancy each; the real
               expectancy is ranked against those K numbers. Needs K >= 10. The
               default for the free baseline.
  bootstrap  — a few surrogate series, all their actionable forecasts pooled, and
               the mean at the REAL sample size bootstrapped from that pool. Far
               fewer calls for the same question; the default for paid providers.

Pricing note: a surrogate run re-forecasts EVERY decision point, so the control
costs `n_surrogates x decisions`, not `n_surrogates`. The planning estimate made
on 2026-09-11 before this harness existed priced it as the latter and
under-counted it by roughly the number of decisions per instrument. `plan_run`
prices it correctly, and the run refuses before its first call if the plan
exceeds the ceiling.

THE VERDICT
-----------
  PASS          beats the surrogate control, conservative significance holds, and
                there are at least GATE_MIN_INDEPENDENT independent actionable
                forecasts (plan gate 3.14).
  FAIL          no better than structureless data, or non-positive expectancy.
                Decisive — spend nothing further.
  INCONCLUSIVE  some evidence, not enough to pass. A cheap run is expected to land
                here when the result is promising and underpowered.
  INSUFFICIENT  fewer than 30 actionable forecasts; nothing can be concluded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from backend.analytics.forecast_context import ContextBuilder
from backend.analytics.forecast_scoring import permutation_control, score_forecasts, surrogate_series
from backend.analytics.significance import assess
from backend.backtester.fill_model import StopFillModel
from backend.services.forecast_providers import Bars, is_operational_failure
from backend.strategies.baseline_forecaster import Forecast
from backend.utils.logger import get_logger
from backend.utils.timeutils import detect_session

logger = get_logger(__name__)

#: First bar a post-cutoff test may decide on. Every current Claude model's
#: knowledge cutoff falls before this (Opus 5's is May 2026); a model with a
#: later cutoff needs a later date, and the CLI takes it as an argument.
DEFAULT_POST_CUTOFF = datetime(2026, 6, 1, tzinfo=timezone.utc)

#: Plan gate 3.14 — independent actionable forecasts required to pass.
GATE_MIN_INDEPENDENT = 200

#: Asset class shown to the model in place of the instrument name.
INSTRUMENT_CLASSES = {
    "XAUUSD": "metal", "XAGUSD": "metal", "XPTUSD": "metal",
    "EURUSD": "fx", "GBPJPY": "fx",
    "BTCUSD": "crypto",
    "US TECH 100": "equity_index", "US SP 500": "equity_index",
}

GENESIS_HASH = "0" * 64


class CostCeilingExceeded(RuntimeError):
    """The planned run would cost more than the caller allowed. Raised before any call."""


class ProviderUnavailable(RuntimeError):
    """The provider failed operationally on every call so far — stop, don't score it."""


def instrument_class_for(symbol: str) -> str:
    return INSTRUMENT_CLASSES.get(str(symbol).upper().strip(), "unknown")


# ── the append-only journal ──────────────────────────────────────────────────

def _row_payload(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))


@dataclass
class ForecastJournal:
    """Hash-chained, append-only record of every forecast a run made.

    Each line carries the hash of the previous one, so a row edited or deleted
    after the fact breaks the chain and `verify()` says where. A forward test is
    only evidence if nobody — including whoever runs it — can quietly revise a
    losing forecast.
    """

    path: Path
    _last: str | None = field(default=None, init=False, repr=False)

    def _tail_hash(self) -> str:
        if self._last is not None:
            return self._last
        last = GENESIS_HASH
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        last = json.loads(line)["hash"]
                    except Exception:
                        break
        self._last = last
        return last

    def append(self, row: dict[str, Any]) -> str:
        prev = self._tail_hash()
        digest = hashlib.sha256((prev + _row_payload(row)).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"prev": prev, "hash": digest, "row": row},
                                sort_keys=True, default=str) + "\n")
        self._last = digest
        return digest

    def verify(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"ok": True, "rows": 0, "note": "empty journal"}
        prev, n = GENESIS_HASH, 0
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    return {"ok": False, "rows": n, "note": f"line {lineno}: not JSON"}
                if rec.get("prev") != prev:
                    return {"ok": False, "rows": n,
                            "note": f"line {lineno}: chain broken — a row before it was removed or reordered"}
                expect = hashlib.sha256((prev + _row_payload(rec.get("row"))).encode()).hexdigest()
                if expect != rec.get("hash"):
                    return {"ok": False, "rows": n,
                            "note": f"line {lineno}: row altered after it was written"}
                prev = rec["hash"]
                n += 1
        return {"ok": True, "rows": n, "note": "chain intact"}


# ── resolving one forecast against what happened next ────────────────────────

@dataclass
class Outcome:
    r: float
    exit_reason: str          # TARGET | STOP | TIMEOUT
    exit_index: int
    entry_price: float
    stop: float
    target: float


def resolve_outcome(
    forecast: Forecast,
    *,
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    decision_index: int,
    horizon: int,
    instrument: str = "X",
    fill_model: StopFillModel | None = None,
) -> Outcome | None:
    """Price a forecast on the bars strictly AFTER its decision bar.

    Returns None for a FLAT, for geometry that does not describe a trade, or when
    the horizon runs past the data — an unresolvable forecast is left out rather
    than marked to a partial outcome.
    """
    i = int(decision_index)
    if forecast.direction not in ("LONG", "SHORT"):
        return None
    if forecast.invalidation is None or not forecast.targets or forecast.entry_zone is None:
        return None
    if i + horizon >= len(close):
        return None

    fm = fill_model or StopFillModel()
    long_ = forecast.direction == "LONG"
    ref = float(forecast.entry_zone[0])
    entry = float(open_[i + 1])
    shift = entry - ref
    stop = float(forecast.invalidation) + shift
    target = float(forecast.targets[0]["level"]) + shift
    dist = abs(entry - stop)
    if dist <= 0:
        return None
    if long_ and not (stop < entry < target):
        return None
    if not long_ and not (target < entry < stop):
        return None

    for j in range(i + 1, i + horizon + 1):
        o, h, l = float(open_[j]), float(high[j]), float(low[j])
        stop_hit = (l <= stop) if long_ else (h >= stop)
        target_hit = (h >= target) if long_ else (l <= target)
        if stop_hit:  # checked first, so a bar touching both resolves to the stop
            fill, _, _ = fm.resolve_stop_fill(
                direction="BUY" if long_ else "SELL",
                open_p=o, high=h, low=l, stop_level=stop, stop_distance=dist,
                symbol=instrument, slippage_pips=0.0, position_key=f"{instrument}:{i}",
            )
            r = ((fill - entry) if long_ else (entry - fill)) / dist
            return Outcome(r, "STOP", j, entry, stop, target)
        if target_hit:
            # A limit fills at its price or better: a bar that OPENS through the
            # target fills at that open. (Bar i+1 opens at the entry itself.)
            gapped = j > i + 1 and ((o >= target) if long_ else (o <= target))
            fill = o if gapped else target
            r = ((fill - entry) if long_ else (entry - fill)) / dist
            return Outcome(r, "TARGET", j, entry, stop, target)

    k = i + horizon
    mark = float(close[k])
    r = ((mark - entry) if long_ else (entry - mark)) / dist
    return Outcome(r, "TIMEOUT", k, entry, stop, target)


# ── the walk-forward ─────────────────────────────────────────────────────────

@dataclass
class ForecastRecord:
    series: str
    instrument: str
    decision_index: int
    decision_time: int
    decision_iso: str
    direction: str
    conviction: float
    horizon_stated: int
    r: float | None
    exit_reason: str
    exit_index: int | None
    exit_time: int | None
    context_hash: str | None
    abstain_reason: str | None
    operational_failure: bool
    provider: str

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def decision_indices(
    times: Sequence[int],
    *,
    cutoff_epoch: int,
    horizon: int,
    min_history: int,
    max_n: int | None = None,
) -> list[int]:
    """Post-cutoff decision bars, stepped by the horizon so trades cannot overlap."""
    n = len(times)
    first = next((k for k in range(n) if int(times[k]) >= cutoff_epoch), None)
    if first is None:
        return []
    start = max(first, int(min_history) - 1)
    out = list(range(start, n - horizon, horizon))   # guarantees i + horizon < n
    return out[:max_n] if max_n else out


def _as_arrays(d: dict[str, Any]) -> dict[str, Any]:
    out = {k: np.asarray(d[k], dtype=float) for k in ("open", "high", "low", "close")}
    out["time"] = np.asarray(d["time"], dtype=np.int64)
    out["volume"] = np.asarray(d["volume"], dtype=float) if d.get("volume") is not None else None
    return out


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


async def walk_forward(
    provider: Any,
    data: dict[str, dict[str, Any]],
    *,
    timeframe: str = "H1",
    cutoff: datetime = DEFAULT_POST_CUTOFF,
    horizon: int = 24,
    lookback: int = 400,
    min_history: int = 150,
    max_forecasts_per_instrument: int | None = None,
    series: str = "real",
    journal: ForecastJournal | None = None,
    row_extra: dict[str, Any] | None = None,
    concurrency: int = 1,
    fail_fast_after: int = 3,
    builder: ContextBuilder | None = None,
    fill_model: StopFillModel | None = None,
) -> list[ForecastRecord]:
    """One forecast per post-cutoff decision point, resolved and journalled."""
    cutoff_epoch = int(cutoff.timestamp())
    builder = builder or ContextBuilder()
    fm = fill_model or StopFillModel()
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    chunk = max(1, int(concurrency))
    records: list[ForecastRecord] = []

    for sym, raw in data.items():
        d = _as_arrays(raw)
        t, o, h, l, c, v = d["time"], d["open"], d["high"], d["low"], d["close"], d["volume"]
        idxs = decision_indices(t, cutoff_epoch=cutoff_epoch, horizon=horizon,
                                min_history=min_history, max_n=max_forecasts_per_instrument)
        cls = instrument_class_for(sym)

        async def one(i: int) -> ForecastRecord:
            if int(t[i]) < cutoff_epoch:  # structural, but never allowed to fail silently
                raise RuntimeError(f"{sym}: decision at {_iso(t[i])} precedes the cutoff")
            sl = slice(max(0, i + 1 - lookback), i + 1)  # ends AT the decision bar
            ctx = builder.build(
                instrument_class=cls, timeframe=timeframe,
                high=h[sl], low=l[sl], close=c[sl],
                volume=v[sl] if v is not None else None,
                session=detect_session(int(t[i])),
            )
            bars = Bars(open=o[sl], high=h[sl], low=l[sl], close=c[sl], decision_time=int(t[i]))
            if ctx is None:
                f = Forecast(instrument=sym, abstain_reason="context unavailable: insufficient history",
                             source=provider.name)
            else:
                async with sem:
                    try:
                        f = await provider.forecast(sym, ctx, bars)
                    except Exception as e:  # a provider must not take the run down
                        f = Forecast(instrument=sym, abstain_reason=f"provider call failed: {e}",
                                     source=provider.name)

            op = is_operational_failure(f)
            out = None if op else resolve_outcome(
                f, open_=o, high=h, low=l, close=c, decision_index=i,
                horizon=horizon, instrument=sym, fill_model=fm,
            )
            if op:
                reason = "OPERATIONAL_FAILURE"
            elif f.direction == "FLAT":
                reason = "ABSTAIN"
            elif out is None:
                reason = "UNRESOLVABLE"
            else:
                reason = out.exit_reason
            return ForecastRecord(
                series=series, instrument=sym, decision_index=int(i),
                decision_time=int(t[i]), decision_iso=_iso(t[i]),
                direction=f.direction, conviction=float(f.conviction or 0.0),
                horizon_stated=int(f.horizon_bars or horizon),
                r=None if out is None else float(out.r),
                exit_reason=reason,
                exit_index=None if out is None else int(out.exit_index),
                exit_time=None if out is None else int(t[out.exit_index]),
                context_hash=None if ctx is None else ctx.context_hash,
                abstain_reason=f.abstain_reason, operational_failure=op,
                provider=provider.name,
            )

        for k in range(0, len(idxs), chunk):
            batch = await asyncio.gather(*(one(i) for i in idxs[k:k + chunk]))
            for rec in batch:
                records.append(rec)
                if journal is not None:
                    journal.append({**rec.to_row(), **(row_extra or {})})
            if len(records) >= fail_fast_after and all(r.operational_failure for r in records):
                raise ProviderUnavailable(
                    f"the first {len(records)} forecasts all failed operationally — "
                    f"{records[0].abstain_reason}"
                )
    return records


# ── the surrogate control ────────────────────────────────────────────────────

def _actionable(records: Sequence[ForecastRecord]) -> list[ForecastRecord]:
    return [r for r in records
            if r.r is not None and r.direction in ("LONG", "SHORT") and not r.operational_failure]


def _surrogate_data(raw: dict[str, Any], *, seed: int, method: str) -> dict[str, Any]:
    d = _as_arrays(raw)
    sc = np.asarray(surrogate_series(d["close"], method=method, seed=seed), dtype=float)
    ratio = sc / d["close"]
    out = dict(d)
    out["close"] = sc
    out["open"] = d["open"] * ratio
    out["high"] = np.maximum(d["high"] * ratio, np.maximum(out["open"], sc))
    out["low"] = np.minimum(d["low"] * ratio, np.minimum(out["open"], sc))
    return out


def _seed_for(symbol: str, k: int, base: int) -> int:
    return int(base) * 1_000_003 + k * 1000 + int(hashlib.sha256(symbol.encode()).hexdigest()[:6], 16) % 1000


async def surrogate_control(
    provider: Any,
    data: dict[str, dict[str, Any]],
    *,
    real_records: Sequence[ForecastRecord],
    instruments: Sequence[str],
    n_surrogates: int,
    mode: str,
    method: str = "sign_flip",
    journal: ForecastJournal | None = None,
    row_extra: dict[str, Any] | None = None,
    bootstrap_draws: int = 2000,
    seed: int = 0,
    **wf_kwargs: Any,
) -> dict[str, Any]:
    instruments = [s for s in instruments if s in data]
    real_act = [r for r in _actionable(real_records) if r.instrument in instruments]
    base = {"ran": False, "mode": mode, "method": method,
            "n_surrogate_series": n_surrogates, "p_value": float("nan"), "beats_control": False}
    if not real_act:
        return {**base, "note": "no actionable real forecasts on the control instruments"}
    real_e = statistics.mean(r.r for r in real_act)

    series_means: list[float] = []
    pooled: list[float] = []
    for k in range(n_surrogates):
        sdata = {s: _surrogate_data(data[s], seed=_seed_for(s, k, seed), method=method)
                 for s in instruments}
        recs = await walk_forward(provider, sdata, series=f"surrogate:{k}",
                                  journal=journal, row_extra=row_extra, **wf_kwargs)
        act = _actionable(recs)
        pooled.extend(r.r for r in act)
        if act:
            series_means.append(statistics.mean(r.r for r in act))

    if mode == "full":
        dist = series_means
    elif mode == "bootstrap":
        if len(pooled) < 10:
            return {**base, "ran": True, "real_expectancy": real_e,
                    "note": f"only {len(pooled)} actionable surrogate forecasts — too few to bootstrap"}
        rng = np.random.default_rng(seed)
        arr = np.asarray(pooled, dtype=float)
        idx = rng.integers(0, len(arr), size=(int(bootstrap_draws), len(real_act)))
        dist = list(arr[idx].mean(axis=1))
    else:
        raise ValueError(f"unknown surrogate mode {mode!r} — use 'full' or 'bootstrap'")

    pc = permutation_control(real_e, dist)
    pc.update({
        "ran": True, "mode": mode, "method": method,
        "n_surrogate_series": n_surrogates,
        "surrogate_forecasts_actionable": len(pooled),
        "real_actionable_on_control_instruments": len(real_act),
        "control_instruments": list(instruments),
    })
    return pc


# ── planning and the verdict ─────────────────────────────────────────────────

def plan_run(
    provider: Any,
    data: dict[str, dict[str, Any]],
    *,
    cutoff: datetime = DEFAULT_POST_CUTOFF,
    horizon: int = 24,
    min_history: int = 150,
    max_forecasts_per_instrument: int | None = None,
    n_surrogates: int = 0,
    surrogate_instruments: Sequence[str] | None = None,
) -> dict[str, Any]:
    """How many provider calls a run makes, and roughly what they cost."""
    ce = int(cutoff.timestamp())
    per = {s: len(decision_indices(d["time"], cutoff_epoch=ce, horizon=horizon,
                                   min_history=min_history, max_n=max_forecasts_per_instrument))
           for s, d in data.items()}
    ctrl = [s for s in (surrogate_instruments or list(data)) if s in per]
    real = sum(per.values())
    sur = int(n_surrogates) * sum(per[s] for s in ctrl)
    unit = float(provider.estimate_call_cost_usd())
    return {
        "decisions_per_instrument": per,
        "real_calls": real,
        "surrogate_calls": sur,
        "total_calls": real + sur,
        "per_call_usd": round(unit, 6),
        "est_cost_usd": round(unit * (real + sur), 4),
        "note": "upper bound — assumes every decision reaches the provider; "
                "cache hits and unavailable contexts cost nothing",
    }


def verdict(
    *,
    n_actionable: int,
    significance_conservative: dict[str, Any],
    significance_independent: dict[str, Any],
    surrogate: dict[str, Any] | None,
) -> dict[str, Any]:
    if n_actionable < 30:
        return {"verdict": "INSUFFICIENT",
                "reasons": [f"only {n_actionable} actionable forecasts — nothing can be concluded"]}

    reasons: list[str] = []
    p = surrogate.get("p_value") if surrogate else float("nan")
    ran = bool(surrogate) and surrogate.get("ran", False) and not (isinstance(p, float) and math.isnan(p))
    real_e = surrogate.get("real_expectancy") if surrogate else None
    sur_mean = surrogate.get("surrogate_mean") if surrogate else None

    if not ran:
        reasons.append("surrogate control not run — no result can pass without it")
    elif not surrogate.get("beats_control"):
        reasons.append(f"inside the surrogate distribution (p={p:.3f}) — the pipeline "
                       f"produces this on structureless data")
    if significance_conservative.get("verdict") != "SIGNIFICANT":
        reasons.append("conservative significance (cross-instrument overlap counted): "
                       f"{significance_conservative.get('verdict', 'unavailable')}")
    n_ind = int(significance_independent.get("n_nonoverlap", 0) or 0)
    if n_ind < GATE_MIN_INDEPENDENT:
        reasons.append(f"{n_ind} independent actionable forecasts; the gate needs {GATE_MIN_INDEPENDENT}")

    if not reasons:
        return {"verdict": "PASS", "reasons": []}
    no_better = ran and real_e is not None and sur_mean is not None and real_e <= sur_mean
    non_positive = float(significance_independent.get("mean_r", 0.0) or 0.0) <= 0.0
    return {"verdict": "FAIL" if (no_better or non_positive) else "INCONCLUSIVE", "reasons": reasons}


# ── the whole test ───────────────────────────────────────────────────────────

@dataclass
class HarnessResult:
    run_id: str
    provider: str
    timeframe: str
    cutoff_iso: str
    horizon: int
    plan: dict[str, Any]
    records: list[ForecastRecord]
    per_instrument: dict[str, Any]
    score: dict[str, Any]
    significance: dict[str, Any]
    significance_independent: dict[str, Any]
    surrogate: dict[str, Any] | None
    verdict: dict[str, Any]
    cost: dict[str, Any]
    journal: dict[str, Any] | None

    def to_dict(self, include_records: bool = True) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k != "records"}
        if include_records:
            d["records"] = [r.to_row() for r in self.records]
        return d


def _per_instrument(records: Sequence[ForecastRecord]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sym in sorted({r.instrument for r in records}):
        rs = [r for r in records if r.instrument == sym]
        scored = [r for r in rs if not r.operational_failure]
        act = _actionable(scored)
        out[sym] = {
            "forecasts": len(scored),
            "operational_failures": len(rs) - len(scored),
            "actionable": len(act),
            "abstain_rate": round(1 - len(act) / len(scored), 4) if scored else None,
            "expectancy_r": round(statistics.mean(r.r for r in act), 4) if act else None,
            "win_rate": round(sum(r.r > 0 for r in act) / len(act), 4) if act else None,
            "total_r": round(sum(r.r for r in act), 3) if act else 0.0,
        }
    return out


def _spend_now() -> float:
    try:
        from backend.services.llm_budget import llm_budget
        return float(llm_budget.status()["cost_usd"])
    except Exception:
        return float("nan")


async def run_test(
    provider: Any,
    data: dict[str, dict[str, Any]],
    *,
    timeframe: str = "H1",
    cutoff: datetime = DEFAULT_POST_CUTOFF,
    horizon: int = 24,
    lookback: int = 400,
    min_history: int = 150,
    max_forecasts_per_instrument: int | None = None,
    n_surrogates: int = 30,
    surrogate_mode: str | None = None,
    surrogate_method: str = "sign_flip",
    surrogate_instruments: Sequence[str] | None = None,
    max_cost_usd: float = 0.0,
    journal_path: Path | str | None = None,
    n_trials: int = 1,
    concurrency: int = 1,
    seed: int = 0,
) -> HarnessResult:
    """Plan, refuse if over budget, run, control, score, and judge."""
    ctrl = list(surrogate_instruments or list(data))
    mode = surrogate_mode or ("bootstrap" if provider.costs_money else "full")
    plan = plan_run(provider, data, cutoff=cutoff, horizon=horizon, min_history=min_history,
                    max_forecasts_per_instrument=max_forecasts_per_instrument,
                    n_surrogates=n_surrogates, surrogate_instruments=ctrl)
    if provider.costs_money and plan["est_cost_usd"] > float(max_cost_usd):
        raise CostCeilingExceeded(
            f"planned {plan['total_calls']} calls at ~${plan['per_call_usd']:.4f} each = "
            f"${plan['est_cost_usd']:.2f}, above --max-cost ${float(max_cost_usd):.2f}. "
            f"Lower --max-forecasts, --surrogates or --surrogate-instruments, "
            f"or raise the ceiling deliberately."
        )

    run_id = f"{provider.name.replace(':', '_')}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    journal = ForecastJournal(Path(journal_path)) if journal_path else None
    extra = {"run_id": run_id}
    spend0 = _spend_now() if provider.costs_money else None

    wf = dict(timeframe=timeframe, cutoff=cutoff, horizon=horizon, lookback=lookback,
              min_history=min_history, max_forecasts_per_instrument=max_forecasts_per_instrument,
              concurrency=concurrency)
    records = await walk_forward(provider, data, series="real", journal=journal, row_extra=extra, **wf)

    surrogate = None
    if n_surrogates > 0:
        surrogate = await surrogate_control(
            provider, data, real_records=records, instruments=ctrl,
            n_surrogates=n_surrogates, mode=mode, method=surrogate_method,
            journal=journal, row_extra=extra, seed=seed, **wf,
        )

    scored = [r for r in records if not r.operational_failure]
    act = _actionable(scored)
    rs = [r.r for r in act]
    score = score_forecasts([r.conviction for r in act], rs, n_total_forecasts=len(scored)).to_dict()
    # Conservative: trades on DIFFERENT instruments over the same hours are
    # correlated bets, so time overlap is counted across instruments too. The
    # independent version treats each instrument's forecasts as independent;
    # the truth sits between the two, and the gate uses the conservative one.
    sig_cons = assess(rs, entries=[float(r.decision_time) for r in act],
                      exits=[float(r.exit_time) for r in act], n_trials=n_trials).to_dict()
    sig_ind = assess(rs, n_trials=n_trials).to_dict()

    cost: dict[str, Any] = {"planned_usd": plan["est_cost_usd"], "planned_calls": plan["total_calls"]}
    if provider.costs_money:
        spend1 = _spend_now()
        cost["actual_usd_booked_today"] = (round(spend1 - spend0, 4)
                                           if spend0 == spend0 and spend1 == spend1 else None)
    if hasattr(provider, "hits"):
        cost["cache_hits"] = provider.hits
        cost["cache_misses"] = provider.misses

    return HarnessResult(
        run_id=run_id, provider=provider.name, timeframe=timeframe,
        cutoff_iso=cutoff.isoformat(), horizon=horizon, plan=plan, records=records,
        per_instrument=_per_instrument(records), score=score,
        significance=sig_cons, significance_independent=sig_ind, surrogate=surrogate,
        verdict=verdict(n_actionable=len(act), significance_conservative=sig_cons,
                        significance_independent=sig_ind, surrogate=surrogate),
        cost=cost, journal=journal.verify() if journal else None,
    )
