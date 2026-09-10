"""
backend/backtester/fill_model.py

Realistic stop-fill resolution for gap-prone instruments.

WHY THIS EXISTS
---------------
Both backtest engines used to resolve a breached stop like this:

    if bar.open already past the stop:  fill at the open (± slippage)
    else:                               fill at EXACTLY the stop price

The second branch is the defect. It only asks whether the bar *opened* through
the level, so a spike that happens INSIDE the bar — which on Boom/Crash is where
every spike happens, by construction — books a perfect fill at the stop.

Measured on the user's own runs (`Implementation/resources/backtest_run_*.json`,
SpikeFade on Crash 1000 + Boom 1000, 1–11 Sep 2026):

    hard-SL exits filled at exactly the stop price ...... 65 / 65   (100.0%)
    trades carrying gap_fill=True ....................... 0 / 132   (0.0%)

Three independent measurements of what the missing slippage is worth, as a
fraction of the trade's own stop distance:

    research/27 §1.2, 4,420 stop exits repriced on raw ticks
        Crash 1000  n=2,190  median 0.158  mean 0.403  p99 7.36  max 18.54
        Boom  1000  n=2,230  median 0.159  mean 0.353  p99 4.94  max 15.67
    user's live MT5 fills, 4 stopped trades on 2026-09-09
        mean +0.318 R  (one was +1.042 R: two Boom stops at 14679.71 and
        14704.35 both filled at the SAME price 14712.6564 — one tick took out
        both)
    the backtester
        0.000 R

Charging the measured mean to `backtest_run_cf9355c6` turns +15.87 R over 132
trades into −5.58 R (hard stops only) or −36.13 R (all stop-type exits). The
unbooked slippage is larger than the entire measured edge, which is why a
+19.9% backtest arrived as −$859 over two days on the live account.

DESIGN
------
Three modes, because the right answer depends on what is being measured and
because a hidden calibration is worse than an honest approximation:

    OFF          legacy — fill at the stop. Kept ONLY so historical runs can be
                 reproduced bit-for-bit. Never use it to evaluate a strategy.
    CONSERVATIVE (default) deterministic: charge the measured MEAN overshoot for
                 the instrument. No RNG, no distributional claim, reproducible,
                 and it reproduces the headline correction above exactly.
    EMPIRICAL    sample a per-instrument quantile table with a seeded RNG. Use
                 this for drawdown and tail-risk work, where the p99 of 7.36 R
                 is the number that matters. The shipped table is an
                 INTERPOLATION anchored on the published (p50, p99, max); it is
                 not itself a measurement. Regenerate it from your own ticks
                 with `scripts/harvest_stop_overshoot.py`.

Whatever the mode, the fill is CLAMPED to the bar's own extreme: price that
never traded cannot fill. That clamp is what makes this self-limiting — a bar
that merely grazed the stop fills at the stop, and only a bar that genuinely
travelled through it pays the overshoot.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from backend.risk.position_sizer import get_pip_size
from backend.utils.logger import get_logger

logger = get_logger(__name__)

MODE_OFF = "OFF"
MODE_CONSERVATIVE = "CONSERVATIVE"
MODE_EMPIRICAL = "EMPIRICAL"
_VALID_MODES = {MODE_OFF, MODE_CONSERVATIVE, MODE_EMPIRICAL}

DEFAULT_MODE = MODE_CONSERVATIVE


# ── Per-instrument overshoot, as a fraction of the trade's own stop distance ──
#
# `mean` drives CONSERVATIVE. `quantiles` drives EMPIRICAL and is a
# piecewise-linear inverse CDF: (probability, overshoot_fraction) pairs.
#
# Crash/Boom 1000 `mean`, `p50`, `p99` and `max` are measured (research/27 §1.2,
# 365 days of raw ticks). The intermediate quantiles are interpolated to sit
# between them — flagged `calibration: "interpolated"` so nothing downstream can
# mistake them for measurements.
#
# The 500-variants jump twice as often for the same magnitude distribution
# (research/24 §3.1: one shared spike-size distribution across the family, so
# the family is "a direction and a rate N"), so per-trade overshoot is taken as
# equal to their 1000 counterparts rather than invented.
#
# Non-jump instruments get a small class default. They are not gap-free — every
# instrument gaps at some point — but nothing here justifies a jump-sized number.

@dataclass(frozen=True)
class OvershootProfile:
    mean: float
    quantiles: tuple[tuple[float, float], ...]
    source: str
    calibration: str

    def sample(self, u: float) -> float:
        """Piecewise-linear inverse CDF at u ∈ [0, 1]."""
        qs = self.quantiles
        if u <= qs[0][0]:
            return qs[0][1]
        for (q0, v0), (q1, v1) in zip(qs, qs[1:]):
            if u <= q1:
                if q1 == q0:
                    return v1
                w = (u - q0) / (q1 - q0)
                return v0 + w * (v1 - v0)
        return qs[-1][1]


_JUMP_1000 = (
    (0.00, 0.000), (0.10, 0.010), (0.25, 0.045), (0.50, 0.158),
    (0.75, 0.300), (0.90, 0.620), (0.95, 1.050), (0.99, 7.360), (1.00, 18.540),
)
_JUMP_1000_BOOM = (
    (0.00, 0.000), (0.10, 0.010), (0.25, 0.045), (0.50, 0.159),
    (0.75, 0.300), (0.90, 0.580), (0.95, 0.950), (0.99, 4.940), (1.00, 15.670),
)
_GENERIC = (
    (0.00, 0.000), (0.50, 0.010), (0.90, 0.060), (0.99, 0.250), (1.00, 1.000),
)

_PROFILES: dict[str, OvershootProfile] = {
    "CRASH 1000 INDEX": OvershootProfile(
        0.403, _JUMP_1000, "research/27 §1.2 (n=2,190 tick-repriced stop exits)", "interpolated"),
    "CRASH 500 INDEX": OvershootProfile(
        0.403, _JUMP_1000, "research/27 §1.2, Crash 1000 profile (shared spike-size dist, research/24 §3.1)", "assumed"),
    "CRASH 300 INDEX": OvershootProfile(
        0.403, _JUMP_1000, "research/27 §1.2, Crash 1000 profile (shared spike-size dist, research/24 §3.1)", "assumed"),
    "BOOM 1000 INDEX": OvershootProfile(
        0.353, _JUMP_1000_BOOM, "research/27 §1.2 (n=2,230 tick-repriced stop exits)", "interpolated"),
    "BOOM 500 INDEX": OvershootProfile(
        0.353, _JUMP_1000_BOOM, "research/27 §1.2, Boom 1000 profile (shared spike-size dist, research/24 §3.1)", "assumed"),
    "BOOM 300 INDEX": OvershootProfile(
        0.353, _JUMP_1000_BOOM, "research/27 §1.2, Boom 1000 profile (shared spike-size dist, research/24 §3.1)", "assumed"),
}

# Jump N indices spike in both directions; Range Break spikes on the break.
# No tick study exists for these, so they get the jump profile with the mean
# halved rather than the generic one — an explicit, conservative guess.
_JUMPY_TOKENS = ("JUMP ", "RANGE BREAK")
_GENERIC_PROFILE = OvershootProfile(
    0.02, _GENERIC, "class default — no tick study", "assumed")


def get_overshoot_profile(symbol: str) -> OvershootProfile:
    key = (symbol or "").upper().strip()
    if key in _PROFILES:
        return _PROFILES[key]
    if any(tok in key for tok in _JUMPY_TOKENS):
        return OvershootProfile(
            0.20, _JUMP_1000_BOOM,
            "no tick study — jump-family profile, mean halved", "assumed")
    return _GENERIC_PROFILE


def _stable_u(seed: str, key: str) -> float:
    """Deterministic uniform in [0,1) from (run seed, position key).

    A seeded RNG stream would make a fill depend on how many OTHER positions
    closed first, so two runs differing only in an unrelated symbol would
    reprice the same trade. Hashing the position's own identity instead makes
    every fill a pure function of that trade.
    """
    h = hashlib.sha256(f"{seed}|{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


@dataclass
class StopFillModel:
    """Resolves the realistic fill for a stop breached during a bar."""

    mode: str = DEFAULT_MODE
    seed: str = "algoedge"
    # Populated as the run proceeds so report.py can state what was actually
    # charged rather than what was configured.
    stats: dict[str, Any] = None

    def __post_init__(self):
        m = (self.mode or DEFAULT_MODE).upper()
        if m not in _VALID_MODES:
            logger.warning(f"[FILL] unknown stop_fill_model '{self.mode}' — using {DEFAULT_MODE}")
            m = DEFAULT_MODE
        self.mode = m
        if self.stats is None:
            self.stats = {"n_stop_exits": 0, "n_gapped": 0, "sum_overshoot_r": 0.0,
                          "max_overshoot_r": 0.0, "by_symbol": {}}

    # ── the one entry point ────────────────────────────────────────────────
    def resolve_stop_fill(
        self,
        *,
        direction: str,
        open_p: float,
        high: float,
        low: float,
        stop_level: float,
        stop_distance: float,
        symbol: str,
        slippage_pips: float,
        position_key: str,
    ) -> tuple[float, bool, float]:
        """Return (fill_price, gapped, overshoot_in_R).

        `stop_distance` is the trade's ORIGINAL risk (|entry − initial stop|),
        which is what the tick study measured overshoot against. Pass 0 and the
        model degrades to the bar-open check alone rather than guessing.
        """
        is_buy = str(direction).upper().startswith("B")
        pip = get_pip_size(symbol) or 0.0
        slip = max(0.0, float(slippage_pips or 0.0)) * pip

        # 1. The bar OPENED through the stop. Nothing was tradeable at the stop
        #    at any point in this bar, so the open is the first available price.
        #    This branch is the pre-existing behaviour and is always correct.
        opened_through = (open_p <= stop_level) if is_buy else (open_p >= stop_level)
        if opened_through:
            fill = open_p - slip if is_buy else open_p + slip
            fill = max(fill, low) if is_buy else min(fill, high)
            ov = self._overshoot_r(is_buy, fill, stop_level, stop_distance)
            self._record(symbol, True, ov)
            return fill, True, ov

        if self.mode == MODE_OFF or stop_distance <= 0:
            fill = stop_level
            self._record(symbol, False, 0.0)
            return fill, False, 0.0

        # 2. The stop was breached INSIDE the bar. How far past it did the first
        #    tick through the level land?
        profile = get_overshoot_profile(symbol)
        if self.mode == MODE_CONSERVATIVE:
            frac = profile.mean
        else:
            frac = profile.sample(_stable_u(self.seed, position_key))

        overshoot = frac * stop_distance
        raw = (stop_level - overshoot) if is_buy else (stop_level + overshoot)

        # 3. Clamp to what actually traded. A bar that merely grazed the stop
        #    fills at the stop; only a bar that genuinely travelled through it
        #    pays. This is what stops the model inventing prices.
        fill = max(raw, low) if is_buy else min(raw, high)
        # Never better than the stop itself — this is an adverse fill model.
        fill = min(fill, stop_level) if is_buy else max(fill, stop_level)

        if slip > 0:
            fill = fill - slip if is_buy else fill + slip
            fill = max(fill, low) if is_buy else min(fill, high)

        ov = self._overshoot_r(is_buy, fill, stop_level, stop_distance)
        gapped = ov > 1e-9
        self._record(symbol, gapped, ov)
        return fill, gapped, ov

    # ── bookkeeping ────────────────────────────────────────────────────────
    @staticmethod
    def _overshoot_r(is_buy: bool, fill: float, stop_level: float, stop_distance: float) -> float:
        if stop_distance <= 0:
            return 0.0
        raw = (stop_level - fill) if is_buy else (fill - stop_level)
        return max(0.0, raw / stop_distance)

    def _record(self, symbol: str, gapped: bool, overshoot_r: float) -> None:
        s = self.stats
        s["n_stop_exits"] += 1
        if gapped:
            s["n_gapped"] += 1
        s["sum_overshoot_r"] += overshoot_r
        s["max_overshoot_r"] = max(s["max_overshoot_r"], overshoot_r)
        b = s["by_symbol"].setdefault(symbol, {"n": 0, "gapped": 0, "sum_r": 0.0, "max_r": 0.0})
        b["n"] += 1
        b["gapped"] += 1 if gapped else 0
        b["sum_r"] += overshoot_r
        b["max_r"] = max(b["max_r"], overshoot_r)

    def summary(self) -> dict[str, Any]:
        """What the run actually charged — goes into the report, not the config."""
        s = self.stats
        n = s["n_stop_exits"] or 1
        out = {
            "mode": self.mode,
            "seed": self.seed,
            "stop_exits": s["n_stop_exits"],
            "gapped_exits": s["n_gapped"],
            "gapped_pct": round(100.0 * s["n_gapped"] / n, 2),
            "mean_overshoot_r": round(s["sum_overshoot_r"] / n, 4),
            "max_overshoot_r": round(s["max_overshoot_r"], 4),
            "total_overshoot_r": round(s["sum_overshoot_r"], 4),
            "by_symbol": {},
        }
        for sym, b in s["by_symbol"].items():
            bn = b["n"] or 1
            prof = get_overshoot_profile(sym)
            out["by_symbol"][sym] = {
                "stop_exits": b["n"],
                "gapped_pct": round(100.0 * b["gapped"] / bn, 2),
                "mean_overshoot_r": round(b["sum_r"] / bn, 4),
                "max_overshoot_r": round(b["max_r"], 4),
                "profile_mean": prof.mean,
                "profile_source": prof.source,
                "profile_calibration": prof.calibration,
            }
        return out


def build_stop_fill_model(risk_config: dict[str, Any] | None, seed: str = "algoedge") -> StopFillModel:
    """Construct from a risk_config, honouring `stop_fill_model` / `stop_fill_seed`."""
    rc = risk_config or {}
    return StopFillModel(
        mode=str(rc.get("stop_fill_model") or DEFAULT_MODE),
        seed=str(rc.get("stop_fill_seed") or seed),
    )
