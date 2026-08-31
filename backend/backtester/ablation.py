"""
backend/backtester/ablation.py

Confluence ablation harness — Phase 3 of the strategy-optimization plan.

WHAT THIS ANSWERS
-----------------
For every confluence ("gate") a strategy applies:

  * How often does it fire, and how often does it BLOCK a candidate?
  * Of the setups that pass it, how far does price actually run in our favour,
    measured in R (multiples of the setup's own initial stop)?
  * What fraction reach 1R / 2R / 3R before giving back the stop?
  * How long does it take to get there?
  * What happens to expectancy if the gate is REMOVED?

The first four come from a single instrumented pass ("record once"). The last
needs a re-run with that gate disabled, which `run_ablation` does only for
gates that actually block something — a gate that never blocks cannot change
the outcome, so re-running it would be pure waste.

WHY MFE-IN-R IS THE RIGHT UNIT
------------------------------
"Does a wick rejection go the distance?" is a question about forward
excursion, not about win rate. Win rate depends on where the TP happens to be
set; MFE-in-R is a property of the setup itself. Ten pips on EURGBP and ten
pips on XAUUSD are different risks, so pips cannot be pooled across symbols —
R can.

DATA FLOW
---------
    run_recording_pass()   -> [Observation]   (one per candidate setup)
    summarize()            -> per-gate table
    run_ablation()         -> baseline vs gate-disabled expectancy
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Same warmup/window table the API's signal loop uses, so the harness sees
# exactly the data a real backtest would.
TF_META = {
    "M1":  {"np_td": (1, "m"),  "window": 500},
    "M5":  {"np_td": (5, "m"),  "window": 500},
    "M15": {"np_td": (15, "m"), "window": 300},
    "M30": {"np_td": (30, "m"), "window": 200},
    "H1":  {"np_td": (1, "h"),  "window": 200},
    "H4":  {"np_td": (4, "h"),  "window": 200},
    "D1":  {"np_td": (1, "D"),  "window": 100},
}
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

# How far forward to measure excursion. 288 M5 bars = 24h.
DEFAULT_FORWARD_BARS = 288


@dataclass
class Observation:
    """One candidate setup, its gate vector, and what price did next."""
    symbol: str
    strategy_id: str
    bar_index: int
    time: str
    emitted: bool
    blocking_gate: str | None
    gate_vector: dict[str, bool] = field(default_factory=dict)

    direction: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    risk_price: float | None = None

    # Forward excursion, in R
    mfe_r: float | None = None
    mae_r: float | None = None
    bars_to_mfe: int | None = None
    hit_1r: bool | None = None
    hit_2r: bool | None = None
    hit_3r: bool | None = None
    # Did MAE reach -1R (i.e. would a plain stop have been hit) before MFE peaked?
    stopped_before_mfe: bool | None = None


def _forward_excursion(
    highs: np.ndarray,
    lows: np.ndarray,
    i: int,
    direction: str,
    entry: float,
    stop: float,
    forward_bars: int,
) -> dict[str, Any]:
    """
    Maximum favourable / adverse excursion over the next `forward_bars`,
    normalised by the setup's own risk distance.

    Entry is assumed to fill at the OPEN of bar i+1, matching the engine's
    next-bar fill rule — so excursion is measured from i+1 onward and never
    includes the signal bar itself (which would be look-ahead).
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return {}

    lo = i + 1
    hi = min(len(highs), lo + forward_bars)
    if lo >= hi:
        return {}

    seg_h = highs[lo:hi]
    seg_l = lows[lo:hi]

    if direction == "BUY":
        fav = (seg_h - entry) / risk
        adv = (entry - seg_l) / risk
    else:
        fav = (entry - seg_l) / risk
        adv = (seg_h - entry) / risk

    # Running max of favourable excursion, and the first bar at which the
    # adverse side reached a full R (a plain stop-out).
    mfe_idx = int(np.argmax(fav))
    mfe = float(fav[mfe_idx])
    mae = float(np.max(adv))

    stop_hits = np.nonzero(adv >= 1.0)[0]
    first_stop = int(stop_hits[0]) if len(stop_hits) else None

    def reached(level: float) -> bool:
        """Did favourable excursion reach `level` R BEFORE the stop was hit?"""
        hits = np.nonzero(fav >= level)[0]
        if not len(hits):
            return False
        if first_stop is None:
            return True
        return int(hits[0]) <= first_stop

    return {
        "mfe_r": round(mfe, 4),
        "mae_r": round(mae, 4),
        "bars_to_mfe": mfe_idx + 1,
        "hit_1r": reached(1.0),
        "hit_2r": reached(2.0),
        "hit_3r": reached(3.0),
        "stopped_before_mfe": (first_stop is not None and first_stop < mfe_idx),
    }


async def run_recording_pass(
    strategy_id: str,
    symbol: str,
    candles_by_tf: dict[str, pd.DataFrame],
    disabled_gates: set[str] | None = None,
    forward_bars: int = DEFAULT_FORWARD_BARS,
    config: Any = None,
) -> tuple[list[Observation], dict[str, Any]]:
    """
    Walk the bars once with gate telemetry on, recording every candidate.

    `candles_by_tf` must be time-indexed and sorted, keyed by timeframe name —
    the same structure the API builds before its signal loop.
    """
    from backend.core.config_schema import InstrumentSettings, UserConfigV2
    from backend.strategies.registry import get_strategy

    if config is None:
        config = UserConfigV2()
        config.instrument_settings = [
            InstrumentSettings(symbol=symbol, strategy_id=strategy_id)
        ]

    strat = get_strategy(strategy_id)(config)
    strat.is_backtesting = True
    strat.gates.enabled = True
    strat.gates.disabled_gates = set(disabled_gates or ())

    required = [tf for tf in strat.get_required_timeframes() if tf in candles_by_tf]
    if not required:
        raise ValueError(f"No data for {strategy_id}'s timeframes on {symbol}")

    primary_tf = sorted(required, key=lambda t: TF_MINUTES.get(t, 999))[0]
    primary = candles_by_tf[primary_tf]
    primary_times = primary.index.values
    tf_times = {tf: candles_by_tf[tf].index.values for tf in required}
    prev_seen: dict[str, Any] = {tf: None for tf in required}

    highs = primary["high"].to_numpy(dtype=float)
    lows = primary["low"].to_numpy(dtype=float)

    observations: list[Observation] = []

    for i in range(300, len(primary_times)):
        current_time = primary_times[i]
        sig = None

        for tf in required:
            meta = TF_META.get(tf, TF_META["M5"])
            series = candles_by_tf[tf]
            times = tf_times[tf]

            if tf == primary_tf:
                tf_end = i
                last_tf_time = current_time
            else:
                np_td, np_unit = meta["np_td"]
                cutoff = current_time - np.timedelta64(np_td, np_unit)
                tf_end = int(np.searchsorted(times, cutoff, side="right"))
                last_tf_time = times[tf_end - 1] if tf_end > 0 else None

            if last_tf_time is None or last_tf_time == prev_seen[tf]:
                continue

            sl = series.iloc[max(0, tf_end - meta["window"]): tf_end]
            if len(sl) < 20:
                continue

            try:
                s = await strat.on_bar(symbol, tf, sl)
                if s:
                    sig = s
            except Exception as e:
                logger.debug(f"[ablation] on_bar error {symbol} {tf} bar {i}: {e}")
            prev_seen[tf] = last_tf_time

        # Close the candidate for this bar and turn it into an Observation.
        rec = strat.gates._current
        if rec is None or not rec.events:
            continue

        obs = Observation(
            symbol=symbol,
            strategy_id=strategy_id,
            bar_index=i,
            time=str(pd.Timestamp(current_time)),
            emitted=sig is not None,
            blocking_gate=rec.blocking_gate,
            gate_vector=rec.vector(),
        )

        if sig is not None:
            obs.direction = getattr(sig, "direction", None)
            obs.entry_price = getattr(sig, "entry_price", None)
            obs.stop_loss = getattr(sig, "stop_loss", None)
            if obs.entry_price and obs.stop_loss and obs.direction:
                obs.risk_price = abs(obs.entry_price - obs.stop_loss)
                exc = _forward_excursion(
                    highs, lows, i, obs.direction,
                    obs.entry_price, obs.stop_loss, forward_bars,
                )
                for k, v in exc.items():
                    setattr(obs, k, v)

        observations.append(obs)

    strat.gates.finish()
    return observations, strat.gates.summary()


def summarize_gates(
    observations: list[Observation],
    gate_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Per-gate predictive table.

    For each gate, the population is the candidates that REACHED it (it appears
    in their gate_vector). Excursion stats are computed over the emitted
    signals within that population, since only those have an entry and stop.
    """
    rows = []
    gates = gate_summary.get("gates", {})

    for name, g in gates.items():
        reached = [o for o in observations if name in o.gate_vector]
        passed = [o for o in reached if o.gate_vector[name]]
        emitted_passed = [o for o in passed if o.emitted and o.mfe_r is not None]

        def frac(key: str) -> float | None:
            vals = [getattr(o, key) for o in emitted_passed if getattr(o, key) is not None]
            return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

        mfes = sorted(o.mfe_r for o in emitted_passed if o.mfe_r is not None)
        maes = sorted(o.mae_r for o in emitted_passed if o.mae_r is not None)
        bars = [o.bars_to_mfe for o in emitted_passed if o.bars_to_mfe is not None]

        def pct(xs, p):
            if not xs:
                return None
            return round(float(np.percentile(xs, p)), 3)

        rows.append({
            "gate": name,
            "evaluated": g["evaluated"],
            "passed": g["passed"],
            "pass_rate": round(g["pass_rate"], 4),
            "blocked_candidates": g["blocked_candidates"],
            "signals_after_pass": len(emitted_passed),
            "mfe_r_median": pct(mfes, 50),
            "mfe_r_p75": pct(mfes, 75),
            "mfe_r_p90": pct(mfes, 90),
            "mae_r_median": pct(maes, 50),
            "p_hit_1r": frac("hit_1r"),
            "p_hit_2r": frac("hit_2r"),
            "p_hit_3r": frac("hit_3r"),
            "median_bars_to_mfe": int(np.median(bars)) if bars else None,
            # A gate that never blocks cannot change any outcome. Flagging it
            # here saves an entire ablation re-run per dead gate.
            "is_dead": g["blocked_candidates"] == 0,
        })

    rows.sort(key=lambda r: -r["blocked_candidates"])
    return rows


def baseline_stats(observations: list[Observation]) -> dict[str, Any]:
    """Population-level excursion stats for the emitted signals of a run."""
    em = [o for o in observations if o.emitted and o.mfe_r is not None]
    if not em:
        return {"signals": 0}
    mfes = [o.mfe_r for o in em]
    return {
        "signals": len(em),
        "mfe_r_median": round(float(np.median(mfes)), 3),
        "mfe_r_mean": round(float(np.mean(mfes)), 3),
        "p_hit_1r": round(sum(1 for o in em if o.hit_1r) / len(em), 4),
        "p_hit_2r": round(sum(1 for o in em if o.hit_2r) / len(em), 4),
        "p_hit_3r": round(sum(1 for o in em if o.hit_3r) / len(em), 4),
        # Crude expectancy proxy: win at 1R vs lose 1R. Not a substitute for a
        # full engine run (no costs, no partial TPs), but it ranks gates
        # consistently and costs nothing.
        "expectancy_1r": round(
            2 * (sum(1 for o in em if o.hit_1r) / len(em)) - 1, 4
        ),
    }


def observations_to_frame(observations: list[Observation]) -> pd.DataFrame:
    """Flatten to a DataFrame with one boolean column per gate."""
    if not observations:
        return pd.DataFrame()
    base = [
        {k: v for k, v in asdict(o).items() if k != "gate_vector"}
        for o in observations
    ]
    df = pd.DataFrame(base)
    all_gates = sorted({g for o in observations for g in o.gate_vector})
    for g in all_gates:
        df[f"gate__{g}"] = [o.gate_vector.get(g) for o in observations]
    return df
