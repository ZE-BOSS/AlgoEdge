"""
backend/rl/exit_policy.py

The open-position head: what the agent may do to a trade that is already
running, and the precedence that makes it safe.

WHY THIS NEEDS ALMOST NO NEW PLUMBING
-------------------------------------
`BaseStrategy.on_position_bar(symbol, timeframe, candles, position)` is called
once per closed bar for every open position the strategy owns - by
`backtester/engine.py`, by `backtester/portfolio_engine.py` and, through
`LIVE_POSITION_EXITS`, by `services/position_manager.py`. The same hook on all
three sides, which is why backtest and live agree here for free.
`TradeAction` already carries `CLOSE` with `close_pct` and `MODIFY_SL`. So the
exit head is a decision function, not an execution path.

PRECEDENCE, AND WHY IT IS SAFE
------------------------------
1. A circuit-breaker halt flattens, and wins over everything here.
2. The OWNING strategy's own exit wins over a supervisor. A measured rule is
   not overridden by an agent; the agent may only act earlier.
3. `CLOSE` is honoured. It can only reduce exposure, so it cannot breach a
   risk limit - which is why closes need no risk approval while opens do.
4. `TIGHTEN_STOP` is honoured only if it tightens. Never widens.
5. Stop, target, trailing and max-hold are unchanged and still the backstop.

WHY THE AGENT DOES NOT CHOOSE THE STOP LEVEL
--------------------------------------------
The plan's action is `TIGHTEN_STOP(level)`, and a level is a risk distance -
which section 4.3 puts outside the agent's reach. So the agent emits the
INTENT and the amount comes from the position and the config: break-even once
the trade is a full R up, otherwise an ATR distance behind price. The agent can
say "protect this", never "risk this much". A policy that could pick the level
could widen its effective risk by picking a loose one, and no amount of
reviewing the weights would make that visible.

GRANULARITY
-----------
Per closed bar, not per tick. `BarFeed` guarantees backtest/live parity at bar
granularity and nothing guarantees it at tick granularity, so a tick-level exit
would be untestable - and an untestable exit is how a backtest stops predicting
live. Faster reactions come from a lower timeframe for the exit head, which is
a config value and still parity-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from backend.rl import policy as pol
from backend.strategies.base_strategy import TradeAction

# What the Journal shows, so an exit can be attributed afterwards. Without
# this you cannot tell which exits were the agent's.
RL_EXIT = "RL_EXIT"
RL_PARTIAL = "RL_PARTIAL"
RL_TIGHTEN = "RL_TIGHTEN"
SUPERVISOR_PREFIX = "RL_SUPERVISOR::"


def supervisor_reason(agent_id: str, version: int | str = 1) -> str:
    """`RL_SUPERVISOR::<agent>@<version>` - the exit_reason the plan specifies."""
    return f"{SUPERVISOR_PREFIX}{agent_id}@{version}"


@dataclass
class ExitDecision:
    """One bar's answer about one open position."""
    action: str = pol.HOLD
    close_pct: float = 1.0
    new_sl: float | None = None
    confidence: float = 0.0
    reason: str = RL_EXIT
    gates: tuple[str, ...] = ()
    adherence: float = 0.0
    probs: tuple[float, ...] = field(default_factory=tuple)

    @property
    def acts(self) -> bool:
        return self.action != pol.HOLD

    def to_trade_action(self, ticket: int) -> TradeAction | None:
        """Into the existing `TradeAction` - no new action type, as the plan says."""
        if self.action == pol.CLOSE_ALL:
            return TradeAction(ticket=ticket, action="CLOSE", close_pct=1.0,
                               close_reason=self.reason)
        if self.action == pol.CLOSE_PARTIAL:
            return TradeAction(ticket=ticket, action="CLOSE",
                               close_pct=float(min(0.99, max(0.01, self.close_pct))),
                               close_reason=self.reason)
        if self.action == pol.TIGHTEN and self.new_sl is not None:
            return TradeAction(ticket=ticket, action="MODIFY_SL", new_sl=float(self.new_sl),
                               close_reason=self.reason)
        return None

    def to_dict(self) -> dict:
        return {"action": self.action, "close_pct": self.close_pct, "new_sl": self.new_sl,
                "confidence": round(self.confidence, 4), "reason": self.reason,
                "gates": list(self.gates), "adherence": round(self.adherence, 4)}


# -- the tightening rule ----------------------------------------------------


def tightens(new_sl: float, current_sl: float, direction: str) -> bool:
    """Whether moving the stop to `new_sl` reduces risk.

    The one check that makes rule 4 true. A long's stop only ever rises; a
    short's only ever falls. Everything else is a widening dressed up as a
    modification.
    """
    try:
        new, cur = float(new_sl), float(current_sl)
    except (TypeError, ValueError):
        return False
    if not (np.isfinite(new) and np.isfinite(cur)):
        return False
    return new > cur if str(direction).upper() == "BUY" else new < cur


def tighten_level(position: dict, price: float, atr_value: float | None = None,
                  atr_multiple: float = 1.0) -> float | None:
    """Where the stop goes when the agent asks for it to be tightened.

    Chosen from the POSITION and the CONFIG, never by the policy - see the
    module note. Break-even once the trade is a full R up, because a
    break-even stop at a profit is the one tightening that cannot turn a winner
    into a loser; otherwise an ATR distance behind price, which is the same
    trail the classic families already use through `core/trail.py`.

    Returns None when no tightening is available, which is a normal answer and
    reads as HOLD.
    """
    entry = float(position.get("entry_price") or 0.0)
    cur = float(position.get("stop_loss") or 0.0)
    direction = str(position.get("direction", "BUY")).upper()
    risk = abs(entry - cur if cur else 0.0)
    sgn = 1.0 if direction == "BUY" else -1.0
    unrealised_r = (sgn * (float(price) - entry) / risk) if risk > 0 else 0.0

    candidates = []
    if unrealised_r >= 1.0 and entry:
        candidates.append(entry)
    if atr_value and atr_value > 0:
        candidates.append(float(price) - sgn * float(atr_value) * float(atr_multiple))
    best = None
    for c in candidates:
        if not tightens(c, cur, direction):
            continue
        if best is None or tightens(c, best, direction):
            best = c
    return best


# -- the decision -----------------------------------------------------------


def decide(policy: pol.LinearPolicy, z, position: dict, *, price: float,
           bars_held: int = 0, max_hold_bars: int = 288,
           min_confidence: float = 0.55, atr_value: float | None = None,
           atr_multiple: float = 1.0, charter=None, reason: str = RL_EXIT,
           partial_pct: float = 0.5) -> ExitDecision:
    """Ask the exit head about one open position on one closed bar.

    `z` is the SCALED market observation for this bar - the same vector the
    entry head would see. The position half is built here, from the position
    the engine handed us, so no caller can accidentally feed the exit head
    something the entry head could not have seen.
    """
    from backend.rl.features import position_vector

    if policy is None or not getattr(policy, "exit_head", False):
        return ExitDecision(action=pol.HOLD)

    pv = position_vector(position, price=float(price), bars_held=int(bars_held),
                         max_hold_bars=int(max_hold_bars))
    action, conf, probs = policy.act_exit(z, pv, min_confidence=min_confidence)

    gates: tuple[str, ...] = ()
    adherence = 0.0
    if charter is not None:
        # An exit is as explainable as an entry: the charter's gates are
        # evaluated on the same bar, so the Journal can say WHY it closed.
        from backend.rl.adherence import observation_dict, score_decision
        try:
            obs = observation_dict(charter_feature_names(charter, z), z)
            score = score_decision(charter, obs, position.get("direction"))
            gates, adherence = tuple(score.gates_held), score.adherence
        except Exception:
            # A charter that cannot be scored must not stop a close. The exit
            # is a risk reduction; the explanation is secondary to it.
            gates, adherence = (), 0.0

    d = ExitDecision(action=action, confidence=conf, reason=reason, gates=gates,
                     adherence=adherence, probs=tuple(float(p) for p in probs))
    if action == pol.CLOSE_PARTIAL:
        d.close_pct = float(partial_pct)
        d.reason = RL_PARTIAL if reason == RL_EXIT else reason
    elif action == pol.TIGHTEN:
        level = tighten_level(position, float(price), atr_value, atr_multiple)
        if level is None:
            # Nothing to tighten to. HOLD rather than a no-op MODIFY_SL, which
            # would otherwise be logged as an action the agent took.
            return ExitDecision(action=pol.HOLD, confidence=conf, probs=d.probs)
        d.new_sl = level
        d.reason = RL_TIGHTEN if reason == RL_EXIT else reason
    return d


def charter_feature_names(charter, z) -> list[str]:
    """The feature names behind a scaled observation vector.

    A charter stores its gates by feature name and the policy sees an unnamed
    vector, so something has to hold the column order. It lives on the charter's
    validation blob, written at training time; if it is missing the names fall
    back to the default list, and a length mismatch raises in
    `observation_dict` rather than silently scoring the wrong column.
    """
    from backend.rl.features import DEFAULT_FEATURES

    names = ((charter.validation or {}).get("features")
             if getattr(charter, "validation", None) else None)
    return list(names or DEFAULT_FEATURES)[:len(np.asarray(z).ravel())]


# -- supervision ------------------------------------------------------------


def supervise(owner_action: TradeAction | None, supervisor_decision: ExitDecision,
              position: dict, ticket: int) -> TradeAction | None:
    """Combine the owning strategy's answer with a supervisor's (plan 4.9).

    The owner wins. Not "the owner is consulted" - wins. If the owning
    strategy's measured rule says close or move the stop, that is what happens,
    and the supervisor's opinion is discarded for this bar. A supervisor may
    only act where the owner had nothing to say, which bounds the damage to one
    failure mode: cutting a trade early that would have recovered. That is
    measurable, and the ablation in `validate.py` is what measures it.
    """
    if owner_action is not None:
        return owner_action
    if not supervisor_decision.acts:
        return None
    action = supervisor_decision.to_trade_action(ticket)
    if action is None:
        return None
    if action.action == "MODIFY_SL" and action.new_sl is not None:
        if not tightens(action.new_sl, position.get("stop_loss"), position.get("direction", "BUY")):
            # Rule 4. The engines enforce this too; enforcing it here as well
            # means a supervisor cannot even express a widening, which is
            # cheaper to reason about than two layers that must agree.
            return None
    return action
