"""
backend/risk/giveback.py

"It was 3R up and it gave it all back."

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
A hard rule: once a trade has been `arm_at_r` in profit, do not let it give
back more than `give_back_pct` of its best. It is evaluated on every price
update rather than once a bar, because the thing it protects against happens
inside a bar.

It is **not an agent and not learned**, and that is the whole design. A learned
per-tick exit cannot be backtested here: `BarFeed` guarantees that backtest and
live see the identical bar sequence, and there is no tick-level equivalent of
that guarantee. An untestable exit is how a backtest stops predicting live.
This rule is three numbers and a comparison, so it needs no backtest to be
understood - you can read it and know exactly what it will do.

WHY IT IS OFF BY DEFAULT
------------------------
Because this project has measured what a well-meaning protective exit does to a
real edge: break-even at 1.5R turned 16 of 113 BTCUSD trades into BE_SL exits
the research never took, and trailing was actively harmful on several shipped
strategies. A give-back rule is the same class of intervention. `arm_at_r`
defaults to 2.0 and `give_back_pct` to 50 so that, when it IS switched on, it
only ever touches trades that are already well in profit - it cannot turn a
winner into a loser, only a big winner into a smaller one.

The honest way to use it: turn it on, and compare. It is per slot, so you can.
"""

from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-12
REASON = "GIVE_BACK"


@dataclass(frozen=True)
class GiveBackRule:
    """Three numbers. Off unless `enabled`."""
    enabled: bool = False
    # Peak profit, in R, before the rule does anything at all. Below this the
    # trade is left entirely alone.
    arm_at_r: float = 2.0
    # How much of the PEAK may be given back once armed, as a percent.
    give_back_pct: float = 50.0
    # Never let the rule close a trade for less than this, whatever the
    # arithmetic says. A floor against a peak measured off a bad tick.
    floor_r: float = 0.0

    @classmethod
    def from_config(cls, cfg) -> "GiveBackRule":
        """Read it off a slot config or a risk dict - both shapes appear."""
        def get(name, default):
            if isinstance(cfg, dict):
                v = cfg.get(name)
            else:
                v = getattr(cfg, name, None)
            return default if v is None else v

        return cls(
            enabled=bool(get("giveback_enabled", False)),
            arm_at_r=float(get("giveback_arm_at_r", 2.0) or 2.0),
            give_back_pct=float(get("giveback_pct", 50.0) or 50.0),
            floor_r=float(get("giveback_floor_r", 0.0) or 0.0),
        )

    @property
    def keep_fraction(self) -> float:
        """What fraction of the peak the trade must hold on to."""
        return max(0.0, 1.0 - float(self.give_back_pct) / 100.0)

    def trigger_r(self, peak_r: float) -> float | None:
        """The R at which this trade should be closed, or None if unarmed."""
        if not self.enabled:
            return None
        peak = float(peak_r)
        if peak < float(self.arm_at_r):
            return None
        return max(float(self.floor_r), peak * self.keep_fraction)

    def should_close(self, peak_r: float, current_r: float) -> bool:
        """The whole decision, on one line of arithmetic."""
        level = self.trigger_r(peak_r)
        return level is not None and float(current_r) <= level + EPS

    def explain(self, peak_r: float, current_r: float) -> str:
        level = self.trigger_r(peak_r)
        if level is None:
            return (f"not armed: peak {float(peak_r):.2f}R is below "
                    f"{self.arm_at_r:.2f}R")
        return (f"peaked at {float(peak_r):.2f}R, now {float(current_r):.2f}R, "
                f"keeping at least {level:.2f}R "
                f"({self.give_back_pct:.0f}% give-back allowed)")


def r_of(position: dict, price: float) -> float | None:
    """A position's open profit in R, from its ORIGINAL risk.

    The original stop, never the current one. `stop_loss` is mutated in place
    by break-even and trailing, so by exit time it typically sits at entry -
    and dividing by that collapsed denominator is the measured bug that once
    reported +243.51R on a plain 1.5R target.
    """
    entry = float(position.get("entry_price") or 0.0)
    original = (position.get("initial_stop_loss")
                or (position.get("original_signal") or {}).get("stop_loss")
                or position.get("stop_loss"))
    try:
        stop = float(original or 0.0)
    except (TypeError, ValueError):
        return None
    risk = abs(entry - stop)
    if not entry or risk <= EPS:
        return None
    sgn = 1.0 if str(position.get("direction", "BUY")).upper() == "BUY" else -1.0
    return sgn * (float(price) - entry) / risk


def peak_r(position: dict, price: float) -> float:
    """The best this trade has been, in R, including right now.

    Prefers the engine's own recorded `mfe_r` and falls back to the current
    value. A position that has never been tracked reads as "its peak is now",
    which cannot arm the rule on a trade that was never in profit.
    """
    now = r_of(position, price)
    recorded = position.get("mfe_r")
    try:
        best = float(recorded) if recorded is not None else None
    except (TypeError, ValueError):
        best = None
    if now is None:
        return best if best is not None else 0.0
    return max(now, best) if best is not None else now


def check(position: dict, price: float, cfg) -> tuple[bool, str]:
    """Should this position be closed right now? With the reason, either way.

    Guarded to never raise: this is called on every price update, and an
    exception on a hot path that touches open positions is worse than the rule
    not firing.
    """
    try:
        rule = GiveBackRule.from_config(cfg)
        if not rule.enabled:
            return False, "give-back rule is off"
        now = r_of(position, price)
        if now is None:
            return False, "no usable original stop - cannot measure R"
        peak = peak_r(position, price)
        return rule.should_close(peak, now), rule.explain(peak, now)
    except Exception as exc:                            # noqa: BLE001
        return False, f"give-back check failed: {type(exc).__name__}: {exc}"
