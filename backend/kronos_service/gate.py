"""
backend/kronos_service/gate.py

The optional Kronos filter on a strategy's signal: applied AFTER the strategy
has decided and BEFORE the risk engine. It can only say "drop this setup" - it
never opens a trade, never moves a stop or target, never sizes anything, and
the risk engine and prop-firm checks still have the final word on whatever it
lets through.

One function, `decide`, used by the backtest and (Phase 2) by live, so the two
cannot disagree about what the gate does.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

FAIL_OPEN, FAIL_CLOSED = "open", "closed"


@dataclass(frozen=True)
class GateConfig:
    enabled: bool = False
    # BUY needs p_up >= min_agreement; SELL needs (1 - p_up) >= min_agreement.
    min_agreement: float = 0.6
    # Optional: refuse when the paths disagree too much (in ATRs).
    max_dispersion_atr: float | None = None
    # Optional: the mean path must point the trade's way by at least this (ATRs).
    min_exp_return_atr: float | None = None
    # Missing or stale features: "open" lets the signal through, "closed" drops it.
    fail_mode: str = FAIL_OPEN

    def __post_init__(self) -> None:
        if self.fail_mode not in (FAIL_OPEN, FAIL_CLOSED):
            raise ValueError("fail_mode must be 'open' or 'closed'")

    @classmethod
    def from_dict(cls, d: dict | None) -> "GateConfig":
        d = dict(d or {})
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    reason: str
    features: dict | None = None

    def to_log(self, symbol: str, strategy_id: str, direction: str) -> dict:
        """Every gate decision is logged in this shape (non-negotiable 4)."""
        return {"symbol": symbol, "strategy": strategy_id, "direction": direction,
                **asdict(self)}


def decide(cfg: GateConfig, direction: str, row: dict | None,
           decision_bar_time: int) -> GateDecision:
    """`row` is the feature row for this symbol/timeframe, `decision_bar_time`
    the epoch of the bar the strategy decided on. A row for any other bar is
    STALE - a forecast made from older bars says nothing about this setup."""
    if not cfg.enabled:
        return GateDecision(True, "gate disabled")
    fresh = row is not None and int(row.get("asof", -1)) == int(decision_bar_time)
    if not fresh or row.get("p_up") is None:
        why = "no Kronos features" if row is None else "Kronos features are stale"
        return GateDecision(cfg.fail_mode == FAIL_OPEN,
                            f"{why} - fail {cfg.fail_mode}", row)
    buy = str(direction).upper() == "BUY"
    agree = float(row["p_up"]) if buy else 1.0 - float(row["p_up"])
    if agree < cfg.min_agreement:
        return GateDecision(False, f"only {agree:.0%} of paths agree "
                                   f"(need {cfg.min_agreement:.0%})", row)
    if cfg.max_dispersion_atr is not None and \
            float(row.get("dispersion_atr", 0.0)) > cfg.max_dispersion_atr:
        return GateDecision(False, f"paths disagree by {row['dispersion_atr']:.2f} ATR "
                                   f"(max {cfg.max_dispersion_atr})", row)
    if cfg.min_exp_return_atr is not None:
        move = float(row.get("exp_return_atr", 0.0)) * (1 if buy else -1)
        if move < cfg.min_exp_return_atr:
            return GateDecision(False, f"expected move {move:+.2f} ATR "
                                       f"(need {cfg.min_exp_return_atr:+.2f})", row)
    return GateDecision(True, f"{agree:.0%} of paths agree", row)
