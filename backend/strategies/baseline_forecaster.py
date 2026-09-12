"""
backend/strategies/baseline_forecaster.py — [P3.7]

The number the LLM has to beat.

WHY A BASELINE COMES BEFORE THE AGENT
-------------------------------------
Phase 3's gate (3.14) is "200 non-overlapping blinded forward forecasts beating
the baseline on EXPECTANCY". Without a baseline that sentence is unfalsifiable,
and an agent measured only against zero will look impressive for reasons that
have nothing to do with forecasting: it will inherit whatever drift, carry or
momentum is sitting in the instrument, and report it as skill.

So this is deliberately cheap, deliberately public, and deliberately hard to
beat for the right reasons. Two ingredients, both of which are the most robust
findings in the empirical finance literature and neither of which requires a
model to have any opinion:

  1. **Time-series momentum.** The sign of the trailing N-bar return. Survives
     out of sample across asset classes and decades better than almost anything
     else, and costs one subtraction.
  2. **A volatility-regime filter.** Stand aside when realised volatility is in
     its own top decile — the regime where stops are widest, spreads are worst
     and the measured edge of everything degrades.

It emits the SAME `Forecast` contract the LLM agent will, so the two can be
scored by identical code. That matters: a comparison where the two sides are
scored by different harnesses is not a comparison.

WHAT "BEATING" THIS MEANS
-------------------------
Not higher accuracy. Expectancy. An agent that is right 70% of the time on
trades that pay 0.3 R and wrong 30% of the time on trades that lose 1 R is worse
than a coin flip, and accuracy would hide that.

Abstention counts. `direction=FLAT` is a real answer and most 15-minute windows
deserve it; a forecaster that always has a view is pattern-matching on the
prompt, not reading the market.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

Direction = Literal["LONG", "SHORT", "FLAT"]


@dataclass
class Forecast:
    """The contract. The LLM agent returns exactly this shape, so the baseline
    and the agent can be scored by the same code and compared honestly."""

    instrument: str
    direction: Direction = "FLAT"
    conviction: float = 0.0            # 0..1, calibrated — see §1.2 of the plan
    horizon_bars: int = 24
    entry_zone: tuple[float, float] | None = None
    invalidation: float | None = None
    targets: list[dict[str, float]] = field(default_factory=list)
    expected_move_atr: float = 0.0
    regime: str = "UNKNOWN"
    primary_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    abstain_reason: str | None = None
    source: str = "baseline"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.entry_zone is not None:
            d["entry_zone"] = list(self.entry_zone)
        return d

    @property
    def is_actionable(self) -> bool:
        return self.direction != "FLAT" and self.conviction > 0


def _atr(high: Sequence[float], low: Sequence[float], close: Sequence[float],
         n: int = 14) -> float | None:
    if len(close) < n + 1:
        return None
    trs = []
    for i in range(len(close) - n, len(close)):
        pc = close[i - 1]
        trs.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return sum(trs) / len(trs) if trs else None


def _realised_vol(close: Sequence[float], lookback: int) -> float | None:
    if len(close) < lookback + 1:
        return None
    tail = list(close)[-(lookback + 1):]
    rets = []
    for a, b in zip(tail, tail[1:]):
        if a <= 0 or b <= 0:
            return None
        rets.append(math.log(b / a))
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) if var > 0 else None


def _rolling_vol_history(close: Sequence[float], lookback: int) -> list[float]:
    """Every rolling `lookback`-bar return stdev in the series, in O(n).

    Rolling mean and mean-of-squares from prefix sums, then
    var = E[x²] − E[x]². Numerically fine here because log returns are small and
    centred; the naive two-pass version is O(n·lookback) per call.
    """
    px = [float(x) for x in close]
    if len(px) < lookback + 2:
        return []
    rets: list[float] = []
    for a, b in zip(px, px[1:]):
        if a <= 0 or b <= 0:
            return []
        rets.append(math.log(b / a))
    if len(rets) < lookback:
        return []
    cs, cs2 = [0.0], [0.0]
    for r in rets:
        cs.append(cs[-1] + r)
        cs2.append(cs2[-1] + r * r)
    out: list[float] = []
    for i in range(lookback, len(rets) + 1):
        s = cs[i] - cs[i - lookback]
        s2 = cs2[i] - cs2[i - lookback]
        mean = s / lookback
        var = (s2 / lookback) - mean * mean
        # Sample variance, and guard the tiny negatives floating point produces
        # when the window is essentially constant.
        var = var * lookback / (lookback - 1)
        if var > 0:
            out.append(math.sqrt(var))
    return out


@dataclass
class BaselineForecaster:
    """Vol-filtered time-series momentum. No parameters were tuned on anything;
    these are textbook values, which is the point — a baseline you optimised is
    not a baseline, it is another competitor with a head start."""

    momentum_lookback: int = 48
    vol_lookback: int = 24
    atr_period: int = 14
    horizon_bars: int = 24
    stop_atr: float = 2.0
    target_rr: float = 2.0
    #: Stand aside above this percentile of the instrument's own recent vol.
    vol_percentile_cutoff: float = 0.90
    #: Below this |momentum| in vol units there is nothing to lean on.
    min_momentum_z: float = 0.5

    def forecast(
        self,
        instrument: str,
        high: Sequence[float],
        low: Sequence[float],
        close: Sequence[float],
    ) -> Forecast:
        need = max(self.momentum_lookback, self.vol_lookback, self.atr_period) + 2
        if len(close) < need:
            return Forecast(instrument, abstain_reason=f"need {need} bars, got {len(close)}")

        px = [float(x) for x in close]
        atr = _atr([float(x) for x in high], [float(x) for x in low], px, self.atr_period)
        vol = _realised_vol(px, self.vol_lookback)
        if not atr or not vol or atr <= 0 or vol <= 0:
            return Forecast(instrument, abstain_reason="volatility not measurable")

        # 1. Volatility-regime filter. Stand aside in the instrument's own top
        #    decile — widest stops, worst spreads, and where measured edges decay.
        #
        # Computed with rolling sums, O(n). The obvious version — calling
        # _realised_vol once per historical index — is O(n x lookback) PER
        # FORECAST, which on 5,000 bars is ~10^9 operations and made a single
        # scan of eight instruments take minutes. The live scan loop would have
        # paid that on every cycle.
        hist = _rolling_vol_history(px, self.vol_lookback)
        if len(hist) >= 30:
            hist_sorted = sorted(hist)
            cutoff = hist_sorted[min(int(len(hist_sorted) * self.vol_percentile_cutoff),
                                     len(hist_sorted) - 1)]
            if vol > cutoff:
                return Forecast(instrument, regime="EXPANDING",
                                abstain_reason="realised volatility in its own top decile")

        # 2. Time-series momentum, expressed in units of volatility so it is
        #    comparable across instruments and across regimes.
        past = px[-(self.momentum_lookback + 1)]
        if past <= 0:
            return Forecast(instrument, abstain_reason="bad price history")
        mom = math.log(px[-1] / past)
        z = mom / (vol * math.sqrt(self.momentum_lookback))

        if abs(z) < self.min_momentum_z:
            return Forecast(instrument, regime="RANGING", conviction=0.0,
                            abstain_reason=f"momentum {z:+.2f} inside the "
                                           f"±{self.min_momentum_z} dead band")

        direction: Direction = "LONG" if z > 0 else "SHORT"
        entry = px[-1]
        risk = self.stop_atr * atr
        invalidation = entry - risk if direction == "LONG" else entry + risk
        target = entry + self.target_rr * risk if direction == "LONG" else entry - self.target_rr * risk

        # Conviction saturates: beyond ~2 vol units more momentum is not more
        # information, and an unbounded score would dominate every risk tier.
        conviction = min(1.0, abs(z) / 2.0)

        return Forecast(
            instrument=instrument,
            direction=direction,
            conviction=round(conviction, 4),
            horizon_bars=self.horizon_bars,
            entry_zone=(entry, entry),
            invalidation=invalidation,
            targets=[{"level": target, "probability": 1.0 / (1.0 + self.target_rr)}],
            expected_move_atr=round(abs(z) * self.stop_atr, 3),
            regime="TRENDING",
            primary_evidence=[
                f"{self.momentum_lookback}-bar momentum {z:+.2f} vol units",
                f"realised vol {vol * 100:.3f}% per bar, below the top-decile cutoff",
            ],
            contradicting_evidence=(
                ["momentum is the only input; no regime or event context is considered"]
            ),
            source="baseline",
        )
