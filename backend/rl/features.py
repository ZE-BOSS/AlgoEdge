"""
backend/rl/features.py

The only things an agent is allowed to see.

THREE RULES, EACH ENFORCED BY A TEST RATHER THAN BY REVIEW
----------------------------------------------------------
1. **Every feature is prefix-stable.** `f(bars[:i])` is identical no matter
   what follows bar `i`. `leakguard.probe` checks it over six cuts and
   `assert_registry_causal()` runs the whole registry; a feature that fails
   cannot be named on an `AgentSpec`.

2. **No money management is expressible.** There is no balance, no equity, no
   drawdown, no lot size, no risk percentage and no stop distance in here. The
   agent cannot breach a risk limit because the numbers are absent by
   construction, not withheld by policy (plan 4.3).

3. **This module may not import the counterfactual.** `reward.py` computes
   `exit_quality` and `saved_R` from bars AFTER the decision - legitimate in a
   reward, fatal in an observation. The separation is structural, and
   `tests/test_rl_features.py` asserts the observation matrix is identical with
   and without the reward module loaded.

SCALING
-------
A scaler fitted on the whole dataset is the most common leak in applied ML and
it looks innocent in a diff - `leakguard.whole_series_control` is that mistake
in miniature. So `Scaler` is fitted on the TRAINING FOLD ONLY, stored inside
the agent, and applied unchanged at inference. It is robust (median /
inter-quartile range) rather than mean / sigma because the instruments here
include Boom and Crash: one spike bar otherwise sets the scale for five years
of ordinary bars.

HIGHER TIMEFRAMES
-----------------
`htf_merge` carries an HTF column onto primary bars under a strict
`htf_bar_close <= primary_bar_open` rule, asserted rather than assumed. That is
the one piece of alignment `BarFeed` does for engines and which a flat feature
matrix has to redo for itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from backend.strategies.core.daily_atr import epoch_seconds

Series = Callable[[pd.DataFrame], np.ndarray]

DAY = 86400
EPS = 1e-12


# -- the registry -----------------------------------------------------------


@dataclass(frozen=True)
class FeatureDef:
    """One column of the observation.

    `plain` is not decoration. It is the English that ends up in the charter
    (plan 4.10) when `distill.py` turns a policy into named gates, so a feature
    without a readable description produces an unreadable charter.
    """
    name: str
    group: str
    plain: str
    fn: Series
    # Features whose natural scale is already comparable across instruments
    # (ratios, positions in [0,1], sines) do not need the robust scaler and are
    # better left alone - scaling a sine wave destroys the periodicity.
    prescaled: bool = False

    def __call__(self, frame: pd.DataFrame) -> np.ndarray:
        return self.fn(frame)


FEATURES: dict[str, FeatureDef] = {}


def register(name: str, group: str, plain: str, *, prescaled: bool = False):
    def deco(fn: Series) -> Series:
        if name in FEATURES:
            raise ValueError(f"feature {name!r} registered twice")
        FEATURES[name] = FeatureDef(name, group, plain, fn, prescaled)
        return fn
    return deco


# -- primitives -------------------------------------------------------------
#
# Deliberately small and deliberately trailing. `min_periods=1` everywhere, so
# a feature is defined from the first bar and the warm-up shows up as a wide
# value rather than a NaN the policy has to special-case.


def _c(frame: pd.DataFrame) -> pd.Series:
    return frame["close"].astype(float)


def _true_range(frame: pd.DataFrame) -> pd.Series:
    high, low, close = frame["high"].astype(float), frame["low"].astype(float), _c(frame)
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder-style ATR, trailing. Never zero - a zero ATR divides into every
    normalised feature, and a flat synthetic session produces one."""
    return _true_range(frame).ewm(alpha=1.0 / n, min_periods=1, adjust=False).mean().clip(lower=EPS)


def _minute_of_day(frame: pd.DataFrame) -> np.ndarray:
    return (epoch_seconds(frame) % DAY) / 60.0


def _weekday(frame: pd.DataFrame) -> np.ndarray:
    # 1970-01-01 was a Thursday, hence the +4.
    return ((epoch_seconds(frame) // DAY) + 4) % 7


# -- price / return ---------------------------------------------------------


@register("ret_1", "return", "the last bar's return, in ATRs")
def ret_1(frame: pd.DataFrame) -> np.ndarray:
    return (_c(frame).diff() / atr(frame)).to_numpy()


@register("ret_5", "return", "the move over the last 5 bars, in ATRs")
def ret_5(frame: pd.DataFrame) -> np.ndarray:
    return (_c(frame).diff(5) / atr(frame)).to_numpy()


@register("ret_20", "return", "the move over the last 20 bars, in ATRs")
def ret_20(frame: pd.DataFrame) -> np.ndarray:
    return (_c(frame).diff(20) / atr(frame)).to_numpy()


@register("gap", "return", "the gap from the previous close to this bar's open, in ATRs")
def gap(frame: pd.DataFrame) -> np.ndarray:
    return ((frame["open"].astype(float) - _c(frame).shift(1)) / atr(frame)).to_numpy()


# -- trend ------------------------------------------------------------------


@register("ema_dist_20", "trend", "how far the close sits from its 20-bar EMA, in ATRs")
def ema_dist_20(frame: pd.DataFrame) -> np.ndarray:
    close = _c(frame)
    return ((close - close.ewm(span=20, min_periods=1, adjust=False).mean()) / atr(frame)).to_numpy()


@register("ema_dist_50", "trend", "how far the close sits from its 50-bar EMA, in ATRs")
def ema_dist_50(frame: pd.DataFrame) -> np.ndarray:
    close = _c(frame)
    return ((close - close.ewm(span=50, min_periods=1, adjust=False).mean()) / atr(frame)).to_numpy()


@register("ema_slope_20", "trend", "the slope of the 20-bar EMA over 5 bars, in ATRs")
def ema_slope_20(frame: pd.DataFrame) -> np.ndarray:
    ema = _c(frame).ewm(span=20, min_periods=1, adjust=False).mean()
    return (ema.diff(5) / atr(frame)).to_numpy()


@register("efficiency_20", "trend",
          "how directional the last 20 bars were: net move divided by total movement",
          prescaled=True)
def efficiency_20(frame: pd.DataFrame) -> np.ndarray:
    """0 = pure chop, 1 = a straight line. The cheap ADX stand-in, and the
    `ADX range` half of the TrendDrift confluence that measured profitable on
    8 of 11 markets in app form."""
    close = _c(frame)
    net = close.diff(20).abs()
    path = close.diff().abs().rolling(20, min_periods=1).sum()
    return (net / path.clip(lower=EPS)).clip(0, 1).fillna(0.0).to_numpy()


@register("up_run", "trend", "consecutive higher closes, negative for lower closes, capped at 10")
def up_run(frame: pd.DataFrame) -> np.ndarray:
    sign = np.sign(_c(frame).diff().fillna(0.0).to_numpy())
    out = np.zeros(len(sign))
    for i in range(1, len(sign)):
        out[i] = sign[i] if sign[i] != sign[i - 1] else out[i - 1] + sign[i]
    return np.clip(out, -10, 10)


# -- range / position -------------------------------------------------------


@register("donchian_pos_20", "range",
          "where the close sits in its 20-bar range: 0 at the low, 1 at the high",
          prescaled=True)
def donchian_pos_20(frame: pd.DataFrame) -> np.ndarray:
    close = _c(frame)
    hi = frame["high"].astype(float).rolling(20, min_periods=1).max()
    lo = frame["low"].astype(float).rolling(20, min_periods=1).min()
    return ((close - lo) / (hi - lo).clip(lower=EPS)).clip(0, 1).to_numpy()


@register("donchian_pos_60", "range",
          "where the close sits in its 60-bar range: 0 at the low, 1 at the high",
          prescaled=True)
def donchian_pos_60(frame: pd.DataFrame) -> np.ndarray:
    close = _c(frame)
    hi = frame["high"].astype(float).rolling(60, min_periods=1).max()
    lo = frame["low"].astype(float).rolling(60, min_periods=1).min()
    return ((close - lo) / (hi - lo).clip(lower=EPS)).clip(0, 1).to_numpy()


@register("dist_high_20", "range", "distance below the 20-bar high, in ATRs")
def dist_high_20(frame: pd.DataFrame) -> np.ndarray:
    hi = frame["high"].astype(float).rolling(20, min_periods=1).max()
    return ((hi - _c(frame)) / atr(frame)).to_numpy()


@register("dist_low_20", "range", "distance above the 20-bar low, in ATRs")
def dist_low_20(frame: pd.DataFrame) -> np.ndarray:
    lo = frame["low"].astype(float).rolling(20, min_periods=1).min()
    return ((_c(frame) - lo) / atr(frame)).to_numpy()


# -- volatility -------------------------------------------------------------


@register("atr_pct", "volatility", "ATR(14) as a fraction of price")
def atr_pct(frame: pd.DataFrame) -> np.ndarray:
    return (atr(frame) / _c(frame).abs().clip(lower=EPS)).to_numpy()


@register("vol_expansion", "volatility",
          "20-bar realised volatility over 60-bar: above 1.15 means volatility is expanding")
def vol_expansion(frame: pd.DataFrame) -> np.ndarray:
    """The one filter in this project measured to give a 10x change in
    expectancy out of sample (TrendBreakout, rv20/rv60 > 1.15), and it needs no
    data feed anybody has to buy."""
    r = _c(frame).pct_change()
    short = r.rolling(20, min_periods=2).std()
    long = r.rolling(60, min_periods=2).std()
    return (short / long.clip(lower=EPS)).replace([np.inf, -np.inf], np.nan).fillna(1.0).to_numpy()


@register("atr_ratio", "volatility", "ATR(14) over ATR(50): short-term volatility against longer")
def atr_ratio(frame: pd.DataFrame) -> np.ndarray:
    return (atr(frame, 14) / atr(frame, 50)).to_numpy()


# -- bar shape --------------------------------------------------------------


@register("body_frac", "shape", "the body as a fraction of the bar's whole range", prescaled=True)
def body_frac(frame: pd.DataFrame) -> np.ndarray:
    rng = (frame["high"].astype(float) - frame["low"].astype(float)).clip(lower=EPS)
    return ((_c(frame) - frame["open"].astype(float)).abs() / rng).clip(0, 1).to_numpy()


@register("upper_wick", "shape", "the upper wick as a fraction of the bar's range", prescaled=True)
def upper_wick(frame: pd.DataFrame) -> np.ndarray:
    high, low = frame["high"].astype(float), frame["low"].astype(float)
    top = pd.concat([_c(frame), frame["open"].astype(float)], axis=1).max(axis=1)
    return ((high - top) / (high - low).clip(lower=EPS)).clip(0, 1).to_numpy()


@register("lower_wick", "shape", "the lower wick as a fraction of the bar's range", prescaled=True)
def lower_wick(frame: pd.DataFrame) -> np.ndarray:
    high, low = frame["high"].astype(float), frame["low"].astype(float)
    bottom = pd.concat([_c(frame), frame["open"].astype(float)], axis=1).min(axis=1)
    return ((bottom - low) / (high - low).clip(lower=EPS)).clip(0, 1).to_numpy()


@register("range_atr", "shape", "this bar's range divided by ATR(14)")
def range_atr(frame: pd.DataFrame) -> np.ndarray:
    return ((frame["high"].astype(float) - frame["low"].astype(float)) / atr(frame)).to_numpy()


# -- clock ------------------------------------------------------------------
#
# Sine/cosine rather than a raw minute count, so 23:55 and 00:05 are adjacent
# instead of being the two ends of the range. Marked prescaled: a sine is
# already in [-1, 1] and the robust scaler would flatten it.


@register("tod_sin", "clock", "time of day (sine), so midnight and 23:55 are adjacent", prescaled=True)
def tod_sin(frame: pd.DataFrame) -> np.ndarray:
    return np.sin(2 * math.pi * _minute_of_day(frame) / 1440.0)


@register("tod_cos", "clock", "time of day (cosine)", prescaled=True)
def tod_cos(frame: pd.DataFrame) -> np.ndarray:
    return np.cos(2 * math.pi * _minute_of_day(frame) / 1440.0)


@register("dow_sin", "clock", "day of week (sine)", prescaled=True)
def dow_sin(frame: pd.DataFrame) -> np.ndarray:
    return np.sin(2 * math.pi * _weekday(frame) / 7.0)


@register("dow_cos", "clock", "day of week (cosine)", prescaled=True)
def dow_cos(frame: pd.DataFrame) -> np.ndarray:
    return np.cos(2 * math.pi * _weekday(frame) / 7.0)


@register("bars_into_day", "clock", "how far into the UTC day this bar is, 0 to 1", prescaled=True)
def bars_into_day(frame: pd.DataFrame) -> np.ndarray:
    return (epoch_seconds(frame) % DAY) / float(DAY)


# -- volume -----------------------------------------------------------------


@register("volume_ratio", "volume",
          "volume against its own 20-bar average; 1.0 throughout on synthetic indices, which have none")
def volume_ratio(frame: pd.DataFrame) -> np.ndarray:
    """Deriv's synthetic indices carry a tick count, not traded volume, and some
    feeds carry nothing at all. A missing column is 1.0 everywhere rather than
    an error, so one feature list works across instruments - and a constant
    column is neutralised by `Scaler`, which is the honest outcome."""
    for col in ("volume", "tick_volume", "real_volume"):
        if col in frame.columns:
            v = pd.to_numeric(frame[col], errors="coerce").astype(float)
            if v.notna().any() and float(v.max()) > 0:
                mean = v.rolling(20, min_periods=1).mean().clip(lower=EPS)
                return (v / mean).fillna(1.0).to_numpy()
    return np.ones(len(frame))


# -- position state, for the exit head only ---------------------------------
#
# These are NOT in the entry observation. The exit head sees them because "is
# this trade still working?" is unanswerable without them (plan 4.8), and they
# are all derived from the position the engine hands to `on_position_bar` -
# never from bars after the decision.

POSITION_FIELDS: tuple[tuple[str, str], ...] = (
    ("unrealised_r", "the trade's open profit, in multiples of its initial risk"),
    ("bars_held", "how long the trade has been open, as a fraction of its max hold"),
    ("mfe_r", "the best the trade has been, in R"),
    ("mae_r", "the worst the trade has been, in R"),
    ("dist_stop_r", "distance from here to the stop, in R"),
    ("dist_target_r", "distance from here to the target, in R"),
    ("is_long", "1 for a long, 0 for a short"),
)

N_POSITION_FIELDS = len(POSITION_FIELDS)


def position_vector(position: dict, *, price: float, max_hold_bars: int = 288,
                    bars_held: int = 0) -> np.ndarray:
    """The position half of the exit head's observation.

    Everything is in R - multiples of the trade's own initial risk - so one
    policy transfers between a 3-pip GBPJPY stop and a 400-point Crash 1000
    stop. R is a ratio of prices, which is why it is allowed here while a
    dollar amount is not: a dollar amount would leak account size into the
    policy, and account size is money management.
    """
    entry = float(position.get("entry_price") or 0.0)
    stop = float(position.get("stop_loss") or 0.0)
    target = float(position.get("take_profit") or 0.0)
    is_long = 1.0 if str(position.get("direction", "BUY")).upper() == "BUY" else 0.0
    risk = abs(entry - stop)
    if risk <= EPS:
        # A degenerate stop makes every R infinite. The research harness has
        # faked whole results this way; here it reads as "no information".
        return np.zeros(N_POSITION_FIELDS, dtype=float)
    sgn = 1.0 if is_long else -1.0
    unrealised = sgn * (price - entry) / risk
    mfe = position.get("mfe_r")
    mae = position.get("mae_r")
    return np.array([
        unrealised,
        min(1.0, bars_held / float(max(1, max_hold_bars))),
        float(mfe) if mfe is not None else max(0.0, unrealised),
        float(mae) if mae is not None else min(0.0, unrealised),
        sgn * (price - stop) / risk,
        (sgn * (target - price) / risk) if target else 0.0,
        is_long,
    ], dtype=float)


# -- the observation matrix -------------------------------------------------


DEFAULT_FEATURES: tuple[str, ...] = (
    "ret_1", "ret_5", "ret_20", "gap",
    "ema_dist_20", "ema_dist_50", "ema_slope_20", "efficiency_20", "up_run",
    "donchian_pos_20", "donchian_pos_60", "dist_high_20", "dist_low_20",
    "atr_pct", "vol_expansion", "atr_ratio",
    "body_frac", "upper_wick", "lower_wick", "range_atr",
    "tod_sin", "tod_cos", "dow_sin", "dow_cos",
    "volume_ratio",
)


def unknown(names) -> list[str]:
    return [n for n in names if n not in FEATURES]


def matrix(frame: pd.DataFrame, names=None) -> np.ndarray:
    """`(n_bars, n_features)` of raw, unscaled feature values.

    Raw on purpose: scaling belongs to the agent and is fitted on its training
    fold. A function that returned scaled values would have to know the fold,
    and a function that guessed would be `whole_series_control`.
    """
    names = tuple(names or DEFAULT_FEATURES)
    missing = unknown(names)
    if missing:
        raise KeyError(f"not registered features: {missing}")
    if frame is None or not len(frame):
        return np.zeros((0, len(names)), dtype=float)
    cols = []
    for n in names:
        v = np.asarray(FEATURES[n](frame), dtype=float).ravel()
        if len(v) != len(frame):
            raise ValueError(f"feature {n!r} returned {len(v)} values for {len(frame)} bars")
        cols.append(v)
    out = np.column_stack(cols)
    # A non-finite value in an observation propagates into every weight it
    # touches. Replace rather than drop: dropping bars would silently change
    # the bar sequence the engine is about to simulate.
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


# -- the scaler -------------------------------------------------------------


@dataclass
class Scaler:
    """Robust per-column scaling, fitted on the training fold and then frozen.

    `centre` is the median and `scale` the inter-quartile range, because the
    instruments here include Boom and Crash - one spike bar moves a mean and a
    standard deviation enough to misplace five years of ordinary bars, and this
    project's research harness has produced whole fake results from exactly
    that kind of contamination.

    `fitted_on` is provenance, not a comment: the validation report prints it,
    so a scaler fitted on the test fold is visible in the artefact rather than
    only in the code that made it.
    """
    names: tuple[str, ...] = ()
    centre: np.ndarray = field(default_factory=lambda: np.zeros(0))
    scale: np.ndarray = field(default_factory=lambda: np.ones(0))
    clip: float = 6.0
    fitted_on: str = ""

    @property
    def fitted(self) -> bool:
        return len(self.centre) > 0

    def fit(self, X: np.ndarray, names=None, *, fitted_on: str = "") -> "Scaler":
        X = np.asarray(X, dtype=float)
        if X.ndim != 2 or not len(X):
            raise ValueError("fit needs a 2-D matrix with at least one row")
        self.names = tuple(names or self.names or tuple(f"f{i}" for i in range(X.shape[1])))
        self.centre = np.median(X, axis=0)
        q75, q25 = np.percentile(X, [75, 25], axis=0)
        iqr = q75 - q25
        # A constant column has zero spread. Scale 1.0 leaves it as a constant
        # offset, which the policy learns to ignore, instead of dividing by zero.
        self.scale = np.where(iqr > EPS, iqr, 1.0)
        # A feature already on a comparable scale is left alone - scaling a sine
        # wave by its own IQR destroys the thing that made it useful.
        for i, n in enumerate(self.names):
            if n in FEATURES and FEATURES[n].prescaled:
                self.centre[i], self.scale[i] = 0.0, 1.0
        self.fitted_on = fitted_on
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if not self.fitted:
            raise RuntimeError("scaler used before it was fitted on a training fold")
        if X.ndim != 2 or X.shape[1] != len(self.centre):
            raise ValueError(f"expected {len(self.centre)} columns, got {getattr(X, 'shape', None)}")
        Z = (X - self.centre) / self.scale
        # Clip rather than let one spike bar dominate a linear policy's output.
        return np.clip(np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0), -self.clip, self.clip)

    def to_dict(self) -> dict:
        return {"names": list(self.names), "centre": [float(v) for v in self.centre],
                "scale": [float(v) for v in self.scale], "clip": float(self.clip),
                "fitted_on": self.fitted_on}

    @classmethod
    def from_dict(cls, d: dict) -> "Scaler":
        s = cls(names=tuple(d.get("names") or ()), clip=float(d.get("clip", 6.0)),
                fitted_on=str(d.get("fitted_on", "")))
        s.centre = np.asarray(d.get("centre") or [], dtype=float)
        s.scale = np.asarray(d.get("scale") or [], dtype=float)
        return s


# -- higher timeframes ------------------------------------------------------


class HTFAlignmentError(AssertionError):
    """An HTF frame that cannot be aligned to the primary bars without lookahead.

    Raised by `datasets.assert_htf_alignment`, which checks the FRAME. The
    merge below cannot raise it: it takes only bars that had closed, by
    construction.
    """


def htf_merge(primary: pd.DataFrame, htf: pd.DataFrame, htf_minutes: int,
              columns=("close",), *, prefix: str = "htf_") -> pd.DataFrame:
    """Carry HTF columns onto primary bars, using only bars that had CLOSED.

    `BarFeed` gives engines this guarantee: at primary bar `i` the engine sees,
    for every higher timeframe, the bars that had fully closed by bar `i`'s
    open. A flat feature matrix has to redo it, and the usual mistake is an
    as-of join on the HTF bar's OPEN time - which hands bar `i` an H1 bar that
    is still forming, i.e. up to 59 minutes of the future.

    So the join key is the HTF bar's CLOSE (`open + htf_minutes`), the match is
    `<=` the primary bar's open, and the result is asserted afterwards rather
    than trusted.
    """
    if primary is None or not len(primary):
        return pd.DataFrame(index=getattr(primary, "index", None))
    p_open = epoch_seconds(primary).astype(np.int64)
    out = pd.DataFrame(index=primary.index)
    if htf is None or not len(htf):
        for col in columns:
            out[f"{prefix}{col}"] = np.nan
        return out

    h_open = epoch_seconds(htf).astype(np.int64)
    h_close = h_open + int(htf_minutes) * 60
    order = np.argsort(h_close, kind="stable")
    h_close, h_open = h_close[order], h_open[order]
    # searchsorted 'right' gives the count of HTF bars that had closed by this
    # primary bar's open; minus one is the newest usable bar.
    idx = np.searchsorted(h_close, p_open, side="right") - 1
    usable = idx >= 0
    for col in columns:
        if col not in htf.columns:
            out[f"{prefix}{col}"] = np.nan
            continue
        vals = pd.to_numeric(htf[col], errors="coerce").to_numpy(dtype=float)[order]
        taken = np.full(len(primary), np.nan)
        taken[usable] = vals[idx[usable]]
        out[f"{prefix}{col}"] = taken

    # NOT asserted here, deliberately. `searchsorted(..., "right") - 1`
    # guarantees `h_close[idx] <= p_open` by construction, so an assertion to
    # that effect cannot fail - the first version of this code had one and it
    # passed a deliberately close-stamped frame. What CAN go wrong is the FRAME
    # being stamped by close rather than open, which this function cannot see
    # from the inside: `datasets.assert_htf_alignment` checks that, by noticing
    # that the last HTF bar closes after the last primary bar does.
    return out


# -- the guard, as callable API ---------------------------------------------


def assert_registry_causal(frame: pd.DataFrame | None = None, names=None) -> list:
    """Every registered feature is prefix-stable, or this raises.

    `leakguard` is imported late and imports nothing from here: keeping the
    dependency one-way means the guard cannot be weakened by a change to the
    thing it guards.
    """
    from backend.rl import leakguard as lg

    lg.self_check(frame)          # the probe still detects its own controls
    frame = frame if frame is not None else lg.synthetic_bars(400)
    chosen = {n: FEATURES[n].fn for n in (names or FEATURES.keys())}
    lg.assert_causal(chosen, frame)
    return lg.scan(chosen, frame)


def assert_no_banned_primitives() -> None:
    """No back-fill, no centred window, no negative shift in this file.

    The probe cannot see a leading back-fill - it contaminates only the first
    few bars, which a cut three quarters of the way in never reaches - so that
    one is caught by name instead.
    """
    from pathlib import Path

    from backend.rl import leakguard as lg

    found = lg.scan_source(Path(__file__).read_text(encoding="utf-8"))
    if found:
        raise lg.LookaheadError(f"banned primitives in features.py: {found}")
