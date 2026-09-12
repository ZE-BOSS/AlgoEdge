"""
backend/analytics/forecast_context.py — [P3.1 / P3.2 / P3.6]

What the forecasting agent is allowed to see, and in what form.

THE PROBLEM THIS SOLVES
-----------------------
A model with a knowledge cutoff, asked what gold did on a date before that
cutoff, is recalling rather than forecasting. No amount of careful backtesting
fixes that — the leak is in the model's weights, not in the harness.

The mitigation with the best ratio of effort to certainty is **blinding**: strip
the things that let a model identify WHICH market and WHEN, and feed only the
shape. So the context is built once, in a normalised form, and then either
rendered plainly or blinded:

    dates       -> bar offsets ("t-48"), never calendar dates
    instrument  -> INSTRUMENT_A, and the class only when it is needed
    price       -> everything relative: ATR multiples, % of range, z-scores

**The accuracy gap between blinded and unblinded is a direct estimate of the
leakage**, which is why both renderings come from one builder rather than two
code paths that could drift. Measure the gap, report it, treat it as the error
bar on every historical result.

DETERMINISM
-----------
Same bars in, same context out, byte for byte — `context_hash` proves it. That
buys three things: a cache key, a reproducible forecast (the snapshot can be
replayed months later), and the ability to assert that a prompt change was
intentional rather than a silent feature drift.

SCOPE
-----
Pure functions over bar arrays. No I/O, no MT5, no clock. The adapter that
fetches bars lives elsewhere precisely so this stays testable offline and
identical between backtest and live — the divergence that cost Phase 1 weeks.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

#: Bump when the FEATURE SET changes in a way that makes old contexts
#: incomparable. Stored on every snapshot so a replay can refuse to mix versions.
CONTEXT_VERSION = "1.0.0"

Rendering = Literal["plain", "blinded"]


# ── small numerics, kept local so this module has no heavyweight imports ─────

def _sma(xs: Sequence[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _atr(high: Sequence[float], low: Sequence[float], close: Sequence[float],
         n: int = 14) -> float | None:
    if len(close) < n + 1:
        return None
    trs = []
    for i in range(len(close) - n, len(close)):
        pc = close[i - 1]
        trs.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return sum(trs) / len(trs) if trs else None


def _log_returns(close: Sequence[float]) -> list[float]:
    out = []
    for a, b in zip(close, close[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _stdev(xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(v) if v > 0 else None


def _acf1_squared(rets: Sequence[float]) -> float | None:
    """Lag-1 autocorrelation of squared returns — the volatility-clustering
    signature. Positive means quiet begets quiet."""
    if len(rets) < 30:
        return None
    r2 = [r * r for r in rets]
    m = sum(r2) / len(r2)
    dev = [x - m for x in r2]
    den = sum(d * d for d in dev)
    if den <= 0:
        return None
    num = sum(dev[i] * dev[i + 1] for i in range(len(dev) - 1))
    return num / den


def _percentile_rank(xs: Sequence[float], value: float) -> float | None:
    if not xs:
        return None
    return sum(1 for x in xs if x <= value) / len(xs)


# ── features ─────────────────────────────────────────────────────────────────

@dataclass
class ForecastContext:
    """Everything the agent sees, already normalised.

    Absolute prices live ONLY in `_private`, which is never rendered into a
    prompt. They are kept so the validator can convert a relative proposal back
    into real levels after the model has answered.
    """

    version: str
    instrument_class: str
    timeframe: str
    n_bars: int
    features: dict[str, Any]
    _private: dict[str, Any] = field(default_factory=dict, repr=False)

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def context_hash(self) -> str:
        """Stable over the rendered content only — `_private` is excluded, so two
        identical shapes at different price levels share a cache entry.

        Floats are ROUNDED first. Every feature here is a ratio, so the same
        market shape at $24 and at $2,400 produces values that agree to about
        1e-16 and no further; hashing the raw doubles made those two contexts
        different keys and the cache would have missed on almost every lookup
        while looking like it worked. 6 decimal places is far finer than
        anything the prompt renders.
        """
        payload = json.dumps(
            {"v": self.version, "cls": self.instrument_class,
             "tf": self.timeframe, "n": self.n_bars, "f": _round_floats(self.features)},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    # ── rendering ────────────────────────────────────────────────────────────
    def render(self, mode: Rendering = "blinded", instrument_name: str | None = None) -> str:
        """The prompt body.

        `blinded` (the default) names nothing and dates nothing. `plain` adds the
        instrument's real name — use it ONLY to measure the leakage gap against
        the blinded run, never as the production path.
        """
        label = "INSTRUMENT_A"
        if mode == "plain" and instrument_name:
            label = instrument_name
        lines = [
            f"instrument: {label}",
            f"asset_class: {self.instrument_class}",
            f"bar_interval: {self.timeframe}",
            f"bars_available: {self.n_bars}",
            "",
            "All levels are RELATIVE. `atr` = 1 ATR(14) of this instrument.",
            "Positive values are above the latest close, negative below.",
            "",
        ]
        for section, vals in self.features.items():
            lines.append(f"[{section}]")
            for k, v in vals.items():
                lines.append(f"  {k}: {_fmt(v)}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def snapshot(self) -> dict[str, Any]:
        """Reproducible record of exactly what produced a forecast."""
        return {
            "version": self.version,
            "context_hash": self.context_hash,
            "instrument_class": self.instrument_class,
            "timeframe": self.timeframe,
            "n_bars": self.n_bars,
            "features": self.features,
        }


def _round_floats(obj: Any, dp: int = 6) -> Any:
    """Recursively round floats so a hash is stable against last-bit noise.

    `round(-0.0, 6)` is `-0.0`, which serialises differently from `0.0`; the
    `+ 0.0` normalises it. Exactly the kind of detail that makes a cache key
    silently unstable.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return str(obj)
        return round(obj, dp) + 0.0
    if isinstance(obj, dict):
        return {k: _round_floats(v, dp) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, dp) for v in obj]
    return obj


def _fmt(v: Any) -> str:
    if v is None:
        return "unavailable"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:+.3f}" if abs(v) < 1000 else f"{v:+.1f}"
    return str(v)


@dataclass
class ContextBuilder:
    """Deterministic, versioned feature extraction over raw bars."""

    atr_period: int = 14
    vol_lookback: int = 24
    momentum_lookbacks: tuple[int, ...] = (12, 24, 48, 96)
    profile_lookback: int = 120
    profile_bins: int = 24
    value_area_pct: float = 0.70

    def build(
        self,
        *,
        instrument_class: str,
        timeframe: str,
        high: Sequence[float],
        low: Sequence[float],
        close: Sequence[float],
        volume: Sequence[float] | None = None,
        session: str | None = None,
        minutes_to_next_event: float | None = None,
        event_impact: str | None = None,
    ) -> ForecastContext | None:
        need = max(self.atr_period, self.vol_lookback, max(self.momentum_lookbacks)) + 2
        if len(close) < need:
            return None

        h = [float(x) for x in high]
        l = [float(x) for x in low]
        c = [float(x) for x in close]
        v = [float(x) for x in volume] if volume is not None else None
        last = c[-1]

        atr = _atr(h, l, c, self.atr_period)
        if not atr or atr <= 0 or last <= 0:
            return None

        rets = _log_returns(c)
        vol = _stdev(rets[-self.vol_lookback:]) if len(rets) >= self.vol_lookback else None

        features: dict[str, Any] = {}

        # ── trend / momentum, in volatility units so it is comparable across
        #    instruments and regimes rather than in raw percent ──
        mom: dict[str, Any] = {}
        for lb in self.momentum_lookbacks:
            if len(c) > lb and c[-(lb + 1)] > 0 and vol:
                r = math.log(last / c[-(lb + 1)])
                mom[f"return_{lb}bar_in_vol_units"] = r / (vol * math.sqrt(lb))
            else:
                mom[f"return_{lb}bar_in_vol_units"] = None
        for n in (20, 50):
            s = _sma(c, n)
            mom[f"close_vs_sma{n}_in_atr"] = (last - s) / atr if s else None
        features["trend"] = mom

        # ── volatility regime ──
        volf: dict[str, Any] = {"atr_pct_of_price": atr / last * 100.0}
        if vol:
            volf["realised_vol_per_bar_pct"] = vol * 100.0
            hist = [_stdev(rets[i - self.vol_lookback:i])
                    for i in range(self.vol_lookback, len(rets) + 1)]
            hist = [x for x in hist if x]
            volf["vol_percentile_vs_own_history"] = _percentile_rank(hist, vol)
            if len(hist) >= 10:
                vov = _stdev(hist[-min(len(hist), 60):])
                volf["vol_of_vol_pct"] = vov * 100.0 if vov else None
        volf["clustering_acf1_squared_returns"] = _acf1_squared(rets[-250:])
        features["volatility"] = volf

        # ── position within the recent range ──
        lb = min(self.profile_lookback, len(c))
        hi, lo = max(h[-lb:]), min(l[-lb:])
        rng = hi - lo
        features["range"] = {
            "lookback_bars": lb,
            "range_width_in_atr": rng / atr if atr else None,
            "position_in_range_0_to_1": (last - lo) / rng if rng > 0 else None,
            "distance_to_high_in_atr": (hi - last) / atr,
            "distance_to_low_in_atr": (last - lo) / atr,
        }

        # ── volume profile: where trade actually happened ──
        features["volume_profile"] = self._profile(h, l, c, v, last, atr, lb)

        # ── VWAP and its bands ──
        features["vwap"] = self._vwap(h, l, c, v, last, atr, lb)

        # ── order-flow proxy. Tick volume is not traded volume, and signing it
        #    by candle direction is an INFERENCE, not the tape. Labelled so the
        #    model cannot mistake it for real aggressor data. ──
        if v:
            signed = [vv if cc >= oo else -vv
                      for vv, cc, oo in zip(v[-lb:], c[-lb:], c[-lb - 1:-1])]
            tot = sum(abs(x) for x in signed)
            features["order_flow_proxy"] = {
                "note": "inferred from candle direction x tick volume, NOT true aggressor data",
                "cvd_imbalance_minus1_to_1": (sum(signed) / tot) if tot > 0 else None,
                "recent_20bar_imbalance": (sum(signed[-20:]) / sum(abs(x) for x in signed[-20:]))
                if len(signed) >= 20 and sum(abs(x) for x in signed[-20:]) > 0 else None,
            }

        # ── context the bars cannot carry ──
        ctxf: dict[str, Any] = {}
        if session:
            ctxf["session"] = session
        if minutes_to_next_event is not None:
            ctxf["minutes_to_next_high_impact_event"] = minutes_to_next_event
        if event_impact:
            ctxf["next_event_impact"] = event_impact
        if ctxf:
            features["context"] = ctxf

        return ForecastContext(
            version=CONTEXT_VERSION,
            instrument_class=instrument_class,
            timeframe=timeframe,
            n_bars=len(c),
            features=features,
            _private={"last_close": last, "atr": atr, "range_high": hi, "range_low": lo},
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _profile(self, h, l, c, v, last, atr, lb) -> dict[str, Any]:
        hi, lo = max(h[-lb:]), min(l[-lb:])
        if hi <= lo:
            return {"available": False}
        bins = self.profile_bins
        width = (hi - lo) / bins
        buckets = [0.0] * bins
        weights = v[-lb:] if v else [1.0] * lb
        for price, w in zip(c[-lb:], weights):
            idx = min(int((price - lo) / width), bins - 1)
            buckets[idx] += w
        total = sum(buckets)
        if total <= 0:
            return {"available": False}
        poc_i = max(range(bins), key=lambda i: buckets[i])
        # Grow outward from the POC until `value_area_pct` of activity is covered.
        lo_i = hi_i = poc_i
        acc = buckets[poc_i]
        while acc < self.value_area_pct * total and (lo_i > 0 or hi_i < bins - 1):
            down = buckets[lo_i - 1] if lo_i > 0 else -1.0
            up = buckets[hi_i + 1] if hi_i < bins - 1 else -1.0
            if up >= down:
                hi_i += 1
                acc += max(up, 0.0)
            else:
                lo_i -= 1
                acc += max(down, 0.0)
        return {
            "available": True,
            "poc_distance_in_atr": (lo + (poc_i + 0.5) * width - last) / atr,
            "vah_distance_in_atr": (lo + (hi_i + 1) * width - last) / atr,
            "val_distance_in_atr": (lo + lo_i * width - last) / atr,
            "inside_value_area": (lo + lo_i * width) <= last <= (lo + (hi_i + 1) * width),
            "weighted_by": "tick volume" if v else "bar count (no volume available)",
        }

    def _vwap(self, h, l, c, v, last, atr, lb) -> dict[str, Any]:
        tp = [(hh + ll + cc) / 3.0 for hh, ll, cc in zip(h[-lb:], l[-lb:], c[-lb:])]
        w = v[-lb:] if v else [1.0] * lb
        tw = sum(w)
        if tw <= 0:
            return {"available": False}
        vwap = sum(p * ww for p, ww in zip(tp, w)) / tw
        var = sum(ww * (p - vwap) ** 2 for p, ww in zip(tp, w)) / tw
        sd = math.sqrt(var) if var > 0 else 0.0
        return {
            "available": True,
            "anchor_bars": lb,
            "distance_to_vwap_in_atr": (vwap - last) / atr,
            "distance_to_vwap_in_sigma": ((last - vwap) / sd) if sd > 0 else None,
            "band_width_1sigma_in_atr": (sd / atr) if atr else None,
        }
