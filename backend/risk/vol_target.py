"""
backend/risk/vol_target.py — [P5.1]

Volatility-targeted position sizing.

WHY THIS IS THE FIRST THING PHASE 5 SHIPS
-----------------------------------------
`research/24 §8`, verbatim:

    Put volatility clustering into position sizing. It is the only measured,
    robust, uncompeted structure this programme found. It requires no entry
    signal, no directional forecast, and does not depend on a parameter that
    flips sign by decade.

That study tested three hypotheses across 167 synthetic indices and 28 live
instruments on 330 million ticks. One directional candidate survived four
validation stages and then died on 57 years of data, flipping sign by decade.
Volatility clustering is what was left standing, and it was measured again
independently on 2026-09-11 from this repo's own bar archive:

    instrument      ACF(r²) lag 1     Ljung-Box Q(20)    (null threshold ~31)
    EURUSD  D1          0.117               621
    XAUUSD  D1          0.096               675
    GBPJPY  D1          0.046               122
    Crash 1000 M15      0.003                35   <- 30x smaller; not tradeable
    Boom  1000 M15      0.007                24   <- none

So: on real instruments, a quiet period really does predict a quiet period and
a violent one a violent one. That is not a directional edge — which is exactly
why nobody has arbitraged it away — but it is enough to size against.

WHAT IT DOES
------------
Fixed-fractional sizing (risk 1.8% of the account every time) makes your DOLLAR
risk constant but your *volatility* risk swing with the market: the same 1.8%
in a calm regime and in a violent one are not the same bet. Vol targeting scales
the fraction instead:

    scale = target_vol / realised_vol          (clamped, see below)
    risk%  = base_risk% * scale

In calm regimes it sizes up, in violent ones down. It does not predict returns.
It makes the risk you actually take match the risk you intended to take.

WHAT IT IS NOT
--------------
Not an edge. It cannot turn a negative-expectancy strategy positive — a losing
system sized better still loses. At best it reduces the variance of the equity
curve for a given mean return.

MEASURED VERDICT — DO NOT ENABLE THIS WITHOUT RE-TESTING IT YOURSELF
--------------------------------------------------------------------
research/24 §8 recommended this generically. Tested against THIS codebase on
live MT5 H1 data (6 instruments, ~239 trades each, edge-free alternating
entries so that only SIZING differs between the two curves), it did not deliver:

    stop style                          mean Sharpe      mean maxDD    improved
    ----------------------------------  ---------------  ------------  --------
    2 x ATR  (what AlgoEdge uses)       +0.07 -> -0.02    17.0% -> 20.3%   1 of 6
    fixed price distance                +0.27 -> +0.23    16.1% -> 18.7%   3 of 6

**The most likely reason is double-counting.** With an ATR-based stop,
`lots = risk_dollars / (k x ATR)`, so position size ALREADY scales as 1/vol.
Multiplying by another `target_vol / realised_vol` corrects the same exposure
twice. The 1-of-6 versus 3-of-6 split between the two stop styles is what that
mechanism predicts, and it is why the generic recommendation does not transfer:
research/24 did not check whether the book's stops were already vol-scaled.
Essentially every strategy here uses an ATR stop.

Drawdown was worse under BOTH stop styles. The clamp allows 2x risk in calm
regimes, and clustering — while real — is not perfect: the regime breaks are
where the losses are, and vol targeting has you maximally levered going into
them.

Caveat on the test: edge-free entries can only reveal the variance effect, and
the aggregate differences are inside noise on ~1,400 trades. So this is "not
demonstrated", not "disproved". `scripts/measure_vol_target.py` re-runs it
against YOUR strategies' actual trade sequence; use that before turning it on.

Shipped OFF by default (`vol_target_annual_pct=None`), fully tested, and left in
place because the measurement is cheap to repeat and the machinery is correct.
On Boom/Crash it would do approximately nothing regardless — there is no
clustering to exploit — and `clustering_strength()` says so rather than
pretending.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from backend.utils.logger import get_logger

logger = get_logger(__name__)

#: Annualisation factors — bars per year, per timeframe.
BARS_PER_YEAR = {
    "M1": 525_600, "M5": 105_120, "M15": 35_040, "M30": 17_520,
    "H1": 8_760, "H4": 2_190, "D1": 252,
}
DEFAULT_BARS_PER_YEAR = 35_040  # M15


@dataclass(frozen=True)
class VolTargetResult:
    """The scale factor and everything needed to justify it after the fact."""
    scale: float
    realised_vol_annual: float | None
    target_vol_annual: float
    base_risk_pct: float
    scaled_risk_pct: float
    binding: str  # "none" | "floor" | "ceiling" | "insufficient_data" | "disabled"

    def explain(self) -> str:
        if self.binding == "disabled":
            return "vol targeting off — sizing at the fixed fraction"
        if self.binding == "insufficient_data":
            return (f"vol targeting requested but not enough history to measure "
                    f"realised volatility — sizing at the fixed {self.base_risk_pct:.3f}%")
        rv = f"{self.realised_vol_annual * 100:.1f}%" if self.realised_vol_annual is not None else "?"
        note = {"floor": " (clamped at the floor)", "ceiling": " (clamped at the ceiling)",
                "none": ""}[self.binding]
        return (f"realised vol {rv} vs target {self.target_vol_annual * 100:.1f}% "
                f"-> scale {self.scale:.3f}{note}; "
                f"risk {self.base_risk_pct:.3f}% -> {self.scaled_risk_pct:.3f}%")


def realised_volatility(
    closes: Sequence[float],
    lookback: int = 20,
    timeframe: str = "M15",
) -> float | None:
    """Annualised realised volatility from the last `lookback` log returns.

    Returns None when there is not enough history, rather than a plausible-looking
    number computed from three bars — a silently-wrong volatility feeds straight
    into position size.
    """
    if closes is None or len(closes) < lookback + 1:
        return None
    # Accept numpy arrays as well as lists — callers pass MT5 rate arrays
    # directly and `float(x)` on a numpy scalar is fine, but truthiness is not.
    tail = [float(c) for c in list(closes)[-(lookback + 1):]]
    rets = []
    for a, b in zip(tail, tail[1:]):
        if a <= 0 or b <= 0:
            return None
        rets.append(math.log(b / a))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    per_bar = math.sqrt(var)
    return per_bar * math.sqrt(BARS_PER_YEAR.get(str(timeframe).upper(), DEFAULT_BARS_PER_YEAR))


def resolve_vol_scale(
    base_risk_pct: float,
    closes: Sequence[float] | None,
    *,
    target_vol_annual_pct: float | None,
    lookback: int = 20,
    timeframe: str = "M15",
    min_scale: float = 0.5,
    max_scale: float = 2.0,
    realised_vol_annual: float | None = None,
) -> VolTargetResult:
    """Scale `base_risk_pct` so realised volatility matches the target.

    `target_vol_annual_pct` of None or <= 0 disables it entirely and returns a
    scale of exactly 1.0 — the shipped default, so nothing changes for anyone who
    has not opted in.

    The clamps are not decoration. Unclamped, `target/realised` diverges as
    realised volatility approaches zero, and a quiet hour would size a position
    at many multiples of the intended risk right before the regime that ended
    the quiet. `min_scale`/`max_scale` bound the damage in both directions.
    """
    if not target_vol_annual_pct or target_vol_annual_pct <= 0:
        return VolTargetResult(1.0, None, 0.0, base_risk_pct, base_risk_pct, "disabled")

    target = target_vol_annual_pct / 100.0
    rv = realised_vol_annual
    if rv is None:
        # `closes or []` raises on a numpy array ("truth value of an array with
        # more than one element is ambiguous"), and bars arrive as numpy from
        # both MT5 and the backtester. Test for None explicitly.
        rv = realised_volatility(closes if closes is not None else [],
                                 lookback=lookback, timeframe=timeframe)
    if rv is None or rv <= 0:
        return VolTargetResult(1.0, None, target, base_risk_pct, base_risk_pct,
                               "insufficient_data")

    raw = target / rv
    scale = max(min_scale, min(max_scale, raw))
    binding = "none"
    if raw < min_scale:
        binding = "floor"
    elif raw > max_scale:
        binding = "ceiling"
    return VolTargetResult(scale, rv, target, base_risk_pct, base_risk_pct * scale, binding)


def clustering_strength(closes: Sequence[float], max_lag: int = 20) -> dict[str, Any]:
    """Ljung-Box Q on squared returns — is there clustering here to target?

    Quiet-then-violent shows up as autocorrelation in SQUARED returns. Q above
    roughly 31.4 (chi-square, 20 df, p=0.05) says clustering is present; the
    EFFECT SIZE (`acf_lag1`) says whether it is large enough to matter. Crash
    1000 clears the threshold on 100,000 bars at an ACF of 0.003, which is
    statistically detectable and economically nothing — so both numbers are
    returned and callers are expected to read the second.
    """
    if closes is None or len(closes) < max_lag + 30:
        return {"n": 0, "acf_lag1": None, "ljung_box_q": None,
                "clustering": False, "note": "not enough history"}
    px = [float(c) for c in closes]
    rets = [math.log(b / a) for a, b in zip(px, px[1:]) if a > 0 and b > 0]
    n = len(rets)
    r2 = [r * r for r in rets]
    m = sum(r2) / n
    dev = [x - m for x in r2]
    den = sum(d * d for d in dev)
    if den <= 0:
        return {"n": n, "acf_lag1": None, "ljung_box_q": None,
                "clustering": False, "note": "degenerate series"}
    acf = []
    for lag in range(1, max_lag + 1):
        num = sum(dev[i] * dev[i + lag] for i in range(n - lag))
        acf.append(num / den)
    q = n * (n + 2) * sum(a * a / (n - k - 1) for k, a in enumerate(acf, start=1))
    return {
        "n": n,
        "acf_lag1": acf[0],
        "max_abs_acf": max(abs(a) for a in acf),
        "ljung_box_q": q,
        "clustering": q > 31.4,
        "note": ("clustering present — vol targeting has something to work with"
                 if q > 31.4 else
                 "no clustering — vol targeting will do approximately nothing here"),
    }
